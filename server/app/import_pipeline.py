from __future__ import annotations

import hashlib
import importlib.util
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from PIL import Image, ImageEnhance, ImageOps
from pymongo.errors import DuplicateKeyError

from app.config import Settings, load_settings
from app.database import get_imports_collection
from app.schemas import ImportPageAsset, ImportRecord, ImportStatus
from app.typhoon import (
    SUPPORTED_EXTENSIONS,
    _clean_image_for_ocr,
    build_page_segments,
    count_pages,
    correct_segments_with_line_ocr,
    join_markdown_pages,
    render_page_preview,
    run_ocr_page,
    validate_extension,
)

IMPORT_OCR_PIPELINE_VERSION = 34
MANUAL_EDIT_REASON = "This page was manually edited after OCR."
CASE_HEADER_CROP_REASON = "First-page case numbers were recovered from a focused header crop."
DEFAULT_DOCUMENT_CATEGORY = "uncategorized"
OCR_QUEUE_STATUSES = {
    ImportStatus.uploaded.value,
    ImportStatus.ocr_queued.value,
    ImportStatus.ocr_failed.value,
}
OCR_BUSY_STATUSES = {
    ImportStatus.cleaning.value,
    ImportStatus.ocr_running.value,
}
CASE_BLACK_MARKERS = ("คดีหมายเลขดำที่", "คดีหมายเลขดำ", "หมายเลขดำที่", "หมายเลขดำ", "คดีดำเลขที่", "คดีดำที่", "คดีดำ")
CASE_RED_MARKERS = ("คดีหมายเลขแดงที่", "คดีหมายเลขแดง", "หมายเลขแดงที่", "หมายเลขแดง", "คดีแดงเลขที่", "คดีแดงที่", "คดีแดง")
CASE_HEADER_MARKERS = CASE_BLACK_MARKERS + CASE_RED_MARKERS


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_import_event(message: str) -> None:
    print(f"[imports] {message}", flush=True)


def _fingerprint_file(file_path: Path) -> str:
    digest = hashlib.sha1()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_document_category(raw_value: str | None) -> str:
    normalized = (raw_value or "").strip().lower().replace(" ", "_")
    compact = "".join(
        character
        for character in normalized
        if character.isalnum() or character in {"_", "-"}
    ).strip("_-")
    return compact or DEFAULT_DOCUMENT_CATEGORY


def _build_import_record(document: dict[str, object]) -> ImportRecord:
    raw_pages = document.get("pages") or []
    assert isinstance(raw_pages, list)
    pages = [
        ImportPageAsset(**page)
        for page in raw_pages
        if isinstance(page, dict)
    ]
    pages.sort(key=lambda page: page.page_number)
    return ImportRecord(
        id=str(document.get("id") or document.get("_id")),
        source_filename=str(document.get("source_filename", "")),
        document_category=str(document.get("document_category") or DEFAULT_DOCUMENT_CATEGORY),
        source_path=str(document.get("source_path", "")),
        cleaned_file_path=str(document.get("cleaned_file_path", "")),
        source_fingerprint=str(document.get("source_fingerprint", "")),
        status=ImportStatus(str(document.get("status", ImportStatus.review_ready.value))),
        total_pages=int(document.get("total_pages", 1)),
        created_at=str(document.get("created_at", "")),
        updated_at=str(document.get("updated_at", "")),
        checked_at=document.get("checked_at"),
        checked_by=document.get("checked_by"),
        note=document.get("note"),
        ocr_markdown=document.get("ocr_markdown"),
        raw_ocr_markdown=document.get("raw_ocr_markdown"),
        corrected_ocr_markdown=document.get("corrected_ocr_markdown"),
        original_ocr_markdown=document.get("original_ocr_markdown"),
        cleaned_ocr_markdown=document.get("cleaned_ocr_markdown"),
        correction_model=document.get("correction_model"),
        ocr_error_message=document.get("ocr_error_message"),
        ocr_completed_at=document.get("ocr_completed_at"),
        pages=pages,
    )


def import_record_needs_ocr(record: ImportRecord) -> bool:
    if record.status.value in OCR_BUSY_STATUSES:
        return False
    if record.status == ImportStatus.checked and record.ocr_markdown and not record.ocr_error_message:
        return False
    if record.status.value in OCR_QUEUE_STATUSES:
        return True
    return bool(record.ocr_error_message or not (record.ocr_markdown or "").strip())


def _document_needs_background_ocr(document: dict[str, object]) -> bool:
    status = str(document.get("status") or "")
    if status in OCR_BUSY_STATUSES:
        return False
    if status == ImportStatus.checked.value and document.get("ocr_markdown") and not document.get("ocr_error_message"):
        return False
    if status in OCR_QUEUE_STATUSES:
        return True
    if document.get("ocr_error_message"):
        return True
    ocr_markdown = document.get("ocr_markdown")
    if not isinstance(ocr_markdown, str) or not ocr_markdown.strip():
        return True
    return _document_pipeline_version(document) < IMPORT_OCR_PIPELINE_VERSION


def _load_park_folder_module():
    module_path = Path(__file__).resolve().parent.parent / "park_folder_flow.py"
    if not module_path.exists():
        return None

    spec = importlib.util.spec_from_file_location("park_folder_flow_runtime", module_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_incoming_file_path(incoming_dir: Path, filename: str) -> Path:
    candidate = incoming_dir / filename
    if not candidate.exists():
        return candidate

    stem = Path(filename).stem or "upload"
    suffix = Path(filename).suffix
    index = 1
    while True:
        candidate = incoming_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _is_incoming_file(file_path: Path, settings: Settings) -> bool:
    try:
        return file_path.resolve().parent == settings.imports_source_dir.resolve()
    except FileNotFoundError:
        return False


def _stage_source_folder_to_park(settings: Settings) -> list[dict[str, str]]:
    park_folder_module = _load_park_folder_module()
    if park_folder_module is None:
        _log_import_event("park folder staging module is not available.")
        return []

    source_dir = getattr(park_folder_module, "DEFAULT_SOURCE_DIR", None)
    if isinstance(source_dir, Path):
        resolved_source_dir = source_dir
    elif isinstance(source_dir, str):
        resolved_source_dir = Path(source_dir)
    else:
        _log_import_event("park folder source directory is not configured.")
        return []

    if not resolved_source_dir.exists():
        _log_import_event(f"source folder does not exist: {resolved_source_dir}")
        return []

    source_candidates = [
        file_path
        for file_path in sorted(resolved_source_dir.iterdir())
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not source_candidates:
        _log_import_event(f"no new files found in source folder: {resolved_source_dir}")
        return []

    _log_import_event(
        f"staging {len(source_candidates)} file(s) from {resolved_source_dir} into {settings.imports_source_dir}"
    )

    results: list[dict[str, str]] = []
    settings.imports_source_dir.mkdir(parents=True, exist_ok=True)
    for source_path in source_candidates:
        staged_path = _build_incoming_file_path(settings.imports_source_dir, source_path.name)
        shutil.copy2(source_path, staged_path)
        source_path.unlink(missing_ok=True)
        results.append(
            {
                "source": str(source_path),
                "incoming": str(staged_path),
                "source_deleted": "true",
            }
        )
        _log_import_event(
            f"staged {source_path.name} -> incoming and removed source file"
        )
    return results


def list_imports(limit: int = 50, *, category: str | None = None) -> list[ImportRecord]:
    category_query: dict[str, object] = {}
    normalized_category: str | None = None
    raw_category = (category or "").strip()
    if raw_category and raw_category.lower() != "all":
        normalized_category = _normalize_document_category(raw_category)

    if normalized_category is None:
        category_query = {}
    elif normalized_category == DEFAULT_DOCUMENT_CATEGORY:
        category_query = {
            "$or": [
                {"document_category": DEFAULT_DOCUMENT_CATEGORY},
                {"document_category": {"$exists": False}},
                {"document_category": None},
                {"document_category": ""},
            ]
        }
    else:
        category_query = {"document_category": normalized_category}

    cursor = get_imports_collection().find(category_query).sort("updated_at", -1).limit(limit)
    return [_build_import_record(document) for document in cursor]


def list_pending_ocr_imports(limit: int = 100) -> list[ImportRecord]:
    collection = get_imports_collection()
    pending_statuses = [
        ImportStatus.uploaded.value,
        ImportStatus.ocr_queued.value,
        ImportStatus.cleaning.value,
        ImportStatus.ocr_running.value,
    ]
    cursor = collection.find({"status": {"$in": pending_statuses}}).sort("updated_at", 1).limit(limit)
    records: list[ImportRecord] = []
    for document in cursor:
        if str(document.get("status") or "") in OCR_BUSY_STATUSES:
            collection.update_one(
                {"_id": document["_id"]},
                {
                    "$set": {
                        "status": ImportStatus.ocr_queued.value,
                        "updated_at": _utc_now(),
                    }
                },
            )
            document = collection.find_one({"_id": document["_id"]}) or document
        records.append(_build_import_record(document))
    return records


def get_import(import_id: str, settings: Settings | None = None, *, refresh_ocr: bool = False) -> ImportRecord | None:
    document = get_imports_collection().find_one({"_id": import_id})
    if document is None:
        return None
    if refresh_ocr:
        document = _refresh_existing_import_if_needed(document, settings or load_settings())
    return _build_import_record(document)


def get_import_preview_path(import_id: str, page_number: int, *, cleaned: bool) -> Path | None:
    document = get_imports_collection().find_one(
        {"_id": import_id},
        {"pages": 1},
    )
    if document is None:
        return None

    raw_pages = document.get("pages") or []
    assert isinstance(raw_pages, list)
    for page in raw_pages:
        if not isinstance(page, dict) or int(page.get("page_number", 0)) != page_number:
            continue
        field_name = "cleaned_preview_path" if cleaned else "original_preview_path"
        value = page.get(field_name)
        if isinstance(value, str):
            path = Path(value)
            if path.exists():
                return path
    return None


def _with_page_ocr_fields(
    page_asset: dict[str, object],
    *,
    markdown: str | None = None,
    raw_markdown: str | None = None,
    corrected_markdown: str | None = None,
    original_markdown: str | None = None,
    cleaned_markdown: str | None = None,
    selected_markdown_source: str | None = None,
    selected_markdown_score: float | None = None,
    original_markdown_score: float | None = None,
    cleaned_markdown_score: float | None = None,
    correction_model: str | None = None,
    correction_error: str | None = None,
    correction_similarity: float | None = None,
    original_ocr_error: str | None = None,
    cleaned_ocr_error: str | None = None,
    diff_similarity: float | None = None,
    suspicious_reasons: list[str] | None = None,
    segments: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    normalized_page = dict(page_asset)
    normalized_page["markdown"] = markdown if markdown is not None else normalized_page.get("markdown")
    normalized_page["raw_markdown"] = (
        raw_markdown if raw_markdown is not None else normalized_page.get("raw_markdown")
    )
    normalized_page["corrected_markdown"] = (
        corrected_markdown if corrected_markdown is not None else normalized_page.get("corrected_markdown")
    )
    normalized_page["original_markdown"] = (
        original_markdown if original_markdown is not None else normalized_page.get("original_markdown")
    )
    normalized_page["cleaned_markdown"] = (
        cleaned_markdown if cleaned_markdown is not None else normalized_page.get("cleaned_markdown")
    )
    normalized_page["selected_markdown_source"] = (
        selected_markdown_source
        if selected_markdown_source is not None
        else normalized_page.get("selected_markdown_source")
    )
    normalized_page["selected_markdown_score"] = (
        selected_markdown_score
        if selected_markdown_score is not None
        else normalized_page.get("selected_markdown_score")
    )
    normalized_page["original_markdown_score"] = (
        original_markdown_score
        if original_markdown_score is not None
        else normalized_page.get("original_markdown_score")
    )
    normalized_page["cleaned_markdown_score"] = (
        cleaned_markdown_score
        if cleaned_markdown_score is not None
        else normalized_page.get("cleaned_markdown_score")
    )
    normalized_page["correction_model"] = (
        correction_model if correction_model is not None else normalized_page.get("correction_model")
    )
    normalized_page["correction_error"] = (
        correction_error if correction_error is not None else normalized_page.get("correction_error")
    )
    normalized_page["correction_similarity"] = (
        correction_similarity
        if correction_similarity is not None
        else normalized_page.get("correction_similarity")
    )
    normalized_page["original_ocr_error"] = (
        original_ocr_error if original_ocr_error is not None else normalized_page.get("original_ocr_error")
    )
    normalized_page["cleaned_ocr_error"] = (
        cleaned_ocr_error if cleaned_ocr_error is not None else normalized_page.get("cleaned_ocr_error")
    )
    normalized_page["diff_similarity"] = (
        diff_similarity if diff_similarity is not None else normalized_page.get("diff_similarity")
    )
    normalized_page["suspicious_reasons"] = (
        suspicious_reasons if suspicious_reasons is not None else list(normalized_page.get("suspicious_reasons") or [])
    )
    normalized_page["segments"] = segments if segments is not None else list(normalized_page.get("segments") or [])
    return normalized_page


def _document_pipeline_version(document: dict[str, object]) -> int:
    raw_value = document.get("ocr_pipeline_version", 0)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 0


def _get_segment_preview_path(page: dict[str, object]) -> Path | None:
    preview_path_value = str(page.get("cleaned_preview_path") or "")
    if preview_path_value:
        preview_path = Path(preview_path_value)
        if preview_path.exists():
            return preview_path
    return None


def _get_segment_source_markdown(page: dict[str, object]) -> str | None:
    selected_source = str(page.get("selected_markdown_source") or "")
    if selected_source == "manual":
        page_markdown = page.get("markdown")
        if isinstance(page_markdown, str):
            return page_markdown

    raw_markdown = page.get("raw_markdown")
    if isinstance(raw_markdown, str):
        return raw_markdown

    page_markdown = page.get("markdown")
    if isinstance(page_markdown, str):
        return page_markdown

    return None


def _strip_ocr_line_decorators(value: str) -> str:
    stripped = value.strip()
    while stripped.startswith("#"):
        stripped = stripped[1:].strip()
    while stripped.startswith(("-", "*", "+", ">")):
        stripped = stripped[1:].strip()
    return stripped


def _clean_case_header_value(value: str) -> str | None:
    cleaned = _strip_ocr_line_decorators(value).strip(" :-–—\t")
    if not cleaned:
        return None
    if not any(character.isdigit() for character in cleaned):
        return None
    return cleaned


def _line_contains_case_marker(line: str, markers: tuple[str, ...]) -> bool:
    compact_line = "".join(line.split())
    compact_markers = ("".join(marker.split()) for marker in markers)
    return any(marker in compact_line for marker in compact_markers)


def _extract_case_header_value(
    lines: list[str],
    markers: tuple[str, ...],
) -> str | None:
    for index, line in enumerate(lines):
        if not _line_contains_case_marker(line, markers):
            continue

        for marker in markers:
            if marker in line:
                value = _clean_case_header_value(line.split(marker, 1)[1])
                if value:
                    return value

        for next_line in lines[index + 1 : index + 4]:
            if not next_line or "สำหรับศาล" in next_line:
                continue
            if _line_contains_case_marker(next_line, CASE_HEADER_MARKERS):
                break
            value = _clean_case_header_value(next_line)
            if value:
                return value

    return None


def _normalize_case_header_ocr(header_markdown: str) -> str | None:
    lines = [
        _strip_ocr_line_decorators(line)
        for line in header_markdown.splitlines()
    ]
    lines = [line for line in lines if line]
    if not lines:
        return None

    black_no = _extract_case_header_value(lines, CASE_BLACK_MARKERS)
    red_no = _extract_case_header_value(lines, CASE_RED_MARKERS)
    if not black_no and not red_no:
        return None

    header_lines = ["## ข้อมูลหัวกระดาษหน้าแรก"]
    if black_no:
        header_lines.append(f"คดีหมายเลขดำที่ {black_no}")
    if red_no:
        header_lines.append(f"คดีหมายเลขแดงที่ {red_no}")
    return "\n".join(header_lines)


def _markdown_has_case_header(markdown: str) -> bool:
    return any(marker in markdown for marker in CASE_HEADER_MARKERS)


def _run_first_page_case_header_ocr(
    page_asset: dict[str, object],
    settings: Settings,
) -> str | None:
    preview_path = _get_segment_preview_path(page_asset)
    if preview_path is None:
        return None

    with Image.open(preview_path) as preview_image:
        image = preview_image.convert("RGB")
        width, height = image.size
        crop_box = (
            int(width * 0.50),
            int(height * 0.03),
            int(width * 0.98),
            int(height * 0.22),
        )
        crop = image.crop(crop_box)
        crop = ImageOps.expand(crop, border=30, fill="white")
        crop = ImageEnhance.Contrast(crop.convert("L")).enhance(1.4).convert("RGB")

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix="-case-header.png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        header_markdown = run_ocr_page(
            temp_path,
            1,
            settings,
            source_is_cleaned=True,
        )
    finally:
        temp_path.unlink(missing_ok=True)

    return _normalize_case_header_ocr(header_markdown)


def _prepend_first_page_case_header_if_needed(
    *,
    page_asset: dict[str, object],
    page_number: int,
    markdown: str,
    settings: Settings,
) -> tuple[str, str | None]:
    if page_number != 1 or _markdown_has_case_header(markdown):
        return markdown, None

    try:
        header_markdown = _run_first_page_case_header_ocr(page_asset, settings)
    except Exception as exc:
        _log_import_event(f"OCR first-page case header crop failed: {exc}")
        return markdown, None

    if not header_markdown:
        return markdown, None

    return f"{header_markdown}\n\n{markdown.strip()}".strip(), CASE_HEADER_CROP_REASON


def _join_page_field_markdown(
    pages: list[dict[str, object]],
    field_name: str,
    *,
    fallback_field: str | None = None,
) -> str | None:
    chunks: list[str] = []
    has_content = False
    for page in sorted(pages, key=lambda value: int(value.get("page_number", 0))):
        value = page.get(field_name)
        text = value if isinstance(value, str) else ""
        if not text.strip() and fallback_field is not None:
            fallback_value = page.get(fallback_field)
            if isinstance(fallback_value, str):
                text = fallback_value
        chunks.append(text)
        has_content = has_content or bool(text.strip())

    if not has_content:
        return None
    return join_markdown_pages(chunks)


def _process_page_segments_and_corrections(
    *,
    page_asset: dict[str, object],
    source_markdown: str,
    settings: Settings,
    enable_line_correction: bool,
) -> dict[str, object]:
    page_number = int(page_asset.get("page_number", 0))
    preview_path = _get_segment_preview_path(page_asset)
    if preview_path is None:
        raise FileNotFoundError(f"Preview not found for page {page_number}.")

    with Image.open(preview_path) as preview_image:
        preview_for_segments = preview_image.convert("RGB")
        segments = build_page_segments(preview_for_segments, source_markdown, page_number)
        if enable_line_correction:
            correction = correct_segments_with_line_ocr(
                page_image=preview_for_segments,
                raw_markdown=source_markdown,
                segments=segments,
                settings=settings,
            )
        else:
            correction = {
                "segments": segments,
                "corrected_markdown": None,
                "correction_model": None,
                "correction_error": None,
                "correction_similarity": None,
                "reviewed_line_count": 0,
                "corrected_line_count": 0,
            }

    corrected_markdown = (
        str(correction["corrected_markdown"])
        if isinstance(correction.get("corrected_markdown"), str)
        else None
    )
    correction_model = (
        str(correction["correction_model"])
        if isinstance(correction.get("correction_model"), str)
        else None
    )
    correction_error = (
        str(correction["correction_error"])
        if isinstance(correction.get("correction_error"), str)
        else None
    )
    correction_similarity = (
        float(correction["correction_similarity"])
        if isinstance(correction.get("correction_similarity"), (int, float))
        else None
    )
    reviewed_line_count = (
        int(correction["reviewed_line_count"])
        if isinstance(correction.get("reviewed_line_count"), int)
        else 0
    )
    corrected_line_count = (
        int(correction["corrected_line_count"])
        if isinstance(correction.get("corrected_line_count"), int)
        else 0
    )
    corrected_segments = correction.get("segments")
    normalized_segments = (
        corrected_segments
        if isinstance(corrected_segments, list)
        else segments
    )

    return {
        "segments": normalized_segments,
        "corrected_markdown": corrected_markdown,
        "correction_model": correction_model,
        "correction_error": correction_error,
        "correction_similarity": correction_similarity,
        "reviewed_line_count": reviewed_line_count,
        "corrected_line_count": corrected_line_count,
    }


def _import_needs_ocr(document: dict[str, object]) -> bool:
    if document.get("ocr_error_message"):
        return True

    ocr_markdown = document.get("ocr_markdown")
    if not isinstance(ocr_markdown, str) or not ocr_markdown.strip():
        return True

    raw_pages = document.get("pages") or []
    if not isinstance(raw_pages, list) or not raw_pages:
        return True

    for page in raw_pages:
        if not isinstance(page, dict):
            return True
        if "markdown" not in page or not isinstance(page.get("markdown"), str):
            return True
        if "segments" not in page or not isinstance(page.get("segments"), list):
            return True

    return False


def _can_rebuild_segments_from_cached_ocr(document: dict[str, object]) -> bool:
    if document.get("ocr_error_message"):
        return False

    raw_pages = document.get("pages") or []
    if not isinstance(raw_pages, list) or not raw_pages:
        return False

    for page in raw_pages:
        if not isinstance(page, dict):
            return False
        if _get_segment_preview_path(page) is None:
            return False
        if _get_segment_source_markdown(page) is None:
            return False

    return True


def _rebuild_import_segments_from_cached_ocr(document: dict[str, object]) -> dict[str, object]:
    raw_pages = document.get("pages") or []
    assert isinstance(raw_pages, list)
    settings = load_settings()

    rebuilt_pages: list[dict[str, object]] = []
    for page in raw_pages:
        if not isinstance(page, dict):
            continue

        page_number = int(page.get("page_number", 0))
        source_markdown = _get_segment_source_markdown(page) or ""
        rebuilt_page = dict(page)
        if str(page.get("selected_markdown_source") or "") == "manual":
            line_result = _process_page_segments_and_corrections(
                page_asset=page,
                source_markdown=source_markdown,
                settings=settings,
                enable_line_correction=False,
            )
            rebuilt_page["segments"] = line_result["segments"]
            rebuilt_page["corrected_markdown"] = None
            rebuilt_page["correction_model"] = None
            rebuilt_page["correction_error"] = None
            rebuilt_page["correction_similarity"] = None
        else:
            line_result = _process_page_segments_and_corrections(
                page_asset=page,
                source_markdown=source_markdown,
                settings=settings,
                enable_line_correction=False,
            )
            corrected_markdown = (
                str(line_result["corrected_markdown"])
                if isinstance(line_result.get("corrected_markdown"), str)
                else None
            )
            rebuilt_page["segments"] = line_result["segments"]
            rebuilt_page["corrected_markdown"] = corrected_markdown
            rebuilt_page["markdown"] = corrected_markdown or source_markdown
            rebuilt_page["correction_model"] = line_result.get("correction_model")
            rebuilt_page["correction_error"] = line_result.get("correction_error")
            rebuilt_page["correction_similarity"] = line_result.get("correction_similarity")
        rebuilt_pages.append(rebuilt_page)

    updated_at = _utc_now()
    corrected_document_markdown = _join_page_field_markdown(
        rebuilt_pages,
        "corrected_markdown",
        fallback_field="raw_markdown",
    )
    get_imports_collection().update_one(
        {"_id": document["_id"]},
        {
            "$set": {
                "pages": rebuilt_pages,
                "ocr_markdown": _join_page_field_markdown(rebuilt_pages, "markdown"),
                "raw_ocr_markdown": _join_page_field_markdown(rebuilt_pages, "raw_markdown"),
                "corrected_ocr_markdown": corrected_document_markdown,
                "original_ocr_markdown": _join_page_field_markdown(rebuilt_pages, "original_markdown"),
                "cleaned_ocr_markdown": _join_page_field_markdown(rebuilt_pages, "cleaned_markdown"),
                "correction_model": (
                    next(
                        (
                            str(page.get("correction_model"))
                            for page in rebuilt_pages
                            if isinstance(page.get("correction_model"), str)
                            and str(page.get("correction_model")).strip()
                        ),
                        None,
                    )
                ),
                "updated_at": updated_at,
                "ocr_pipeline_version": IMPORT_OCR_PIPELINE_VERSION,
            }
        },
    )

    updated = get_imports_collection().find_one({"_id": document["_id"]})
    return updated or document


def _generate_import_ocr_payload(
    *,
    source_file_path: Path,
    cleaned_file_path: Path,
    page_assets: list[dict[str, object]],
    settings: Settings,
) -> dict[str, object]:
    normalized_pages = [_with_page_ocr_fields(page_asset) for page_asset in page_assets]
    total_pages = len(normalized_pages)
    if not settings.ocr_ready:
        _log_import_event(
            f"OCR skipped for {source_file_path.name}: OCR endpoint is not configured."
        )
        return {
            "pages": normalized_pages,
            "ocr_markdown": None,
            "raw_ocr_markdown": None,
            "corrected_ocr_markdown": None,
            "original_ocr_markdown": None,
            "cleaned_ocr_markdown": None,
            "correction_model": None,
            "ocr_error_message": None,
            "ocr_completed_at": None,
            "ocr_pipeline_version": IMPORT_OCR_PIPELINE_VERSION,
        }

    if not source_file_path.exists():
        _log_import_event(f"OCR aborted for {source_file_path.name}: source file not found.")
        return {
            "pages": normalized_pages,
            "ocr_markdown": None,
            "raw_ocr_markdown": None,
            "corrected_ocr_markdown": None,
            "original_ocr_markdown": None,
            "cleaned_ocr_markdown": None,
            "correction_model": None,
            "ocr_error_message": f"Source file not found: {source_file_path}",
            "ocr_completed_at": None,
            "ocr_pipeline_version": IMPORT_OCR_PIPELINE_VERSION,
        }

    if not cleaned_file_path.exists():
        _log_import_event(f"OCR aborted for {source_file_path.name}: cleaned file not found.")
        return {
            "pages": normalized_pages,
            "ocr_markdown": None,
            "raw_ocr_markdown": None,
            "corrected_ocr_markdown": None,
            "original_ocr_markdown": None,
            "cleaned_ocr_markdown": None,
            "correction_model": None,
            "ocr_error_message": f"Cleaned file not found: {cleaned_file_path}",
            "ocr_completed_at": None,
            "ocr_pipeline_version": IMPORT_OCR_PIPELINE_VERSION,
        }

    page_markdowns: list[str] = []
    raw_page_markdowns: list[str] = []
    corrected_page_markdowns: list[str] = []
    original_page_markdowns: list[str] = []
    cleaned_page_markdowns: list[str] = []
    enriched_pages: list[dict[str, object]] = []
    correction_model_name: str | None = settings.ocr_model if settings.ocr_ready else None
    failed_page_messages: list[str] = []
    _log_import_event(f"OCR start: {source_file_path.name} ({total_pages} page(s))")
    for page_asset in normalized_pages:
        page_number = int(page_asset.get("page_number", 0))
        _log_import_event(
            f"OCR {source_file_path.name}: page {page_number}/{total_pages} running"
        )
        page_start = perf_counter()
        cleaned_page_path = Path(str(page_asset.get("cleaned_preview_path") or cleaned_file_path))
        cleaned_ocr_path = cleaned_page_path if cleaned_page_path.exists() else cleaned_file_path
        cleaned_ocr_page_number = (
            page_number
            if cleaned_ocr_path.suffix.lower() == ".pdf"
            else 1
        )
        page_failed = False
        try:
            cleaned_markdown = run_ocr_page(
                cleaned_ocr_path,
                cleaned_ocr_page_number,
                settings,
                source_is_cleaned=True,
            )
            ocr_seconds = perf_counter() - page_start
            selected_source = "cleaned"
            raw_markdown = cleaned_markdown
            selected_score = None
            original_markdown = None
            original_score = None
            cleaned_score = None
            original_error = None
            cleaned_error = None
            diff_similarity = None
            suspicious_reasons: list[str] = []
            raw_markdown, header_crop_reason = _prepend_first_page_case_header_if_needed(
                page_asset=page_asset,
                page_number=page_number,
                markdown=raw_markdown,
                settings=settings,
            )
            if header_crop_reason:
                cleaned_markdown = raw_markdown
                suspicious_reasons.append(header_crop_reason)
        except Exception as exc:
            page_failed = True
            ocr_seconds = perf_counter() - page_start
            page_error = f"Page {page_number} OCR failed: {exc}"
            failed_page_messages.append(page_error)
            _log_import_event(
                f"OCR {source_file_path.name}: page {page_number}/{total_pages} failed "
                + f"(ocr_seconds={ocr_seconds:.1f}, error={exc})"
            )
            raw_markdown = f"[OCR failed on page {page_number}: {exc}]"
            cleaned_markdown = ""
            corrected_markdown = None
            markdown = raw_markdown
            segments = []
            correction_error = None
            correction_similarity = None
            correction_model = None
            original_markdown = None
            original_score = None
            cleaned_score = None
            selected_source = "cleaned"
            selected_score = None
            original_error = None
            cleaned_error = str(exc)
            diff_similarity = None
            suspicious_reasons = [
                page_error,
                "This page failed OCR, but other pages were preserved for review.",
            ]
            reviewed_line_count = 0
            corrected_line_count = 0

        page_asset_for_processing = _with_page_ocr_fields(page_asset, selected_markdown_source=selected_source)
        if not page_failed:
            line_result = _process_page_segments_and_corrections(
                page_asset=page_asset_for_processing,
                source_markdown=raw_markdown,
                settings=settings,
                enable_line_correction=False,
            )
            corrected_markdown = (
                str(line_result["corrected_markdown"])
                if isinstance(line_result.get("corrected_markdown"), str)
                else None
            )
            markdown = corrected_markdown or raw_markdown
            segments = line_result["segments"] if isinstance(line_result.get("segments"), list) else []
            correction_error = (
                str(line_result["correction_error"])
                if isinstance(line_result.get("correction_error"), str)
                else None
            )
            correction_similarity = (
                float(line_result["correction_similarity"])
                if isinstance(line_result.get("correction_similarity"), (int, float))
                else None
            )
            correction_model = (
                str(line_result["correction_model"])
                if isinstance(line_result.get("correction_model"), str)
                else None
            )
            reviewed_line_count = (
                int(line_result["reviewed_line_count"])
                if isinstance(line_result.get("reviewed_line_count"), int)
                else 0
            )
            corrected_line_count = (
                int(line_result["corrected_line_count"])
                if isinstance(line_result.get("corrected_line_count"), int)
                else 0
            )
        if corrected_markdown:
            if correction_similarity is not None and correction_similarity < 0.9:
                suspicious_reasons.append(
                    "Typhoon OCR line reread changed this page noticeably, so compare it with the preview before checking."
                )
            if corrected_line_count > 0:
                suspicious_reasons.append(
                    f"Typhoon OCR re-read {corrected_line_count} suspicious line(s) from image crops on this page."
                )
        elif correction_error:
            suspicious_reasons.append(
                "Typhoon OCR line reread could not be applied to this page, so the raw OCR text is being shown."
            )
        elif reviewed_line_count > 0:
            suspicious_reasons.append(
                f"Typhoon OCR reviewed {reviewed_line_count} suspicious line crop(s) and kept the raw OCR wording where no safer fix was found."
            )
        _log_import_event(
            "OCR "
            + f"{source_file_path.name}: page {page_number}/{total_pages} done "
            + (
                f"(selected_source={selected_source}, corrected={'yes' if corrected_markdown else 'no'}, "
                + f"ocr_seconds={ocr_seconds:.1f}, "
                + f"reviewed_lines={reviewed_line_count}, corrected_lines={corrected_line_count}, "
                + f"blocks={len(segments)})"
            )
        )
        page_markdowns.append(markdown)
        raw_page_markdowns.append(raw_markdown)
        corrected_page_markdowns.append(corrected_markdown or "")
        original_page_markdowns.append(original_markdown or "")
        cleaned_page_markdowns.append(cleaned_markdown or "")
        enriched_pages.append(
            _with_page_ocr_fields(
                page_asset_for_processing,
                markdown=markdown,
                raw_markdown=raw_markdown,
                corrected_markdown=corrected_markdown,
                original_markdown=original_markdown or None,
                cleaned_markdown=cleaned_markdown or None,
                selected_markdown_source=selected_source,
                selected_markdown_score=selected_score,
                original_markdown_score=original_score,
                cleaned_markdown_score=cleaned_score,
                correction_model=correction_model,
                correction_error=correction_error,
                correction_similarity=correction_similarity,
                original_ocr_error=original_error,
                cleaned_ocr_error=cleaned_error,
                diff_similarity=diff_similarity,
                suspicious_reasons=suspicious_reasons,
                segments=segments,
            )
        )

    if failed_page_messages:
        _log_import_event(
            f"OCR completed with page errors: {source_file_path.name} ({'; '.join(failed_page_messages)})"
        )
    else:
        _log_import_event(f"OCR completed: {source_file_path.name}")
    return {
        "pages": enriched_pages,
        "ocr_markdown": join_markdown_pages(page_markdowns),
        "raw_ocr_markdown": join_markdown_pages(raw_page_markdowns),
        "corrected_ocr_markdown": (
            join_markdown_pages(
                [
                    corrected_markdown if corrected_markdown.strip() else raw_markdown
                    for corrected_markdown, raw_markdown in zip(
                        corrected_page_markdowns,
                        raw_page_markdowns,
                        strict=True,
                    )
                ]
            )
            if raw_page_markdowns
            else None
        ),
        "original_ocr_markdown": (
            join_markdown_pages(original_page_markdowns)
            if any(markdown.strip() for markdown in original_page_markdowns)
            else None
        ),
        "cleaned_ocr_markdown": (
            join_markdown_pages(cleaned_page_markdowns)
            if any(markdown.strip() for markdown in cleaned_page_markdowns)
            else None
        ),
        "correction_model": correction_model_name,
        "ocr_error_message": None,
        "ocr_completed_at": _utc_now(),
        "ocr_pipeline_version": IMPORT_OCR_PIPELINE_VERSION,
    }


def _refresh_existing_import_if_needed(
    document: dict[str, object],
    settings: Settings,
) -> dict[str, object]:
    needs_ocr = _import_needs_ocr(document)
    pipeline_version = _document_pipeline_version(document)

    if not needs_ocr and pipeline_version >= IMPORT_OCR_PIPELINE_VERSION:
        _log_import_event(
            f"reusing cached OCR for {document.get('source_filename', 'unknown file')}"
        )
        return document

    if needs_ocr and _can_rebuild_segments_from_cached_ocr(document):
        _log_import_event(
            f"rebuilding cached review blocks for {document.get('source_filename', 'unknown file')}"
        )
        return _rebuild_import_segments_from_cached_ocr(document)

    cleaned_file_path = Path(str(document.get("cleaned_file_path", "")))
    source_file_path = Path(str(document.get("source_path", "")))
    raw_pages = document.get("pages") or []
    assert isinstance(raw_pages, list)
    ocr_payload = _generate_import_ocr_payload(
        source_file_path=source_file_path,
        cleaned_file_path=cleaned_file_path,
        page_assets=[page for page in raw_pages if isinstance(page, dict)],
        settings=settings,
    )

    updated_at = _utc_now()
    _log_import_event(
        f"refreshing OCR for existing record {document.get('source_filename', 'unknown file')}"
    )
    get_imports_collection().update_one(
        {"_id": document["_id"]},
        {
            "$set": {
                **ocr_payload,
                "updated_at": updated_at,
            }
        },
    )

    updated = get_imports_collection().find_one({"_id": document["_id"]})
    return updated or document


def mark_import_checked(
    import_id: str,
    *,
    checked_by: str | None = None,
    note: str | None = None,
) -> ImportRecord | None:
    collection = get_imports_collection()
    document = collection.find_one({"_id": import_id})
    if document is None:
        return None

    now = _utc_now()
    normalized_checked_by = (checked_by or "").strip() or None
    normalized_note = (note or "").strip() or None

    collection.update_one(
        {"_id": import_id},
        {
            "$set": {
                "status": ImportStatus.checked.value,
                "updated_at": now,
                "checked_at": now,
                "checked_by": normalized_checked_by,
                "note": normalized_note,
            }
        },
    )
    _log_import_event(
        f"checked document {document.get('source_filename', import_id)}"
        + (f" by {normalized_checked_by}" if normalized_checked_by else "")
    )

    updated = collection.find_one({"_id": import_id})
    if updated is None:
        return None
    return _build_import_record(updated)


def save_import_page_markdown(
    import_id: str,
    *,
    page_number: int,
    markdown: str,
) -> ImportRecord | None:
    collection = get_imports_collection()
    document = collection.find_one({"_id": import_id})
    if document is None:
        return None

    raw_pages = document.get("pages") or []
    if not isinstance(raw_pages, list):
        return None

    normalized_markdown = markdown.strip()
    updated_pages: list[dict[str, object]] = []
    target_found = False

    for page in raw_pages:
        if not isinstance(page, dict):
            continue

        current_page_number = int(page.get("page_number", 0))
        updated_page = dict(page)
        if current_page_number == page_number:
            preview_path = _get_segment_preview_path(page)
            if preview_path is not None:
                with Image.open(preview_path) as preview_image:
                    preview_for_segments = preview_image.convert("RGB")
                segments = build_page_segments(preview_for_segments, normalized_markdown, page_number)
            else:
                segments = []

            reasons = [
                str(reason)
                for reason in page.get("suspicious_reasons", [])
                if isinstance(reason, str) and reason != MANUAL_EDIT_REASON
            ]
            reasons.append(MANUAL_EDIT_REASON)

            updated_page["markdown"] = normalized_markdown
            updated_page["corrected_markdown"] = None
            updated_page["selected_markdown_source"] = "manual"
            updated_page["selected_markdown_score"] = None
            updated_page["correction_model"] = None
            updated_page["correction_error"] = None
            updated_page["correction_similarity"] = None
            updated_page["segments"] = segments
            updated_page["suspicious_reasons"] = reasons
            target_found = True

        updated_pages.append(updated_page)

    if not target_found:
        return None

    sorted_pages = sorted(updated_pages, key=lambda page: int(page.get("page_number", 0)))
    collection.update_one(
        {"_id": import_id},
        {
            "$set": {
                "pages": sorted_pages,
                "ocr_markdown": _join_page_field_markdown(sorted_pages, "markdown"),
                "corrected_ocr_markdown": _join_page_field_markdown(
                    sorted_pages,
                    "corrected_markdown",
                    fallback_field="raw_markdown",
                ),
                "updated_at": _utc_now(),
            }
        },
    )
    _log_import_event(
        f"saved manual OCR edit for {document.get('source_filename', import_id)} page {page_number}"
    )

    updated = collection.find_one({"_id": import_id})
    if updated is None:
        return None
    return _build_import_record(updated)


def _save_cleaned_pdf(cleaned_images: list[Image.Image], output_path: Path) -> None:
    if not cleaned_images:
        raise ValueError("No cleaned pages were generated for the PDF.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(cleaned_images) == 1:
        cleaned_images[0].save(output_path, "PDF", resolution=150.0)
        return

    cleaned_images[0].save(
        output_path,
        "PDF",
        save_all=True,
        append_images=cleaned_images[1:],
        resolution=150.0,
    )


def _store_original_source_file(
    source_path: Path,
    *,
    target_path: Path,
    settings: Settings,
) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() == target_path.resolve():
        return target_path

    if _is_incoming_file(source_path, settings):
        shutil.move(str(source_path), str(target_path))
    else:
        shutil.copy2(source_path, target_path)
    return target_path


def _cleanup_incoming_source_file(file_path: Path, settings: Settings) -> None:
    if not _is_incoming_file(file_path, settings):
        return
    file_path.unlink(missing_ok=True)


def _empty_ocr_payload() -> dict[str, object]:
    return {
        "pages": [],
        "ocr_markdown": None,
        "raw_ocr_markdown": None,
        "corrected_ocr_markdown": None,
        "original_ocr_markdown": None,
        "cleaned_ocr_markdown": None,
        "correction_model": None,
        "ocr_error_message": None,
        "ocr_completed_at": None,
        "ocr_pipeline_version": 0,
    }


def _build_cleaned_assets(
    stored_original_path: Path,
    derived_dir: Path,
    total_pages: int,
) -> tuple[Path, list[dict[str, object]]]:
    page_assets: list[dict[str, object]] = []
    cleaned_images: list[Image.Image] = []

    for page_number in range(1, total_pages + 1):
        original_preview = render_page_preview(stored_original_path, page_number)
        cleaned_preview = _clean_image_for_ocr(original_preview)

        original_preview_path = derived_dir / f"page-{page_number:03d}-original.png"
        cleaned_preview_path = derived_dir / f"page-{page_number:03d}-cleaned.png"
        original_preview.save(original_preview_path, format="PNG")
        cleaned_preview.save(cleaned_preview_path, format="PNG")

        page_assets.append(
            {
                "page_number": page_number,
                "original_preview_path": str(original_preview_path),
                "cleaned_preview_path": str(cleaned_preview_path),
            }
        )
        cleaned_images.append(cleaned_preview.copy())

    if stored_original_path.suffix.lower() == ".pdf":
        cleaned_file_path = derived_dir / "cleaned.pdf"
        _save_cleaned_pdf(cleaned_images, cleaned_file_path)
    else:
        cleaned_file_path = derived_dir / "cleaned.png"
        cleaned_images[0].save(cleaned_file_path, format="PNG")

    return cleaned_file_path, page_assets


def _process_source_file(
    file_path: Path,
    settings: Settings,
    *,
    source_filename: str | None = None,
    document_category: str | None = None,
) -> ImportRecord:
    validate_extension(file_path)
    display_filename = source_filename or file_path.name
    normalized_document_category = _normalize_document_category(document_category)
    _log_import_event(f"processing file {display_filename}")

    fingerprint = _fingerprint_file(file_path)
    collection = get_imports_collection()
    existing = collection.find_one({"source_fingerprint": fingerprint})
    if existing is not None:
        now = _utc_now()
        update_fields: dict[str, object] = {"updated_at": now}
        if _normalize_document_category(str(existing.get("document_category") or "")) != normalized_document_category:
            update_fields["document_category"] = normalized_document_category

        if _document_needs_background_ocr(existing):
            update_fields.update(
                {
                    "status": ImportStatus.ocr_queued.value,
                    "ocr_error_message": None,
                    "ocr_completed_at": None,
                }
            )

        collection.update_one({"_id": existing["_id"]}, {"$set": update_fields})
        existing = collection.find_one({"_id": existing["_id"]}) or existing
        _log_import_event(
            f"{display_filename} matched existing fingerprint in MongoDB"
        )
        _cleanup_incoming_source_file(file_path, settings)
        return _build_import_record(existing)

    import_id = uuid4().hex
    original_dir = settings.imports_original_dir / import_id
    derived_dir = settings.imports_derived_dir / import_id
    original_dir.mkdir(parents=True, exist_ok=True)
    derived_dir.mkdir(parents=True, exist_ok=True)

    original_extension = file_path.suffix.lower() or ".pdf"
    stored_original_path = _store_original_source_file(
        file_path,
        target_path=original_dir / f"source{original_extension}",
        settings=settings,
    )

    total_pages = count_pages(stored_original_path)
    _log_import_event(f"{display_filename}: detected {total_pages} page(s)")
    cleaned_file_path = derived_dir / (
        "cleaned.pdf" if stored_original_path.suffix.lower() == ".pdf" else "cleaned.png"
    )

    now = _utc_now()
    document = {
        "_id": import_id,
        "id": import_id,
        "source_filename": display_filename,
        "document_category": normalized_document_category,
        "source_path": str(stored_original_path),
        "cleaned_file_path": str(cleaned_file_path),
        "source_fingerprint": fingerprint,
        "status": ImportStatus.ocr_queued.value,
        "total_pages": total_pages,
        "created_at": now,
        "updated_at": now,
        "checked_at": None,
        "checked_by": None,
        "note": None,
        **_empty_ocr_payload(),
    }
    try:
        collection.insert_one(document)
    except DuplicateKeyError:
        _log_import_event(
            f"{display_filename} hit a duplicate fingerprint while saving; reusing the existing MongoDB record"
        )
        existing_after_insert = collection.find_one({"source_fingerprint": fingerprint})
        if existing_after_insert is None:
            raise
        if _document_needs_background_ocr(existing_after_insert):
            collection.update_one(
                {"_id": existing_after_insert["_id"]},
                {
                    "$set": {
                        "status": ImportStatus.ocr_queued.value,
                        "updated_at": _utc_now(),
                    }
                },
            )
            existing_after_insert = (
                collection.find_one({"_id": existing_after_insert["_id"]})
                or existing_after_insert
            )
        return _build_import_record(existing_after_insert)
    _log_import_event(
        f"saved queued OCR record for {display_filename} with import_id={import_id}"
    )
    return _build_import_record(document)


def create_import_from_uploaded_file(
    staged_file_path: Path,
    *,
    source_filename: str,
    document_category: str | None = None,
    settings: Settings | None = None,
) -> ImportRecord:
    active_settings = settings or load_settings()
    return _process_source_file(
        staged_file_path,
        active_settings,
        source_filename=source_filename,
        document_category=document_category,
    )


def process_import_ocr(import_id: str, settings: Settings | None = None) -> ImportRecord | None:
    active_settings = settings or load_settings()
    collection = get_imports_collection()
    document = collection.find_one({"_id": import_id})
    if document is None:
        _log_import_event(f"OCR skipped: import not found ({import_id})")
        return None

    if not _document_needs_background_ocr(document):
        _log_import_event(
            f"OCR skipped for {document.get('source_filename', import_id)}: cached result is current"
        )
        return _build_import_record(document)

    source_file_path = Path(str(document.get("source_path", "")))
    derived_dir = active_settings.imports_derived_dir / import_id
    now = _utc_now()
    collection.update_one(
        {"_id": import_id},
        {
            "$set": {
                "status": ImportStatus.cleaning.value,
                "updated_at": now,
                "ocr_error_message": None,
                "ocr_completed_at": None,
            }
        },
    )

    try:
        total_pages = count_pages(source_file_path)
        derived_dir.mkdir(parents=True, exist_ok=True)
        cleaned_file_path, page_assets = _build_cleaned_assets(
            source_file_path,
            derived_dir,
            total_pages,
        )
    except Exception as exc:
        _log_import_event(f"cleaning failed for {document.get('source_filename', import_id)}: {exc}")
        collection.update_one(
            {"_id": import_id},
            {
                "$set": {
                    "status": ImportStatus.ocr_failed.value,
                    "updated_at": _utc_now(),
                    "ocr_error_message": str(exc),
                    "ocr_completed_at": None,
                }
            },
        )
        failed = collection.find_one({"_id": import_id})
        return _build_import_record(failed) if failed is not None else None

    collection.update_one(
        {"_id": import_id},
        {
            "$set": {
                "status": ImportStatus.ocr_running.value,
                "updated_at": _utc_now(),
                "cleaned_file_path": str(cleaned_file_path),
                "total_pages": total_pages,
                "pages": [_with_page_ocr_fields(page_asset) for page_asset in page_assets],
            }
        },
    )

    ocr_payload = _generate_import_ocr_payload(
        source_file_path=source_file_path,
        cleaned_file_path=cleaned_file_path,
        page_assets=page_assets,
        settings=active_settings,
    )
    next_status = (
        ImportStatus.ocr_failed.value
        if ocr_payload.get("ocr_error_message")
        else ImportStatus.ready_for_review.value
    )
    collection.update_one(
        {"_id": import_id},
        {
            "$set": {
                **ocr_payload,
                "status": next_status,
                "updated_at": _utc_now(),
                "cleaned_file_path": str(cleaned_file_path),
                "total_pages": total_pages,
            }
        },
    )
    updated = collection.find_one({"_id": import_id})
    return _build_import_record(updated) if updated is not None else None


def scan_source_folder(settings: Settings | None = None) -> list[ImportRecord]:
    active_settings = settings or load_settings()
    _log_import_event(f"scan started for {active_settings.imports_source_dir}")
    staged_results = _stage_source_folder_to_park(active_settings)
    if staged_results:
        _log_import_event(f"staging completed: {len(staged_results)} file(s)")
    records: list[ImportRecord] = []
    source_files = [
        file_path
        for file_path in sorted(active_settings.imports_source_dir.iterdir())
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not source_files:
        _log_import_event(
            f"scan completed: no files found in {active_settings.imports_source_dir}"
        )
        return records

    _log_import_event(
        f"found {len(source_files)} file(s) ready in {active_settings.imports_source_dir}"
    )
    for index, file_path in enumerate(source_files, start=1):
        _log_import_event(f"queue item {index}/{len(source_files)}: {file_path.name}")
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        records.append(_process_source_file(file_path, active_settings))
    _log_import_event(f"scan completed: {len(records)} record(s) available")
    return records
