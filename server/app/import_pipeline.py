from __future__ import annotations

import hashlib
import re
import shutil
import socket
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse
from uuid import uuid4

import fitz
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pymongo.errors import DuplicateKeyError

from app.anchor_provider import AnchorLine, detect_anchor_lines
from app.core.config import Settings, load_settings
from app.core.database import get_imports_collection
from app.schemas import ImportPageAsset, ImportRecord, ImportStatus
from app.typhoon import (
    SUPPORTED_EXTENSIONS,
    build_page_segments,
    compare_ocr_page_sources,
    count_pages,
    correct_segments_with_line_ocr,
    join_markdown_pages,
    render_page_preview,
    run_vision_json,
    run_ocr_page,
    validate_extension,
)
from app.tr_review import build_tr_review_data
from app.tr_watermark_cleaner import build_tr_cleaned_image, is_tr_document_category
from app.watermark_cleaner import DEFAULT_RENDER_DPI, clean_page_to_image, clean_pil_image, clean_pil_image_soft

IMPORT_OCR_PIPELINE_VERSION = 71
TR_IMPORT_OCR_PIPELINE_VERSION = 77
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
ASCII_TO_THAI_DIGITS = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")
CASE_BLACK_MARKERS = ("คดีหมายเลขดำที่", "คดีหมายเลขดำ", "หมายเลขดำที่", "หมายเลขดำ", "คดีดำเลขที่", "คดีดำที่", "คดีดำ")
CASE_RED_MARKERS = ("คดีหมายเลขแดงที่", "คดีหมายเลขแดง", "หมายเลขแดงที่", "หมายเลขแดง", "คดีแดงเลขที่", "คดีแดงที่", "คดีแดง")
CASE_HEADER_MARKERS = CASE_BLACK_MARKERS + CASE_RED_MARKERS
JUDGMENT_KEYWORDS = ("พิพากษาให้จำเลย", "ชำระเงิน", "ค่าทนายความ")
JUDGMENT_START_KEYWORDS = ("คำพิพากษา", "พิพากษาให้จำเลย", "พิพากษาให้จำเลบย")
JUDGMENT_END_KEYWORDS = (
    "ค่าใช้จ่ายในการดำเนินคดีให้เป็นพับ./",
    "ให้เป็นพับ./",
    "เป็นพับ./",
    "พับ./",
)
JUDGMENT_OCR_FALLBACK_END_MARKERS = (
    "ค่าใช้จ่ายในการดำเนินคดีให้เป็นพับ",
    "ค่าใช้จ่ายในการดำเนินคดีให้เป็บพับ",
    "ให้เป็นพับ",
    "ให้เป็บพับ",
    "เป็นพับ",
    "เป็บพับ",
)
FIRST_PAGE_FALLBACK_ANCHORS: dict[str, tuple[float, float, float, float]] = {
    "caseBlackNo": (0.56, 0.07, 0.89, 0.135),
    "caseRedNo": (0.56, 0.14, 0.89, 0.205),
    "courtName": (0.33, 0.34, 0.63, 0.405),
}
BLACK_HEADER_VISION_CROP = (0.52, 0.08, 0.92, 0.16)
RED_HEADER_VISION_CROP = (0.52, 0.17, 0.92, 0.25)
COURT_VISION_CROP = (0.26, 0.22, 0.72, 0.44)
JUDGMENT_VISION_CROP = (0.06, 0.43, 0.96, 0.98)
JUDGMENT_NOISE_MARKERS = (
    "สำหรับเรียกตู้ข้อมูลเท่านั้น",
    "สำหรับเรียกคดีข้อมูลเท่านั้น",
    "สำหรับเรียกตู้ห้องเตาบั้น",
    "สำหรับศาลใช้",
)
JUDGMENT_STOP_MARKERS = (
    "ผู้เขียน",
    "นายธรรมชัย",
    "นางสาวสมหญิง",
)


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
        review_data=document.get("review_data"),
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
    return _document_pipeline_version(document) < _document_target_pipeline_version(document)


def _is_incoming_file(file_path: Path, settings: Settings) -> bool:
    try:
        return file_path.resolve().parent == settings.imports_source_dir.resolve()
    except FileNotFoundError:
        return False


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
    selected_ocr_model: str | None = None,
    selected_candidate_source: str | None = None,
    ocr_candidate_scores: list[dict[str, object]] | None = None,
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
    normalized_page["selected_ocr_model"] = (
        selected_ocr_model
        if selected_ocr_model is not None
        else normalized_page.get("selected_ocr_model")
    )
    normalized_page["selected_candidate_source"] = (
        selected_candidate_source
        if selected_candidate_source is not None
        else normalized_page.get("selected_candidate_source")
    )
    normalized_page["ocr_candidate_scores"] = (
        ocr_candidate_scores
        if ocr_candidate_scores is not None
        else list(normalized_page.get("ocr_candidate_scores") or [])
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


def _pipeline_version_for_category(document_category: str | None) -> int:
    if is_tr_document_category(document_category):
        return TR_IMPORT_OCR_PIPELINE_VERSION
    return IMPORT_OCR_PIPELINE_VERSION


def _document_target_pipeline_version(document: dict[str, object]) -> int:
    return _pipeline_version_for_category(
        str(document.get("document_category") or DEFAULT_DOCUMENT_CATEGORY)
    )


def _get_segment_preview_path(page: dict[str, object]) -> Path | None:
    preview_path = _get_page_preview_path(page, cleaned=True)
    if preview_path is not None:
        return preview_path
    return _get_page_preview_path(page, cleaned=False)


def _get_page_preview_path(
    page: dict[str, object],
    *,
    cleaned: bool,
) -> Path | None:
    field_name = "cleaned_preview_path" if cleaned else "original_preview_path"
    preview_path_value = str(page.get(field_name) or "")
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
        return _run_case_header_crop_ocr_image(preview_image.convert("RGB"), settings)


def _run_case_header_crop_ocr_image(
    image: Image.Image,
    settings: Settings,
) -> str | None:
    width, height = image.size
    crop_box = (
        int(width * 0.48),
        int(height * 0.03),
        int(width * 0.98),
        int(height * 0.23),
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


def _get_page_text_for_review(page: dict[str, object]) -> str:
    for field_name in ("corrected_markdown", "markdown", "raw_markdown"):
        value = page.get(field_name)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _ocr_endpoint_connectivity_error(settings: Settings) -> str | None:
    if not settings.ocr_base_url:
        return None

    try:
        parsed_url = urlparse(settings.ocr_base_url)
    except ValueError as exc:
        return f"OCR endpoint URL is invalid: {exc}"

    hostname = parsed_url.hostname
    if not hostname:
        return None

    port = parsed_url.port
    if port is None:
        port = 443 if parsed_url.scheme == "https" else 80

    try:
        with socket.create_connection((hostname, port), timeout=3.0):
            pass
    except OSError as exc:
        return f"OCR endpoint is not reachable at {hostname}:{port}: {exc}"

    if parsed_url.path.rstrip("/") == "/api/generate":
        health_url = parsed_url._replace(path="/api/tags", query="", params="", fragment="").geturl()
        try:
            request = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(request, timeout=3.0):
                return None
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return None
            return f"OCR endpoint health check failed with HTTP {exc.code}: {health_url}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return f"OCR endpoint health check timed out or failed: {health_url} ({exc})"

    return None


def _vision_endpoint_connectivity_error(settings: Settings) -> str | None:
    if not settings.vision_base_url:
        return None

    try:
        parsed_url = urlparse(settings.vision_base_url)
    except ValueError as exc:
        return f"Vision endpoint URL is invalid: {exc}"

    hostname = parsed_url.hostname
    if not hostname:
        return None

    port = parsed_url.port
    if port is None:
        port = 443 if parsed_url.scheme == "https" else 80

    try:
        with socket.create_connection((hostname, port), timeout=1.5):
            return None
    except OSError as exc:
        return f"Vision endpoint is not reachable at {hostname}:{port}: {exc}"


def _has_usable_review_text(pages: list[dict[str, object]]) -> bool:
    for page in pages:
        text = _get_page_text_for_review(page).strip()
        if text and not text.startswith("[OCR failed"):
            return True
    return False


def _normalize_review_text(value: str) -> str:
    return " ".join(value.replace("\r", "\n").split())


def _compact_for_compare(value: str) -> str:
    return "".join(character for character in _normalize_review_text(value) if not character.isspace()).lower()


def _compact_for_compare_with_offsets(value: str) -> tuple[str, list[int]]:
    normalized_text = _normalize_review_text(value)
    compact_chars: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(normalized_text):
        if character.isspace():
            continue
        compact_chars.append(character.lower())
        offsets.append(index)
    return "".join(compact_chars), offsets


def _find_compact_keyword_end_index(compact_text: str, keywords: tuple[str, ...]) -> int | None:
    end_start_index: int | None = None
    end_index: int | None = None
    for keyword in keywords:
        compact_keyword = _compact_for_compare(keyword)
        if not compact_keyword:
            continue
        found_at = compact_text.find(compact_keyword)
        if found_at == -1:
            continue
        candidate_end_index = found_at + len(compact_keyword)
        if (
            end_start_index is None
            or found_at < end_start_index
            or (found_at == end_start_index and candidate_end_index > (end_index or 0))
        ):
            end_start_index = found_at
            end_index = candidate_end_index
    return end_index


def _slice_review_text_between_keywords(
    text: str,
    *,
    start_keywords: tuple[str, ...],
    end_keywords: tuple[str, ...],
    fallback_end_keywords: tuple[str, ...] = (),
) -> str:
    normalized_text = _normalize_review_text(text)
    if not normalized_text:
        return ""

    compact_text, text_offsets = _compact_for_compare_with_offsets(normalized_text)
    start_index: int | None = None
    for keyword in start_keywords:
        compact_keyword = _compact_for_compare(keyword)
        if not compact_keyword:
            continue
        found_at = compact_text.find(compact_keyword)
        if found_at == -1:
            continue
        if start_index is None or found_at < start_index:
            start_index = found_at

    if start_index is None:
        return normalized_text

    sliced_text = normalized_text[text_offsets[start_index] :]
    compact_sliced, sliced_offsets = _compact_for_compare_with_offsets(sliced_text)
    end_index = _find_compact_keyword_end_index(compact_sliced, end_keywords)
    if end_index is None and fallback_end_keywords:
        end_index = _find_compact_keyword_end_index(compact_sliced, fallback_end_keywords)

    if end_index is None:
        return sliced_text.strip()

    return sliced_text[: sliced_offsets[end_index - 1] + 1].strip()


def _slice_review_text_to_end_marker(
    text: str,
    *,
    end_keywords: tuple[str, ...],
    fallback_end_keywords: tuple[str, ...] = (),
) -> str:
    normalized_text = _normalize_review_text(text)
    if not normalized_text:
        return ""

    compact_text, text_offsets = _compact_for_compare_with_offsets(normalized_text)
    end_index = _find_compact_keyword_end_index(compact_text, end_keywords)
    if end_index is None and fallback_end_keywords:
        end_index = _find_compact_keyword_end_index(compact_text, fallback_end_keywords)
    if end_index is None:
        return normalized_text.strip()

    return normalized_text[: text_offsets[end_index - 1] + 1].strip()


def _repair_judgment_display_spacing(text: str) -> str:
    replacements: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"เป\s*็\s*นต้นไป"), "เป็นต้นไป"),
        (re.compile(r"ค่าใช้\s*จ่า\s*ย"), "ค่าใช้จ่าย"),
        (re.compile(r"ค่าใช้จ่ายในการ\s*ดำเนิน\s*คดี"), "ค่าใช้จ่ายในการดำเนินคดี"),
        (re.compile(r"ขาด\s+ประโยชน์"), "ขาดประโยชน์"),
        (re.compile(r"ส่ว\s+นค่า"), "ส่วนค่า"),
        (re.compile(r"([0-9๐-๙]),\s+([0-9๐-๙])"), r"\1,\2"),
    )
    repaired_text = text
    for pattern, replacement in replacements:
        repaired_text = pattern.sub(replacement, repaired_text)
    return repaired_text


def _normalize_judgment_end_marker(text: str) -> str:
    normalized_text = _repair_judgment_display_spacing(_normalize_review_text(text).strip())
    if not normalized_text:
        return ""

    replacements: tuple[tuple[re.Pattern[str], str], ...] = (
        (
            re.compile(r"ค่าใช้จ่ายในการ\s*ดำเนิน\s*คดี\s*ให้\s*เป\s*็\s*[นบ]\s*พับ\s*(?:\./|\.|/)?\s*$"),
            "ค่าใช้จ่ายในการดำเนินคดีให้เป็นพับ./",
        ),
        (
            re.compile(r"ให้\s*เป\s*็\s*[นบ]\s*พับ\s*(?:\./|\.|/)?\s*$"),
            "ให้เป็นพับ./",
        ),
        (
            re.compile(r"เป\s*็\s*[นบ]\s*พับ\s*(?:\./|\.|/)?\s*$"),
            "เป็นพับ./",
        ),
        (re.compile(r"พับ\s*\./\s*$"), "พับ./"),
    )
    for pattern, replacement in replacements:
        next_text = pattern.sub(replacement, normalized_text)
        if next_text != normalized_text:
            return next_text
    return normalized_text


def _strip_review_page_noise_lines(text: str) -> str:
    kept_lines: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_ocr_line_decorators(raw_line)
        if not line:
            continue
        lower_line = line.lower()
        compact_line = _compact_for_compare(line)
        if lower_line.startswith("<page_number"):
            continue
        if compact_line in {"สำหรับ", "ศาลใช้", "สำหรับศาลใช้"}:
            continue
        if re.fullmatch(r"\(?[0-9๐-๙]+\s*พ\.\)?", line):
            continue
        if re.fullmatch(r"-?\s*[0-9๐-๙]+\s*-?", line):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)


def _build_judgment_display_text_from_page(page: dict[str, object]) -> str:
    page_text = _strip_review_page_noise_lines(_get_page_text_for_review(page))
    display_text = _slice_review_text_between_keywords(
        page_text,
        start_keywords=("พิพากษาให้จำเลย", "พิพากษาให้จำเลบย"),
        end_keywords=JUDGMENT_END_KEYWORDS,
        fallback_end_keywords=JUDGMENT_OCR_FALLBACK_END_MARKERS,
    )
    return _normalize_judgment_end_marker(display_text)


def _build_judgment_continuation_display_text_from_page(page: dict[str, object]) -> str:
    page_text = _strip_review_page_noise_lines(_get_page_text_for_review(page))
    page_text = _normalize_review_text(page_text)
    page_text = re.sub(
        r"^\s*/?\s*[0-9๐-๙]{2,4}\)?\s+(?=เป\s*็?นต้นไป|เป็นต้นไป)",
        "",
        page_text,
    )
    display_text = _slice_review_text_to_end_marker(
        page_text,
        end_keywords=JUDGMENT_END_KEYWORDS,
        fallback_end_keywords=JUDGMENT_OCR_FALLBACK_END_MARKERS,
    )
    return _normalize_judgment_end_marker(display_text)


def _contains_any_review_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    compact_text = _compact_for_compare(text)
    return any(_compact_for_compare(keyword) in compact_text for keyword in keywords if keyword.strip())


def _pick_first_pattern(value: str, patterns: list[re.Pattern[str]]) -> str | None:
    for pattern in patterns:
        match = pattern.search(value)
        if match:
            captured = match.group(1).strip(" .,;:-")
            if captured:
                return captured
    return None


def _pick_pay_amount(value: str) -> str | None:
    return _pick_first_pattern(
        value,
        [
            re.compile(r"พิพากษาให้จำเลย(?:ที่\s*[0-9๐-๙]+)?\s*ชำระเงิน(?:เป็น)?\s*จำนวน\s*([0-9๐-๙,\.]+)"),
            re.compile(r"พิพากษาให้จำเลย(?:ที่\s*[0-9๐-๙]+)?\s*ชำระเงิน\s*([0-9๐-๙,\.]+)"),
            re.compile(r"ชำระเงิน(?:เป็น)?\s*จำนวน\s*([0-9๐-๙,\.]+)"),
            re.compile(r"คืนไม่ได้ให้ใช้ราคาแทนเป็นเงิน(?:จำนวน)?\s*([0-9๐-๙,\.]+)"),
            re.compile(r"ใช้ราคาแทนเป็นเงิน(?:จำนวน)?\s*([0-9๐-๙,\.]+)"),
        ],
    )


def _normalize_review_date(value: str | None) -> str | None:
    if not value:
        return None
    normalized = _normalize_review_text(value.replace("/", " "))
    return normalized or None


def _pick_filing_date(value: str) -> str | None:
    raw_date = _pick_first_pattern(
        value,
        [
            re.compile(r"(?:ฟ้อง|พ้อง)วันที่\s*([0-9๐-๙]+\s+\S+\s+[0-9๐-๙]+)"),
            re.compile(r"(?:ฟ้อง|พ้อง)วันที่\s*([0-9๐-๙]+\s+\S+\s+[0-9๐-๙]+\s*\(?[0-9๐-๙]*\)?)"),
            re.compile(r"วันฟ้อง\s*\(?\s*วันที่\s*([0-9๐-๙]+\s+\S+\s+[0-9๐-๙]+)"),
            re.compile(r"วันฟ้อง\s*\(?\s*วันที่\s*([0-9๐-๙]+\s+\S+\s+[0-9๐-๙]+\s*\)?[0-9๐-๙]*)"),
        ],
    )
    return _normalize_review_date(raw_date)


def _normalize_ocr_number_token(value: str | None) -> str | None:
    if not value:
        return None

    normalized = (
        value.replace(" ", "")
        .replace("ด", "๑")
        .replace("I", "1")
        .replace("l", "1")
        .replace("O", "0")
        .replace("o", "0")
    )
    normalized = normalized.translate(ASCII_TO_THAI_DIGITS)
    return normalized or None


def _decode_latin1_cp874_mojibake(value: str | None) -> str | None:
    if not value:
        return value
    if not any(0x80 <= ord(character) <= 0xFF for character in value):
        return value
    try:
        decoded = value.encode("latin1").decode("cp874")
    except UnicodeError:
        return value
    return decoded


def _looks_like_case_number(value: str | None) -> bool:
    if not value:
        return False
    compact = _compact_for_compare(value)
    return "/" in value and any(character.isdigit() for character in compact)


def _restore_missing_case_series_letter(value: str) -> str:
    compact = value.replace(" ", "")
    if re.match(r"^ผูE[0-9๐-๙]", compact):
        return "ผบE" + compact[3:]
    if re.match(r"^ผE[0-9๐-๙]", compact):
        return "ผบE" + compact[2:]
    if re.match(r"^ผู[0-9๐-๙]", compact):
        return "ผบE" + compact[2:]
    if re.match(r"^ผบ[0-9๐-๙]", compact):
        return "ผบE" + compact[2:]
    return value


def _case_number_missing_series_letter(value: str | None) -> bool:
    if not value:
        return False
    normalized = (_decode_latin1_cp874_mojibake(value) or value).replace(" ", "")
    for marker in CASE_HEADER_MARKERS:
        normalized = normalized.replace(marker.replace(" ", ""), "")
    return bool(re.match(r"^ผบ[0-9๐-๙]", normalized))


def _values_compatible(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    compact_left = _compact_for_compare(left)
    compact_right = _compact_for_compare(right)
    if not compact_left or not compact_right:
        return False
    return compact_left in compact_right or compact_right in compact_left


def _extract_court_name_from_markdown(markdown: str) -> str | None:
    for raw_line in markdown.splitlines():
        line = _strip_ocr_line_decorators(raw_line).strip(" :-\t")
        compact = _compact_for_compare(line)
        if not line or not compact.startswith("ศาล"):
            continue
        if len(line) > 80:
            continue
        return line
    return None


def _get_segment_text(segment: dict[str, object]) -> str:
    raw_text = segment.get("corrected_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    raw_text = segment.get("raw_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    text = segment.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _merge_bboxes(
    bboxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    if not bboxes:
        return None
    left = min(bbox[0] for bbox in bboxes)
    top = min(bbox[1] for bbox in bboxes)
    right = max(bbox[2] for bbox in bboxes)
    bottom = max(bbox[3] for bbox in bboxes)
    return (
        round(max(0.0, left), 6),
        round(max(0.0, top), 6),
        round(min(1.0, right), 6),
        round(min(1.0, bottom), 6),
    )


def _find_segment_anchor_by_value(
    page: dict[str, object],
    value: str | None,
) -> tuple[float, float, float, float] | None:
    if not value:
        return None

    target = _compact_for_compare(value)
    if len(target) < 3:
        return None

    raw_segments = page.get("segments") or []
    if not isinstance(raw_segments, list):
        return None

    matched_boxes: list[tuple[float, float, float, float]] = []
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict):
            continue
        segment_text = _compact_for_compare(_get_segment_text(raw_segment))
        if not segment_text:
            continue
        if target in segment_text or segment_text in target:
            bbox = raw_segment.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                try:
                    matched_boxes.append(tuple(float(part) for part in bbox))
                except (TypeError, ValueError):
                    continue

    return _merge_bboxes(matched_boxes)


def _find_segment_anchor_by_keyword(
    page: dict[str, object],
    keywords: tuple[str, ...],
) -> tuple[float, float, float, float] | None:
    raw_segments = page.get("segments") or []
    if not isinstance(raw_segments, list):
        return None

    compact_keywords = tuple(_compact_for_compare(keyword) for keyword in keywords if keyword)
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict):
            continue
        segment_text = _compact_for_compare(_get_segment_text(raw_segment))
        if not segment_text:
            continue
        if any(keyword in segment_text for keyword in compact_keywords):
            bbox = raw_segment.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                try:
                    return tuple(float(part) for part in bbox)
                except (TypeError, ValueError):
                    return None
    return None


def _make_review_field(
    *,
    value: str | None,
    page_number: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    source: str | None = None,
) -> dict[str, object]:
    return {
        "value": value,
        "pageNumber": page_number,
        "bbox": list(bbox) if bbox is not None else None,
        "source": source,
    }


def _detect_page_anchor_lines(
    page: dict[str, object],
    settings: Settings,
) -> list[AnchorLine]:
    preview_path = _get_segment_preview_path(page)
    if preview_path is None:
        return []

    try:
        anchor_lines = detect_anchor_lines(
            preview_path,
            provider=settings.anchor_provider,
            lang=settings.anchor_language,
        )
    except Exception as exc:
        _log_import_event(f"Anchor provider failed for {preview_path.name}: {exc}")
        return []

    if anchor_lines:
        _log_import_event(
            f"Anchor provider {settings.anchor_provider} found "
            f"{len(anchor_lines)} line anchor(s) for {preview_path.name}."
        )
    return anchor_lines


def _anchor_line_matches_value(
    line: AnchorLine,
    value: str | None,
    markers: tuple[str, ...],
) -> bool:
    line_text = _compact_for_compare(line.text)
    if not line_text:
        return False

    if value:
        target = _compact_for_compare(value)
        if target and (target in line_text or line_text in target):
            return True

    compact_markers = tuple(_compact_for_compare(marker) for marker in markers)
    return any(marker and marker in line_text for marker in compact_markers)


def _choose_anchor_line_by_text(
    lines: list[AnchorLine],
    *,
    value: str | None,
    markers: tuple[str, ...],
) -> AnchorLine | None:
    candidates = [
        line
        for line in lines
        if line.text and _anchor_line_matches_value(line, value, markers)
    ]
    if not candidates:
        return None

    def score(line: AnchorLine) -> tuple[float, float, float]:
        bbox = line.bbox
        compact_text = _compact_for_compare(line.text)
        target = _compact_for_compare(value or "")
        value_bonus = 1.0 if target and target in compact_text else 0.0
        right_header_bonus = 1.0 if bbox[0] >= 0.45 and bbox[1] <= 0.25 else 0.0
        return (value_bonus, right_header_bonus, -bbox[1])

    return max(candidates, key=score)


def _choose_court_anchor_line_by_text(
    lines: list[AnchorLine],
    court_name: str | None,
) -> AnchorLine | None:
    candidates: list[AnchorLine] = []
    target = _compact_for_compare(court_name or "")
    for line in lines:
        line_text = _strip_ocr_line_decorators(line.text)
        compact_text = _compact_for_compare(line_text)
        if not compact_text or "สำหรับ" in compact_text:
            continue
        if target and (target in compact_text or compact_text in target):
            candidates.append(line)
            continue
        if compact_text.startswith("ศาล"):
            candidates.append(line)

    if not candidates:
        return None

    def score(line: AnchorLine) -> tuple[float, float, float]:
        left, top, right, _bottom = line.bbox
        center_x = (left + right) / 2
        target_bonus = 1.0 if target and target in _compact_for_compare(line.text) else 0.0
        center_score = 1.0 - min(1.0, abs(center_x - 0.5) / 0.35)
        return (target_bonus, center_score, -abs(top - 0.25))

    return max(candidates, key=score)


def _choose_header_anchor_lines_by_geometry(
    lines: list[AnchorLine],
) -> dict[str, AnchorLine]:
    result: dict[str, AnchorLine] = {}

    case_lines = [
        line
        for line in lines
        if (
            line.bbox[0] >= 0.48
            and line.bbox[1] >= 0.075
            and line.bbox[3] <= 0.21
            and (line.bbox[2] - line.bbox[0]) >= 0.22
        )
    ]
    case_lines.sort(key=lambda line: (line.bbox[1], line.bbox[0]))
    if case_lines:
        result["caseBlackNo"] = case_lines[0]
    if len(case_lines) >= 2:
        result["caseRedNo"] = case_lines[1]

    red_bottom = result.get("caseRedNo", result.get("caseBlackNo"))
    red_bottom_y = red_bottom.bbox[3] if red_bottom is not None else 0.14
    center_lines = [
        line
        for line in lines
        if (
            line.bbox[1] > red_bottom_y + 0.02
            and line.bbox[1] >= 0.14
            and line.bbox[3] <= 0.36
            and 0.25 <= ((line.bbox[0] + line.bbox[2]) / 2) <= 0.75
            and (line.bbox[2] - line.bbox[0]) >= 0.08
        )
    ]
    center_lines.sort(key=lambda line: (line.bbox[1], line.bbox[0]))
    if len(center_lines) >= 2:
        result["courtName"] = center_lines[1]
    elif center_lines:
        result["courtName"] = center_lines[0]

    return result


def _resolve_header_anchors_from_provider(
    lines: list[AnchorLine],
    *,
    case_black_value: str | None,
    case_red_value: str | None,
    court_name: str | None,
) -> dict[str, dict[str, object]]:
    if not lines:
        return {}

    resolved: dict[str, dict[str, object]] = {}
    black_line = _choose_anchor_line_by_text(
        lines,
        value=case_black_value,
        markers=CASE_BLACK_MARKERS,
    )
    red_line = _choose_anchor_line_by_text(
        lines,
        value=case_red_value,
        markers=CASE_RED_MARKERS,
    )
    court_line = _choose_court_anchor_line_by_text(lines, court_name)

    geometry_lines = _choose_header_anchor_lines_by_geometry(lines)
    black_line = black_line or geometry_lines.get("caseBlackNo")
    red_line = red_line or geometry_lines.get("caseRedNo")
    court_line = court_line or geometry_lines.get("courtName")

    if black_line is not None:
        resolved["caseBlackNo"] = {
            "bbox": black_line.bbox,
            "source": f"{black_line.source}_anchor",
        }
    if red_line is not None:
        resolved["caseRedNo"] = {
            "bbox": red_line.bbox,
            "source": f"{red_line.source}_anchor",
        }
    if court_line is not None:
        resolved["courtName"] = {
            "bbox": court_line.bbox,
            "source": f"{court_line.source}_anchor",
        }
    return resolved


def _normalize_bbox_1000(raw_bbox: object) -> tuple[float, float, float, float] | None:
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    try:
        left, top, right, bottom = (float(part) for part in raw_bbox)
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return (
        max(0.0, min(left / 1000.0, 1.0)),
        max(0.0, min(top / 1000.0, 1.0)),
        max(0.0, min(right / 1000.0, 1.0)),
        max(0.0, min(bottom / 1000.0, 1.0)),
    )


def _crop_image_with_bounds(
    image: Image.Image,
    bounds: tuple[float, float, float, float],
) -> Image.Image:
    width = max(image.width, 1)
    height = max(image.height, 1)
    left = int(round(width * bounds[0]))
    top = int(round(height * bounds[1]))
    right = int(round(width * bounds[2]))
    bottom = int(round(height * bounds[3]))
    return image.crop((left, top, max(right, left + 1), max(bottom, top + 1)))


def _map_crop_bbox_to_page(
    crop_bbox: tuple[float, float, float, float],
    crop_bounds: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    crop_left, crop_top, crop_right, crop_bottom = crop_bounds
    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top
    return (
        round(crop_left + (crop_bbox[0] * crop_width), 6),
        round(crop_top + (crop_bbox[1] * crop_height), 6),
        round(crop_left + (crop_bbox[2] * crop_width), 6),
        round(crop_top + (crop_bbox[3] * crop_height), 6),
    )


def _run_ocr_on_crop_image(
    image: Image.Image,
    settings: Settings,
) -> str | None:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        image.convert("RGB").save(temp_path, format="PNG")
        return run_ocr_page(
            temp_path,
            1,
            settings,
            source_is_cleaned=True,
        )
    except Exception:
        return None
    finally:
        temp_path.unlink(missing_ok=True)


def _extract_case_header_values_from_crop_text(markdown: str | None) -> dict[str, str]:
    if not markdown:
        return {}

    lines = [_strip_ocr_line_decorators(line) for line in markdown.splitlines()]
    lines = [line for line in lines if line]
    result: dict[str, str] = {}

    black_value = _extract_case_header_value(lines, CASE_BLACK_MARKERS)
    red_value = _extract_case_header_value(lines, CASE_RED_MARKERS)
    if black_value:
        result["caseBlackNo"] = black_value
    if red_value:
        result["caseRedNo"] = red_value
    return result


def _extract_case_field_value_from_crop_text(
    markdown: str | None,
    field_key: str,
) -> str | None:
    if not markdown:
        return None

    extracted_values = _extract_case_header_values_from_crop_text(markdown)
    extracted_value = _normalize_header_field_value(field_key, extracted_values.get(field_key))
    if extracted_value:
        return extracted_value

    candidate_values: list[str] = []
    for raw_line in markdown.splitlines():
        line = _strip_ocr_line_decorators(raw_line).strip()
        if not line:
            continue
        line_value = _extract_case_number_like_value(line)
        normalized_value = _normalize_header_field_value(field_key, line_value)
        if normalized_value:
            candidate_values.append(normalized_value)

    if not candidate_values:
        return None

    candidate_values.sort(key=_score_case_number_candidate, reverse=True)
    return candidate_values[0]


def _extract_court_name_from_crop_text(markdown: str | None) -> str | None:
    if not markdown:
        return None

    for raw_line in markdown.splitlines():
        line = _strip_ocr_line_decorators(raw_line).strip(" :-\t")
        if not line:
            continue
        value = _extract_court_name_from_markdown(line)
        if value:
            return value
    return None


def _extract_vision_lines(
    payload: dict[str, object],
) -> list[tuple[str, tuple[float, float, float, float] | None]]:
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        return []

    lines: list[tuple[str, tuple[float, float, float, float] | None]] = []
    for raw_line in raw_lines:
        if not isinstance(raw_line, dict):
            continue
        text = raw_line.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        lines.append((text.strip(), _normalize_bbox_1000(raw_line.get("bbox_1000"))))
    return lines


def _extract_case_number_like_value(text: str | None) -> str | None:
    if not text:
        return None

    compact_text = _decode_latin1_cp874_mojibake(text) or text
    compact_text = compact_text.replace(" ", "")
    for token in re.findall(r"[A-Za-zก-๛0-9๐-๙./-]+", compact_text):
        normalized = token.strip(".,;:-/")
        if "ผบ" in normalized and not normalized.startswith("ผบ"):
            normalized = normalized[normalized.find("ผบ") :]
        elif "พบ" in normalized and not normalized.startswith("พบ"):
            normalized = normalized[normalized.find("พบ") :]
        if normalized.startswith("พบ"):
            normalized = "ผบ" + normalized[2:]
        if _looks_like_case_number(normalized):
            return normalized
    return None


def _build_case_crop_variants(crop_image: Image.Image) -> list[tuple[str, Image.Image]]:
    base = crop_image.convert("RGB")
    enlarged = base.resize((base.width * 4, base.height * 4), Image.Resampling.LANCZOS)
    enlarged = ImageEnhance.Contrast(enlarged).enhance(1.7)
    enlarged = enlarged.filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN)

    grayscale = ImageOps.grayscale(base)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(2.0).convert("RGB")
    grayscale = grayscale.resize((grayscale.width * 4, grayscale.height * 4), Image.Resampling.LANCZOS)

    return [
        ("base", base),
        ("enlarged", enlarged),
        ("grayscale", grayscale),
    ]


def _build_case_crop_ocr_image(crop_image: Image.Image) -> Image.Image:
    grayscale = ImageOps.grayscale(crop_image.convert("RGB"))
    grayscale = ImageEnhance.Contrast(grayscale).enhance(2.1)
    grayscale = grayscale.resize((grayscale.width * 4, grayscale.height * 4), Image.Resampling.LANCZOS)
    grayscale = grayscale.filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN)
    return grayscale.convert("RGB")


def _score_case_number_candidate(value: str) -> float:
    score = 0.0
    compact = value.replace(" ", "")
    slash_count = compact.count("/")
    thai_digit_count = sum(1 for character in compact if "๐" <= character <= "๙")
    ascii_digit_count = sum(1 for character in compact if character.isdigit() and not ("๐" <= character <= "๙"))

    if compact.startswith("ผบ"):
        score += 3.0
    if compact.startswith("ผบ."):
        score += 0.5
    if "ศต" in compact:
        score += 1.0
    if "E" in compact:
        score += 0.8
    if slash_count == 1:
        score += 3.0
    elif slash_count == 2:
        score += 1.0
    if thai_digit_count >= 4:
        score += 2.0
    if ascii_digit_count > 0:
        score -= 0.4
    if re.search(r"/๒๕[0-9๐-๙]{2}$", compact):
        score += 3.5
    if re.search(r"/25[0-9]{2}$", compact):
        score += 2.5
    if "." in compact and "ผบ" not in compact[:4]:
        score -= 1.2
    if len(compact) < 8 or len(compact) > 22:
        score -= 1.5
    return score


def _case_field_needs_targeted_recovery(
    value: str | None,
    *,
    sibling_value: str | None = None,
) -> bool:
    normalized_value = _normalize_header_field_value("caseBlackNo", value)
    if not normalized_value:
        return True
    if sibling_value and _values_compatible(normalized_value, sibling_value):
        return True
    if _score_case_number_candidate(normalized_value) < 7.0:
        return True
    return False


def _choose_case_candidate_with_vision(
    *,
    crop_image: Image.Image,
    candidate_values: list[str],
    field_label: str,
    settings: Settings,
) -> str | None:
    if not candidate_values:
        return None

    deduped_candidates: list[str] = []
    for candidate in candidate_values:
        if candidate not in deduped_candidates:
            deduped_candidates.append(candidate)

    candidate_list = "\n".join(f"- {candidate}" for candidate in deduped_candidates[:6])
    try:
        payload = run_vision_json(
            image=crop_image,
            prompt=(
                f"Read this Thai court {field_label} line crop and choose the best case number value. "
                "Return JSON only in exactly this shape: {\"value\":string|null}. "
                "Choose only from these OCR candidates if one matches the image:\n"
                f"{candidate_list}\n"
                "Return only the value text, not the label. Preserve Thai digits, Latin letters, slashes, and dots exactly. "
                "If none match confidently, use null."
            ),
            settings=settings,
        )
    except Exception:
        return None

    raw_value = str(payload.get("value") or "").strip() or None
    return _extract_case_number_like_value(raw_value)


def _append_case_candidate(
    candidate_records: list[dict[str, object]],
    *,
    field_key: str,
    value: str | None,
    bbox: tuple[float, float, float, float] | list[float] | None,
    source: str,
    method: str,
    preview_source: str,
) -> None:
    normalized_value = _normalize_header_field_value(field_key, value)
    if not normalized_value:
        return

    normalized_bbox: tuple[float, float, float, float] | None = None
    if isinstance(bbox, tuple) and len(bbox) == 4:
        normalized_bbox = bbox
    elif isinstance(bbox, list) and len(bbox) == 4:
        try:
            normalized_bbox = tuple(float(part) for part in bbox)
        except (TypeError, ValueError):
            normalized_bbox = None

    candidate_records.append(
        {
            "value": normalized_value,
            "bbox": normalized_bbox,
            "source": source,
            "method": method,
            "preview_source": preview_source,
            "score": _score_case_number_candidate(normalized_value),
        }
    )


def _choose_best_case_field_candidate(
    candidate_records: list[dict[str, object]],
) -> dict[str, object] | None:
    if not candidate_records:
        return None

    grouped_candidates: dict[str, dict[str, object]] = {}
    for record in candidate_records:
        value = str(record.get("value") or "").strip()
        if not value:
            continue
        grouped_entry = grouped_candidates.setdefault(
            value,
            {
                "value": value,
                "records": [],
                "preview_sources": set(),
                "methods": set(),
                "source_labels": set(),
                "score": 0.0,
            },
        )
        grouped_entry["records"].append(record)
        grouped_entry["preview_sources"].add(str(record.get("preview_source") or ""))
        grouped_entry["methods"].add(str(record.get("method") or ""))
        grouped_entry["source_labels"].add(str(record.get("source") or ""))
        grouped_entry["score"] = max(
            float(grouped_entry["score"]),
            float(record.get("score") or 0.0),
        )

    if not grouped_candidates:
        return None

    for grouped_entry in grouped_candidates.values():
        supporting_records = list(grouped_entry["records"])
        preview_sources = set(grouped_entry["preview_sources"])
        methods = set(grouped_entry["methods"])
        source_labels = set(grouped_entry["source_labels"])
        grouped_entry["score"] += max(len(supporting_records) - 1, 0) * 2.3
        grouped_entry["score"] += max(len(preview_sources) - 1, 0) * 1.8
        if "full_page_ocr" in methods:
            grouped_entry["score"] += 0.5
        if "ocr_crop" in methods:
            grouped_entry["score"] += 0.7
        if "vision_crop" in methods:
            grouped_entry["score"] += 0.6
        if {"full_page_ocr", "ocr_crop"} <= methods:
            grouped_entry["score"] += 1.3
        if {"ocr_crop", "vision_crop"} <= methods:
            grouped_entry["score"] += 0.9
        if any("original" in source_label for source_label in source_labels):
            grouped_entry["score"] += 0.2

    best_value, best_entry = max(
        grouped_candidates.items(),
        key=lambda item: (
            float(item[1]["score"]),
            len(item[1]["records"]),
            len(item[0]),
        ),
    )

    supporting_records = list(best_entry["records"])
    best_record = max(
        supporting_records,
        key=lambda record: (
            1 if record.get("bbox") is not None else 0,
            2 if record.get("method") == "vision_crop" else 1 if record.get("method") == "ocr_crop" else 0,
            1 if record.get("preview_source") == "original" else 0,
            float(record.get("score") or 0.0),
        ),
    )
    derived_source = (
        "hybrid_case_consensus"
        if len(supporting_records) > 1
        else str(best_record.get("source") or "hybrid_case_consensus")
    )
    return {
        "value": best_value,
        "bbox": best_record.get("bbox"),
        "source": derived_source,
        "support_count": len(supporting_records),
        "consensus_score": round(float(best_entry["score"]), 3),
    }


def _normalize_header_field_value(
    field_key: str,
    value: str | None,
) -> str | None:
    if not value:
        return None

    normalized = (_decode_latin1_cp874_mojibake(value) or value).strip()
    if field_key in {"caseBlackNo", "caseRedNo"}:
        normalized = normalized.replace(" ", "")
        for marker in CASE_HEADER_MARKERS:
            normalized = normalized.replace(marker.replace(" ", ""), "")
        normalized = _restore_missing_case_series_letter(normalized)
        if not _looks_like_case_number(normalized):
            return None
    elif field_key == "courtName":
        normalized = normalized.replace(" ", "")
        if "ศาล" not in normalized:
            return None

    return normalized


def _extract_single_case_field_with_vision_crop(
    image: Image.Image,
    *,
    field_key: str,
    crop_bounds: tuple[float, float, float, float],
    prompt: str,
    settings: Settings,
) -> tuple[dict[str, object] | None, list[str]]:
    errors: list[str] = []
    crop_image = _crop_image_with_bounds(image, crop_bounds)

    candidate_values: list[str] = []
    variant_images = _build_case_crop_variants(crop_image)
    for _, variant_image in variant_images:
        try:
            payload = run_vision_json(
                image=variant_image,
                prompt=prompt,
                settings=settings,
            )
        except Exception as exc:
            errors.append(str(exc))
            continue

        raw_value = str(payload.get("value") or "").strip() or None
        extracted_value = _extract_case_number_like_value(raw_value)
        normalized_value = _normalize_header_field_value(field_key, extracted_value)
        if normalized_value:
            candidate_values.append(normalized_value)

        try:
            fallback_payload = run_vision_json(
                image=variant_image,
                prompt=(
                    "Read the single visible case-number line in this Thai court document crop. "
                    "Return JSON only in exactly this shape: {\"text\":string|null}. "
                    "Transcribe the whole line exactly. Preserve Thai digits, Latin letters, slashes, and punctuation. "
                    "If the crop does not contain a single case-number line, use null."
                ),
                settings=settings,
            )
        except Exception as exc:
            errors.append(str(exc))
        else:
            fallback_text = str(fallback_payload.get("text") or "").strip() or None
            extracted_value = _extract_case_number_like_value(fallback_text)
            normalized_value = _normalize_header_field_value(field_key, extracted_value)
            if normalized_value:
                candidate_values.append(normalized_value)

    candidate_values.sort(key=_score_case_number_candidate, reverse=True)
    normalized_value = candidate_values[0] if candidate_values else None
    chooser_value = _choose_case_candidate_with_vision(
        crop_image=crop_image,
        candidate_values=candidate_values,
        field_label="case-number",
        settings=settings,
    )
    chooser_value = _normalize_header_field_value(field_key, chooser_value)
    if chooser_value:
        normalized_value = chooser_value
    if not normalized_value:
        return None, errors

    return {
        "value": normalized_value,
        "bbox": list(crop_bounds),
        "source": "qwen_header_crop",
    }, errors


def _extract_case_fields_with_vision_crop(
    image: Image.Image,
    settings: Settings,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    errors: list[str] = []
    result: dict[str, dict[str, object]] = {}

    black_field, black_errors = _extract_single_case_field_with_vision_crop(
        image,
        field_key="caseBlackNo",
        crop_bounds=BLACK_HEADER_VISION_CROP,
        prompt=(
            "Read this Thai court case-number line crop. "
            "Return JSON only in exactly this shape: {\"value\":string|null}. "
            "Extract only the black case number value after the label. "
            "Preserve Thai digits, Latin letters, slashes, dots, and punctuation exactly. "
            "If unclear, use null."
        ),
        settings=settings,
    )
    red_field, red_errors = _extract_single_case_field_with_vision_crop(
        image,
        field_key="caseRedNo",
        crop_bounds=RED_HEADER_VISION_CROP,
        prompt=(
            "Read this Thai court case-number line crop. "
            "Return JSON only in exactly this shape: {\"value\":string|null}. "
            "Extract only the red case number value after the label. "
            "Preserve Thai digits, Latin letters, slashes, dots, and punctuation exactly. "
            "If unclear, use null."
        ),
        settings=settings,
    )
    errors.extend(black_errors)
    errors.extend(red_errors)
    if black_field is not None:
        result["caseBlackNo"] = black_field
    if red_field is not None:
        result["caseRedNo"] = red_field

    return result, errors


def _collect_case_field_candidates_from_preview(
    image: Image.Image,
    *,
    field_key: str,
    crop_bounds: tuple[float, float, float, float],
    preview_source: str,
    settings: Settings,
) -> list[dict[str, object]]:
    crop_image = _crop_image_with_bounds(image, crop_bounds)
    candidate_records: list[dict[str, object]] = []
    prepared_image = _build_case_crop_ocr_image(crop_image)
    crop_markdown = _run_ocr_on_crop_image(prepared_image, settings)
    crop_value = _extract_case_field_value_from_crop_text(crop_markdown, field_key)
    _append_case_candidate(
        candidate_records,
        field_key=field_key,
        value=crop_value,
        bbox=crop_bounds,
        source=f"ocr_crop_{preview_source}",
        method="ocr_crop",
        preview_source=preview_source,
    )

    return candidate_records


def _resolve_first_page_case_fields(
    page_asset: dict[str, object],
    *,
    first_page_markdown: str,
    settings: Settings,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    resolved_fields: dict[str, dict[str, object]] = {}
    errors: list[str] = []
    preview_images: list[tuple[str, Image.Image]] = []
    header_crop_values_by_source: dict[str, dict[str, str]] = {}
    full_page_values = _extract_case_header_values_from_crop_text(first_page_markdown)
    if not full_page_values:
        return resolved_fields, errors

    needs_targeted_recovery = any(
        (
            _case_field_needs_targeted_recovery(
                full_page_values.get("caseBlackNo"),
                sibling_value=full_page_values.get("caseRedNo"),
            ),
            _case_field_needs_targeted_recovery(
                full_page_values.get("caseRedNo"),
                sibling_value=full_page_values.get("caseBlackNo"),
            ),
        )
    )

    for field_key in ("caseBlackNo", "caseRedNo"):
        normalized_value = _normalize_header_field_value(field_key, full_page_values.get(field_key))
        if not normalized_value:
            continue
        resolved_fields[field_key] = {
            "value": normalized_value,
            "bbox": None,
            "source": "full_page_cleaned_ocr",
        }

    if not needs_targeted_recovery:
        return resolved_fields, errors

    for preview_source, cleaned in (("cleaned", True), ("original", False)):
        preview_path = _get_page_preview_path(page_asset, cleaned=cleaned)
        if preview_path is None:
            continue
        try:
            with Image.open(preview_path) as preview_image:
                rgb_preview = preview_image.convert("RGB")
                preview_images.append((preview_source, rgb_preview))
                if preview_source == "original":
                    soft_preview = clean_pil_image_soft(rgb_preview).convert("RGB")
                    preview_images.append(("soft_header", soft_preview))
                if needs_targeted_recovery:
                    header_crop_markdown = _run_case_header_crop_ocr_image(rgb_preview, settings)
                    header_crop_values_by_source[preview_source] = _extract_case_header_values_from_crop_text(
                        header_crop_markdown
                    )
                    if preview_source == "original":
                        soft_header_markdown = _run_case_header_crop_ocr_image(soft_preview, settings)
                        header_crop_values_by_source["soft_header"] = _extract_case_header_values_from_crop_text(
                            soft_header_markdown
                        )
        except Exception as exc:
            errors.append(f"{preview_source} preview load failed: {exc}")

    field_specs = {
        "caseBlackNo": {
            "crop_bounds": BLACK_HEADER_VISION_CROP,
            "prompt": (
                "Read this Thai court case-number line crop. "
                "Return JSON only in exactly this shape: {\"value\":string|null}. "
                "Extract only the black case number value after the label. "
                "Preserve Thai digits, Latin letters, slashes, dots, and punctuation exactly. "
                "If unclear, use null."
            ),
        },
        "caseRedNo": {
            "crop_bounds": RED_HEADER_VISION_CROP,
            "prompt": (
                "Read this Thai court case-number line crop. "
                "Return JSON only in exactly this shape: {\"value\":string|null}. "
                "Extract only the red case number value after the label. "
                "Preserve Thai digits, Latin letters, slashes, dots, and punctuation exactly. "
                "If unclear, use null."
            ),
        },
    }
    for field_key, spec in field_specs.items():
        candidate_records: list[dict[str, object]] = []
        _append_case_candidate(
            candidate_records,
            field_key=field_key,
            value=full_page_values.get(field_key),
            bbox=None,
            source="full_page_cleaned_ocr",
            method="full_page_ocr",
            preview_source="cleaned",
        )

        for preview_source, preview_image in preview_images:
            _append_case_candidate(
                candidate_records,
                field_key=field_key,
                value=header_crop_values_by_source.get(preview_source, {}).get(field_key),
                bbox=spec["crop_bounds"],
                source=f"header_crop_ocr_{preview_source}",
                method="ocr_crop",
                preview_source=preview_source,
            )
            if preview_source != "cleaned":
                continue
            candidate_records.extend(
                _collect_case_field_candidates_from_preview(
                    preview_image,
                    field_key=field_key,
                    crop_bounds=spec["crop_bounds"],
                    preview_source=preview_source,
                    settings=settings,
                )
            )

        selected_candidate = _choose_best_case_field_candidate(candidate_records)
        if selected_candidate is None or _case_field_needs_targeted_recovery(
            str(selected_candidate.get("value") or "") if selected_candidate else None
        ):
            for preview_source, preview_image in preview_images:
                if preview_source == "cleaned":
                    continue
                candidate_records.extend(
                    _collect_case_field_candidates_from_preview(
                        preview_image,
                        field_key=field_key,
                        crop_bounds=spec["crop_bounds"],
                        preview_source=preview_source,
                        settings=settings,
                    )
                )
            selected_candidate = _choose_best_case_field_candidate(candidate_records)

        should_try_vision_for_case_field = False
        if should_try_vision_for_case_field and settings.vision_ready and (
            selected_candidate is None
            or int(selected_candidate.get("support_count") or 0) < 2
        ):
            for preview_source, preview_image in preview_images:
                try:
                    vision_field, vision_errors = _extract_single_case_field_with_vision_crop(
                        preview_image,
                        field_key=field_key,
                        crop_bounds=spec["crop_bounds"],
                        prompt=spec["prompt"],
                        settings=settings,
                    )
                except Exception as exc:
                    errors.append(f"{field_key} vision crop failed on {preview_source}: {exc}")
                    continue

                errors.extend(
                    f"{field_key} vision crop {preview_source}: {message}"
                    for message in vision_errors
                    if message
                )
                if vision_field is None:
                    continue
                _append_case_candidate(
                    candidate_records,
                    field_key=field_key,
                    value=str(vision_field.get("value") or "").strip() or None,
                    bbox=vision_field.get("bbox"),
                    source=f"{vision_field.get('source') or 'qwen_header_crop'}_{preview_source}",
                    method="vision_crop",
                    preview_source=preview_source,
                )

        selected_candidate = _choose_best_case_field_candidate(candidate_records)
        if selected_candidate is None:
            continue
        resolved_fields[field_key] = {
            "value": selected_candidate["value"],
            "bbox": selected_candidate.get("bbox"),
            "source": selected_candidate.get("source") or "hybrid_case_consensus",
        }

    return resolved_fields, errors


def _extract_court_field_with_vision_crop(
    image: Image.Image,
    crop_bounds: tuple[float, float, float, float],
    settings: Settings,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    errors: list[str] = []
    crop_image = _crop_image_with_bounds(image, crop_bounds)

    try:
        payload = run_vision_json(
            image=crop_image,
            prompt=(
                "Read this Thai court document crop. "
                "Return JSON only in exactly this shape: "
                "{\"court_name\":{\"value\":string|null,\"bbox_1000\":[number,number,number,number]|null}}. "
                "Extract only the visible Thai court name text in the center. "
                "Do not include dates or labels. If unclear, use null."
            ),
            settings=settings,
        )
    except Exception as exc:
        errors.append(str(exc))
        payload = {}

    raw_entry = payload.get("court_name")
    vision_value: str | None = None
    vision_bbox: tuple[float, float, float, float] | None = None
    if isinstance(raw_entry, dict):
        raw_value = raw_entry.get("value")
        if isinstance(raw_value, str) and raw_value.strip():
            vision_value = raw_value.strip()
        vision_bbox = _normalize_bbox_1000(raw_entry.get("bbox_1000"))
    elif isinstance(raw_entry, str) and raw_entry.strip():
        vision_value = raw_entry.strip()

    value = _normalize_header_field_value("courtName", vision_value)
    if not value:
        return {}, errors

    return {
        "courtName": {
            "value": value,
            "bbox": _map_crop_bbox_to_page(vision_bbox, crop_bounds) if vision_bbox is not None else None,
            "source": "qwen_court_crop",
        }
    }, errors


def _extract_header_fields_with_vision(
    page_asset: dict[str, object],
    settings: Settings,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    preview_path = _get_segment_preview_path(page_asset)
    if preview_path is None:
        return {}, []

    errors: list[str] = []
    try:
        with Image.open(preview_path) as preview_image:
            rgb_preview = preview_image.convert("RGB")
            case_fields, case_errors = _extract_case_fields_with_vision_crop(rgb_preview, settings)
            court_field, court_errors = _extract_court_field_with_vision_crop(
                rgb_preview,
                COURT_VISION_CROP,
                settings,
            )
            errors.extend(case_errors)
            errors.extend(court_errors)
    except Exception as exc:
        errors.append(str(exc))
        return {}, errors

    return {
        **case_fields,
        **court_field,
    }, errors


def _extract_judgment_hit_with_vision_crop(
    page_asset: dict[str, object],
    settings: Settings,
) -> dict[str, object] | None:
    preview_path = _get_segment_preview_path(page_asset)
    if preview_path is None:
        return None

    try:
        with Image.open(preview_path) as preview_image:
            crop_image = _crop_image_with_bounds(
                preview_image.convert("RGB"),
                JUDGMENT_VISION_CROP,
            )
    except Exception:
        return None

    candidate_texts: list[str] = []
    crop_ocr_text = _run_ocr_on_crop_image(crop_image, settings)
    if isinstance(crop_ocr_text, str) and crop_ocr_text.strip():
        candidate_texts.append(crop_ocr_text)

    try:
        payload = run_vision_json(
            image=crop_image,
            prompt=(
                "Read this Thai court judgment body crop. "
                "Return JSON only in exactly this shape: {\"text\":string|null}. "
                "Transcribe the visible judgment paragraph exactly in Thai. "
                "Preserve Thai digits, punctuation, and line order. "
                "Ignore watermarks if possible. If unreadable, use null."
            ),
            settings=settings,
        )
    except Exception:
        payload = {}

    raw_text = payload.get("text")
    if isinstance(raw_text, str) and raw_text.strip():
        candidate_texts.append(raw_text)

    display_text = ""
    best_has_start_keyword = False
    for candidate_text in candidate_texts:
        bounded_text = _slice_review_text_between_keywords(
            candidate_text,
            start_keywords=JUDGMENT_START_KEYWORDS,
            end_keywords=JUDGMENT_END_KEYWORDS,
            fallback_end_keywords=JUDGMENT_OCR_FALLBACK_END_MARKERS,
        )
        bounded_text = _normalize_judgment_end_marker(bounded_text)
        has_start_keyword = _contains_any_review_keyword(bounded_text, JUDGMENT_START_KEYWORDS)
        if not bounded_text:
            continue
        if has_start_keyword and not best_has_start_keyword:
            display_text = bounded_text
            best_has_start_keyword = True
            continue
        if has_start_keyword == best_has_start_keyword and len(bounded_text) > len(display_text):
            display_text = bounded_text
            best_has_start_keyword = has_start_keyword
    if not display_text:
        return None

    page_number = int(page_asset.get("page_number", 0))
    return {
        "id": f"page-{page_number}-judgment-vision-1",
        "pageNumber": page_number,
        "text": display_text,
        "displayText": display_text,
        "bbox": list(JUDGMENT_VISION_CROP),
    }


def _extract_attorney_fee_with_vision_crop(
    page_asset: dict[str, object],
    settings: Settings,
) -> str | None:
    preview_path = _get_segment_preview_path(page_asset)
    if preview_path is None:
        return None

    try:
        with Image.open(preview_path) as preview_image:
            crop_image = _crop_image_with_bounds(
                preview_image.convert("RGB"),
                JUDGMENT_VISION_CROP,
            )
    except Exception:
        return None

    try:
        payload = run_vision_json(
            image=crop_image,
            prompt=(
                "Read this Thai court judgment crop. "
                "Return JSON only in exactly this shape: {\"attorney_fee\":string|null}. "
                "Extract only the attorney fee amount after the phrase ค่าทนายความ. "
                "Preserve Thai digits and punctuation. If not visible, use null."
            ),
            settings=settings,
        )
    except Exception:
        return None

    raw_value = payload.get("attorney_fee")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None

    normalized_value = _normalize_ocr_number_token(raw_value.replace("บาท", "").strip())
    return normalized_value or None


def _build_judgment_hit_from_page(page: dict[str, object]) -> dict[str, object] | None:
    page_number = int(page.get("page_number", 0))
    raw_segments = page.get("segments") or []
    if not isinstance(raw_segments, list) or not raw_segments:
        return None

    compact_keywords = tuple(_compact_for_compare(keyword) for keyword in JUDGMENT_KEYWORDS)
    compact_noise_markers = tuple(_compact_for_compare(marker) for marker in JUDGMENT_NOISE_MARKERS)
    compact_stop_markers = tuple(_compact_for_compare(marker) for marker in JUDGMENT_STOP_MARKERS)
    compact_end_markers = tuple(_compact_for_compare(marker) for marker in JUDGMENT_END_KEYWORDS)
    compact_fallback_end_markers = tuple(
        _compact_for_compare(marker) for marker in JUDGMENT_OCR_FALLBACK_END_MARKERS
    )
    anchor_index: int | None = None
    for index, raw_segment in enumerate(raw_segments):
        if not isinstance(raw_segment, dict):
            continue
        segment_text = _compact_for_compare(_get_segment_text(raw_segment))
        if not segment_text:
            continue
        if any(keyword in segment_text for keyword in compact_keywords):
            anchor_index = index
            break

    if anchor_index is None:
        return None

    selected_segments: list[dict[str, object]] = []
    selected_compact_parts: list[str] = []
    accumulated_chars = 0
    for raw_segment in raw_segments[anchor_index:]:
        if not isinstance(raw_segment, dict):
            continue
        segment_text = _get_segment_text(raw_segment)
        if not segment_text:
            continue
        compact_segment_text = _compact_for_compare(segment_text)
        if any(marker in compact_segment_text for marker in compact_noise_markers):
            continue
        if selected_segments and any(marker in compact_segment_text for marker in compact_stop_markers):
            break
        selected_segments.append(raw_segment)
        selected_compact_parts.append(compact_segment_text)
        accumulated_chars += len(segment_text)
        compact_selected_text = "".join(selected_compact_parts)
        if any(marker and marker in compact_selected_text for marker in compact_end_markers):
            break
        if any(marker and marker in compact_selected_text for marker in compact_fallback_end_markers):
            break
        if len(selected_segments) >= 18 or accumulated_chars >= 1400:
            break

    bboxes: list[tuple[float, float, float, float]] = []
    display_parts: list[str] = []
    for raw_segment in selected_segments:
        bbox = raw_segment.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                bboxes.append(tuple(float(part) for part in bbox))
            except (TypeError, ValueError):
                pass
        display_parts.append(_get_segment_text(raw_segment))

    merged_bbox = _merge_bboxes(bboxes)
    segment_display_text = _normalize_review_text(" ".join(part for part in display_parts if part)).strip()
    segment_display_text = _slice_review_text_between_keywords(
        segment_display_text,
        start_keywords=JUDGMENT_START_KEYWORDS,
        end_keywords=JUDGMENT_END_KEYWORDS,
        fallback_end_keywords=JUDGMENT_OCR_FALLBACK_END_MARKERS,
    )
    segment_display_text = _normalize_judgment_end_marker(segment_display_text)
    raw_display_text = _build_judgment_display_text_from_page(page)
    display_text = (
        raw_display_text
        if _contains_any_review_keyword(raw_display_text, ("พิพากษาให้จำเลย", "พิพากษาให้จำเลบย"))
        else segment_display_text
    )
    if not display_text:
        return None
    if not _contains_any_review_keyword(display_text, JUDGMENT_START_KEYWORDS):
        return None

    return {
        "id": f"page-{page_number}-judgment-1",
        "pageNumber": page_number,
        "text": display_text,
        "displayText": display_text,
        "bbox": list(merged_bbox) if merged_bbox is not None else None,
    }


def _judgment_text_has_end_marker(text: str) -> bool:
    return _contains_any_review_keyword(text, JUDGMENT_END_KEYWORDS) or _contains_any_review_keyword(
        text,
        JUDGMENT_OCR_FALLBACK_END_MARKERS,
    )


def _is_judgment_continuation_noise(segment_text: str) -> bool:
    normalized_text = _normalize_review_text(segment_text)
    compact_text = _compact_for_compare(normalized_text)
    if not compact_text:
        return True
    if compact_text in {"สำหรับ", "ศาลใช้", "สำหรับศาลใช้"}:
        return True
    if re.fullmatch(r"\(?[0-9๐-๙]+\s*พ\.\)?", normalized_text):
        return True
    if re.fullmatch(r"[0-9๐-๙]+\)", normalized_text):
        return True
    return False


def _build_judgment_continuation_hit_from_page(page: dict[str, object]) -> dict[str, object] | None:
    page_number = int(page.get("page_number", 0))
    raw_segments = page.get("segments") or []
    if not isinstance(raw_segments, list) or not raw_segments:
        return None

    compact_noise_markers = tuple(_compact_for_compare(marker) for marker in JUDGMENT_NOISE_MARKERS)
    compact_stop_markers = tuple(_compact_for_compare(marker) for marker in JUDGMENT_STOP_MARKERS)
    compact_end_markers = tuple(_compact_for_compare(marker) for marker in JUDGMENT_END_KEYWORDS)
    compact_fallback_end_markers = tuple(
        _compact_for_compare(marker) for marker in JUDGMENT_OCR_FALLBACK_END_MARKERS
    )

    selected_segments: list[dict[str, object]] = []
    selected_compact_parts: list[str] = []
    accumulated_chars = 0
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict):
            continue
        segment_text = _get_segment_text(raw_segment)
        if not segment_text:
            continue
        compact_segment_text = _compact_for_compare(segment_text)
        if not selected_segments and _is_judgment_continuation_noise(segment_text):
            continue
        if any(marker in compact_segment_text for marker in compact_noise_markers):
            continue
        if selected_segments and any(marker in compact_segment_text for marker in compact_stop_markers):
            break

        selected_segments.append(raw_segment)
        selected_compact_parts.append(compact_segment_text)
        accumulated_chars += len(segment_text)
        compact_selected_text = "".join(selected_compact_parts)
        if any(marker and marker in compact_selected_text for marker in compact_end_markers):
            break
        if any(marker and marker in compact_selected_text for marker in compact_fallback_end_markers):
            break
        if len(selected_segments) >= 18 or accumulated_chars >= 1400:
            break

    if not selected_segments:
        return None

    bboxes: list[tuple[float, float, float, float]] = []
    display_parts: list[str] = []
    for raw_segment in selected_segments:
        bbox = raw_segment.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                bboxes.append(tuple(float(part) for part in bbox))
            except (TypeError, ValueError):
                pass
        display_parts.append(_get_segment_text(raw_segment))

    merged_bbox = _merge_bboxes(bboxes)
    segment_display_text = _normalize_judgment_end_marker(
        _normalize_review_text(" ".join(part for part in display_parts if part)).strip()
    )
    raw_display_text = _build_judgment_continuation_display_text_from_page(page)
    display_text = raw_display_text or segment_display_text
    if not display_text:
        return None

    return {
        "id": f"page-{page_number}-judgment-continuation-1",
        "pageNumber": page_number,
        "text": display_text,
        "displayText": display_text,
        "bbox": list(merged_bbox) if merged_bbox is not None else None,
    }


def _build_judgment_hits_from_pages(sorted_pages: list[dict[str, object]]) -> list[dict[str, object]]:
    for index, page in enumerate(sorted_pages):
        hit = _build_judgment_hit_from_page(page)
        if hit is None:
            continue

        hits = [hit]
        if _judgment_text_has_end_marker(str(hit.get("text") or "")):
            return hits

        for continuation_page in sorted_pages[index + 1 :]:
            continuation_hit = _build_judgment_continuation_hit_from_page(continuation_page)
            if continuation_hit is None:
                continue
            hits.append(continuation_hit)
            if _judgment_text_has_end_marker(str(continuation_hit.get("text") or "")):
                break
        return hits

    return []


def _find_judgment_hit_by_keywords(
    judgment_hits: list[dict[str, object]],
    keywords: tuple[str, ...],
) -> dict[str, object] | None:
    compact_keywords = tuple(_compact_for_compare(keyword) for keyword in keywords if keyword)
    for hit in judgment_hits:
        compact_text = _compact_for_compare(str(hit.get("text") or ""))
        if compact_text and any(keyword in compact_text for keyword in compact_keywords):
            return hit
    return judgment_hits[0] if judgment_hits else None


def _get_review_hit_bbox(hit: dict[str, object] | None) -> tuple[float, float, float, float] | None:
    if hit is None or not isinstance(hit.get("bbox"), list):
        return None
    try:
        return tuple(float(part) for part in hit["bbox"])
    except (TypeError, ValueError):
        return None


def _get_review_hit_page(hit: dict[str, object] | None) -> int | None:
    if hit is None:
        return None
    page_number = hit.get("pageNumber")
    return page_number if isinstance(page_number, int) else None


def _build_review_data(
    pages: list[dict[str, object]],
    settings: Settings,
    *,
    document_category: str | None = None,
) -> dict[str, object]:
    if is_tr_document_category(document_category):
        return build_tr_review_data(pages)

    sorted_pages = sorted(pages, key=lambda page: int(page.get("page_number", 0)))
    first_page = sorted_pages[0] if sorted_pages else None
    first_page_markdown = _get_page_text_for_review(first_page) if first_page is not None else ""
    full_text = _normalize_review_text(" ".join(_get_page_text_for_review(page) for page in sorted_pages))
    can_use_vision = (
        settings.vision_ready
        and _has_usable_review_text(sorted_pages)
        and _vision_endpoint_connectivity_error(settings) is None
    )
    header_extraction_errors: list[str] = []

    lines = [_strip_ocr_line_decorators(line) for line in first_page_markdown.splitlines()]
    lines = [line for line in lines if line]
    case_black_value = _extract_case_header_value(lines, CASE_BLACK_MARKERS)
    case_red_value = _extract_case_header_value(lines, CASE_RED_MARKERS)
    court_name_value = _extract_court_name_from_markdown(first_page_markdown)

    resolved_case_fields: dict[str, dict[str, object]] = {}
    if first_page is not None:
        resolved_case_fields, header_extraction_errors = _resolve_first_page_case_fields(
            first_page,
            first_page_markdown=first_page_markdown,
            settings=settings,
        )
        resolved_black_value = str(resolved_case_fields.get("caseBlackNo", {}).get("value") or "").strip() or None
        resolved_red_value = str(resolved_case_fields.get("caseRedNo", {}).get("value") or "").strip() or None
        if resolved_black_value:
            case_black_value = resolved_black_value
        if resolved_red_value:
            case_red_value = resolved_red_value

    pay_amount = _pick_pay_amount(full_text)
    interest_rate = _pick_first_pattern(
        full_text,
        [
            re.compile(r"อัตราร้อยละ\s*([0-9๐-๙,\.]+)"),
            re.compile(r"รายละเอียด\s*([0-9๐-๙,\.]+)\s*ต่อปี"),
            re.compile(r"([0-9๐-๙,\.]+)\s*ต่อปี"),
        ],
    )
    principal_amount = _pick_first_pattern(
        full_text,
        [re.compile(r"(?:ของต้นเงิน|ของเงินต้น)\s*([0-9๐-๙ด,\.]+)")],
    )
    filing_date = _pick_filing_date(full_text)
    attorney_fee = _pick_first_pattern(
        full_text,
        [re.compile(r"ค่าทนายความ\s*[\/\\|]?\s*([0-9๐-๙,\.]+)")],
    )

    vision_fields: dict[str, dict[str, object]] = {}
    vision_errors: list[str] = []
    if first_page is not None and can_use_vision:
        vision_fields, vision_errors = _extract_header_fields_with_vision(first_page, settings)
        vision_case_black_value = str(vision_fields.get("caseBlackNo", {}).get("value") or "").strip() or None
        vision_case_red_value = str(vision_fields.get("caseRedNo", {}).get("value") or "").strip() or None
        vision_court_name_value = str(vision_fields.get("courtName", {}).get("value") or "").strip() or None
        if not case_black_value and _looks_like_case_number(vision_case_black_value):
            case_black_value = vision_case_black_value
        if not case_red_value and _looks_like_case_number(vision_case_red_value):
            case_red_value = vision_case_red_value
        if not court_name_value and vision_court_name_value:
            court_name_value = vision_court_name_value
    vision_errors.extend(header_extraction_errors)

    judgment_hits = _build_judgment_hits_from_pages(sorted_pages)
    if not judgment_hits and can_use_vision:
        for page in sorted_pages:
            if int(page.get("page_number", 0)) <= 1:
                continue
            hit = _extract_judgment_hit_with_vision_crop(page, settings)
            if hit is not None:
                judgment_hits.append(hit)
                break

    pay_amount_hit = _find_judgment_hit_by_keywords(
        judgment_hits,
        ("ชำระเงิน", "ใช้ราคาแทนเป็นเงิน", "ราคาแทน"),
    )
    interest_rate_hit = _find_judgment_hit_by_keywords(judgment_hits, ("ดอกเบี้ย", "ร้อยละ"))
    principal_amount_hit = _find_judgment_hit_by_keywords(judgment_hits, ("ต้นเงิน", "เงินต้น"))
    filing_date_hit = _find_judgment_hit_by_keywords(judgment_hits, ("วันฟ้อง", "ฟ้องวันที่", "พ้องวันที่"))
    attorney_fee_hit = _find_judgment_hit_by_keywords(judgment_hits, ("ค่าทนายความ",))
    primary_hit_text = (
        _normalize_review_text(" ".join(str(hit.get("text") or "") for hit in judgment_hits))
        if judgment_hits
        else ""
    )
    if primary_hit_text:
        if not pay_amount:
            pay_amount = _pick_pay_amount(primary_hit_text)
        if not interest_rate:
            interest_rate = _pick_first_pattern(
                primary_hit_text,
                [
                    re.compile(r"อัตราร้อยละ\s*([0-9๐-๙,\.]+)"),
                    re.compile(r"รายละเอียด\s*([0-9๐-๙,\.]+)\s*ต่อปี"),
                    re.compile(r"([0-9๐-๙,\.]+)\s*ต่อปี"),
                ],
            )
        if not principal_amount:
            principal_amount = _pick_first_pattern(
                primary_hit_text,
                [re.compile(r"(?:ของต้นเงิน|ของเงินต้น)\s*([0-9๐-๙ด,\.]+)")],
            )
        if not filing_date:
            filing_date = _pick_filing_date(primary_hit_text)
        if not attorney_fee:
            attorney_fee = _pick_first_pattern(
                primary_hit_text,
                [re.compile(r"ค่าทนายความ\s*[\/\\|]?\s*([0-9๐-๙,\.]+)")],
            )
    if not attorney_fee and can_use_vision:
        for page in sorted_pages:
            if int(page.get("page_number", 0)) <= 1:
                continue
            attorney_fee = _extract_attorney_fee_with_vision_crop(page, settings)
            if attorney_fee:
                break
    pay_amount = _normalize_ocr_number_token(pay_amount)
    interest_rate = _normalize_ocr_number_token(interest_rate)
    principal_amount = _normalize_ocr_number_token(principal_amount)
    attorney_fee = _normalize_ocr_number_token(attorney_fee)

    provider_header_anchors: dict[str, dict[str, object]] = {}
    if first_page is not None:
        anchor_lines = _detect_page_anchor_lines(first_page, settings)
        provider_header_anchors = _resolve_header_anchors_from_provider(
            anchor_lines,
            case_black_value=case_black_value,
            case_red_value=case_red_value,
            court_name=court_name_value,
        )

    case_black_bbox = None
    case_black_source = "regex"
    if "caseBlackNo" in provider_header_anchors:
        case_black_bbox = provider_header_anchors["caseBlackNo"].get("bbox")
        case_black_source = str(provider_header_anchors["caseBlackNo"].get("source") or "anchor_provider")
    elif "caseBlackNo" in resolved_case_fields and _values_compatible(
        str(resolved_case_fields["caseBlackNo"].get("value") or "").strip() or None,
        case_black_value,
    ):
        case_black_bbox = resolved_case_fields["caseBlackNo"].get("bbox")
        case_black_source = str(resolved_case_fields["caseBlackNo"].get("source") or "hybrid_case_consensus")
    elif "caseBlackNo" in vision_fields and _values_compatible(
        str(vision_fields["caseBlackNo"].get("value") or "").strip() or None,
        case_black_value,
    ):
        case_black_bbox = vision_fields["caseBlackNo"].get("bbox")
        case_black_source = str(vision_fields["caseBlackNo"].get("source") or "qwen_header")
    elif first_page is not None:
        case_black_bbox = _find_segment_anchor_by_value(first_page, case_black_value)
        if case_black_bbox is None:
            case_black_bbox = FIRST_PAGE_FALLBACK_ANCHORS["caseBlackNo"]
            case_black_source = "fallback_header"

    case_red_bbox = None
    case_red_source = "regex"
    if "caseRedNo" in provider_header_anchors:
        case_red_bbox = provider_header_anchors["caseRedNo"].get("bbox")
        case_red_source = str(provider_header_anchors["caseRedNo"].get("source") or "anchor_provider")
    elif "caseRedNo" in resolved_case_fields and _values_compatible(
        str(resolved_case_fields["caseRedNo"].get("value") or "").strip() or None,
        case_red_value,
    ):
        case_red_bbox = resolved_case_fields["caseRedNo"].get("bbox")
        case_red_source = str(resolved_case_fields["caseRedNo"].get("source") or "hybrid_case_consensus")
    elif "caseRedNo" in vision_fields and _values_compatible(
        str(vision_fields["caseRedNo"].get("value") or "").strip() or None,
        case_red_value,
    ):
        case_red_bbox = vision_fields["caseRedNo"].get("bbox")
        case_red_source = str(vision_fields["caseRedNo"].get("source") or "qwen_header")
    elif first_page is not None:
        case_red_bbox = _find_segment_anchor_by_value(first_page, case_red_value)
        if case_red_bbox is None:
            case_red_bbox = FIRST_PAGE_FALLBACK_ANCHORS["caseRedNo"]
            case_red_source = "fallback_header"

    court_bbox = None
    court_source = "regex"
    if "courtName" in provider_header_anchors:
        court_bbox = provider_header_anchors["courtName"].get("bbox")
        court_source = str(provider_header_anchors["courtName"].get("source") or "anchor_provider")
    elif "courtName" in vision_fields and (
        not court_name_value
        or _values_compatible(
            str(vision_fields["courtName"].get("value") or "").strip() or None,
            court_name_value,
        )
    ):
        court_bbox = vision_fields["courtName"].get("bbox")
        court_source = str(vision_fields["courtName"].get("source") or "qwen_header")
    elif first_page is not None:
        court_bbox = _find_segment_anchor_by_value(first_page, court_name_value)
        if court_bbox is None:
            court_bbox = _find_segment_anchor_by_keyword(first_page, ("ศาล",))
        if court_bbox is None:
            court_bbox = FIRST_PAGE_FALLBACK_ANCHORS["courtName"]
            court_source = "fallback_header"

    return {
        "version": 1,
        "visionModel": settings.vision_model if settings.vision_ready else None,
        "visionErrors": vision_errors,
        "fields": {
            "caseBlackNo": _make_review_field(
                value=case_black_value,
                page_number=1 if case_black_value else None,
                bbox=case_black_bbox,
                source=case_black_source if case_black_value else None,
            ),
            "caseRedNo": _make_review_field(
                value=case_red_value,
                page_number=1 if case_red_value else None,
                bbox=case_red_bbox,
                source=case_red_source if case_red_value else None,
            ),
            "courtName": _make_review_field(
                value=court_name_value,
                page_number=1 if court_name_value else None,
                bbox=court_bbox,
                source=court_source if court_name_value else None,
            ),
            "payAmount": _make_review_field(
                value=pay_amount,
                page_number=_get_review_hit_page(pay_amount_hit) if pay_amount else None,
                bbox=_get_review_hit_bbox(pay_amount_hit),
                source="judgment_hit" if pay_amount and _get_review_hit_bbox(pay_amount_hit) is not None else None,
            ),
            "interestRate": _make_review_field(
                value=interest_rate,
                page_number=_get_review_hit_page(interest_rate_hit) if interest_rate else None,
                bbox=_get_review_hit_bbox(interest_rate_hit),
                source="judgment_hit" if interest_rate and _get_review_hit_bbox(interest_rate_hit) is not None else None,
            ),
            "principalAmount": _make_review_field(
                value=principal_amount,
                page_number=_get_review_hit_page(principal_amount_hit) if principal_amount else None,
                bbox=_get_review_hit_bbox(principal_amount_hit),
                source="judgment_hit" if principal_amount and _get_review_hit_bbox(principal_amount_hit) is not None else None,
            ),
            "filingDate": _make_review_field(
                value=filing_date,
                page_number=_get_review_hit_page(filing_date_hit) if filing_date else None,
                bbox=_get_review_hit_bbox(filing_date_hit),
                source="judgment_hit" if filing_date and _get_review_hit_bbox(filing_date_hit) is not None else None,
            ),
            "attorneyFee": _make_review_field(
                value=attorney_fee,
                page_number=_get_review_hit_page(attorney_fee_hit) if attorney_fee else None,
                bbox=_get_review_hit_bbox(attorney_fee_hit),
                source="judgment_hit" if attorney_fee and _get_review_hit_bbox(attorney_fee_hit) is not None else None,
            ),
        },
        "keywordHits": judgment_hits,
    }


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
    document_category = str(document.get("document_category") or DEFAULT_DOCUMENT_CATEGORY)

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
    review_data = _build_review_data(
        rebuilt_pages,
        settings,
        document_category=document_category,
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
                "review_data": review_data,
                "ocr_pipeline_version": _pipeline_version_for_category(document_category),
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
    document_category: str | None = None,
) -> dict[str, object]:
    normalized_pages = [_with_page_ocr_fields(page_asset) for page_asset in page_assets]
    total_pages = len(normalized_pages)
    pipeline_version = _pipeline_version_for_category(document_category)
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
            "review_data": None,
            "ocr_pipeline_version": pipeline_version,
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
            "review_data": None,
            "ocr_pipeline_version": pipeline_version,
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
            "review_data": None,
            "ocr_pipeline_version": pipeline_version,
        }

    endpoint_error = _ocr_endpoint_connectivity_error(settings)
    if endpoint_error:
        _log_import_event(f"OCR aborted for {source_file_path.name}: {endpoint_error}")
        return {
            "pages": normalized_pages,
            "ocr_markdown": None,
            "raw_ocr_markdown": None,
            "corrected_ocr_markdown": None,
            "original_ocr_markdown": None,
            "cleaned_ocr_markdown": None,
            "correction_model": None,
            "ocr_error_message": endpoint_error,
            "ocr_completed_at": None,
            "review_data": None,
            "ocr_pipeline_version": pipeline_version,
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
        original_page_path = Path(str(page_asset.get("original_preview_path") or source_file_path))
        cleaned_page_path = Path(str(page_asset.get("cleaned_preview_path") or cleaned_file_path))
        original_ocr_path = original_page_path if original_page_path.exists() else source_file_path
        cleaned_ocr_path = cleaned_page_path if cleaned_page_path.exists() else cleaned_file_path
        page_failed = False
        try:
            if is_tr_document_category(document_category):
                raw_markdown = run_ocr_page(
                    cleaned_ocr_path,
                    page_number,
                    settings,
                    source_is_cleaned=True,
                )
                selected_source = "cleaned"
                selected_score = None
                selected_ocr_model = settings.ocr_model
                selected_candidate_source = "cleaned"
                ocr_candidate_scores = []
                original_markdown = None
                cleaned_markdown = raw_markdown
                original_score = None
                cleaned_score = None
                original_error = None
                cleaned_error = None
                diff_similarity = None
                suspicious_reasons = [
                    "TR OCR used the category-specific cleaned image only, without rerunning the watermarked original.",
                ]
            else:
                comparison = compare_ocr_page_sources(
                    original_file_path=original_ocr_path,
                    cleaned_file_path=cleaned_ocr_path,
                    page_number=page_number,
                    settings=settings,
                )
                raw_markdown = str(comparison.get("selected_markdown") or "")
                selected_source = str(comparison.get("selected_source") or "cleaned")
                selected_score = (
                    float(comparison["selected_score"])
                    if isinstance(comparison.get("selected_score"), (int, float))
                    else None
                )
                selected_ocr_model = (
                    str(comparison["selected_ocr_model"])
                    if isinstance(comparison.get("selected_ocr_model"), str)
                    else settings.ocr_model
                )
                selected_candidate_source = (
                    str(comparison["selected_candidate_source"])
                    if isinstance(comparison.get("selected_candidate_source"), str)
                    else selected_source
                )
                ocr_candidate_scores = [
                    dict(candidate)
                    for candidate in comparison.get("candidate_scores", [])
                    if isinstance(candidate, dict)
                ]
                original_markdown = (
                    str(comparison["original_markdown"])
                    if isinstance(comparison.get("original_markdown"), str)
                    else None
                )
                cleaned_markdown = (
                    str(comparison["cleaned_markdown"])
                    if isinstance(comparison.get("cleaned_markdown"), str)
                    else None
                )
                original_score = (
                    float(comparison["original_score"])
                    if isinstance(comparison.get("original_score"), (int, float))
                    else None
                )
                cleaned_score = (
                    float(comparison["cleaned_score"])
                    if isinstance(comparison.get("cleaned_score"), (int, float))
                    else None
                )
                original_error = (
                    str(comparison["original_error"])
                    if isinstance(comparison.get("original_error"), str)
                    else None
                )
                cleaned_error = (
                    str(comparison["cleaned_error"])
                    if isinstance(comparison.get("cleaned_error"), str)
                    else None
                )
                diff_similarity = (
                    float(comparison["diff_similarity"])
                    if isinstance(comparison.get("diff_similarity"), (int, float))
                    else None
                )
                suspicious_reasons = [
                    str(reason)
                    for reason in comparison.get("suspicious_reasons", [])
                    if isinstance(reason, str) and reason.strip()
                ]
            ocr_seconds = perf_counter() - page_start
            if not is_tr_document_category(document_category):
                raw_markdown, header_crop_reason = _prepend_first_page_case_header_if_needed(
                    page_asset=page_asset,
                    page_number=page_number,
                    markdown=raw_markdown,
                    settings=settings,
                )
                if header_crop_reason:
                    if selected_source == "original":
                        original_markdown = raw_markdown
                    else:
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
            selected_ocr_model = settings.ocr_model
            selected_candidate_source = selected_source
            ocr_candidate_scores = []
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
                selected_ocr_model=selected_ocr_model,
                selected_candidate_source=selected_candidate_source,
                ocr_candidate_scores=ocr_candidate_scores,
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
    review_data = _build_review_data(
        enriched_pages,
        settings,
        document_category=document_category,
    )
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
        "review_data": review_data,
        "ocr_pipeline_version": pipeline_version,
    }


def _refresh_existing_import_if_needed(
    document: dict[str, object],
    settings: Settings,
) -> dict[str, object]:
    needs_ocr = _import_needs_ocr(document)
    pipeline_version = _document_pipeline_version(document)
    target_pipeline_version = _document_target_pipeline_version(document)

    if not needs_ocr and pipeline_version >= target_pipeline_version:
        _log_import_event(
            f"reusing cached OCR for {document.get('source_filename', 'unknown file')}"
        )
        return document

    if (
        not needs_ocr
        and pipeline_version < target_pipeline_version
        and _can_rebuild_segments_from_cached_ocr(document)
    ):
        _log_import_event(
            f"rebuilding cached review blocks for {document.get('source_filename', 'unknown file')}"
        )
        return _rebuild_import_segments_from_cached_ocr(document)

    if (
        needs_ocr
        and pipeline_version >= target_pipeline_version
        and _can_rebuild_segments_from_cached_ocr(document)
    ):
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
        document_category=str(document.get("document_category") or DEFAULT_DOCUMENT_CATEGORY),
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
        "review_data": None,
        "ocr_pipeline_version": 0,
    }


def _build_cleaned_assets(
    stored_original_path: Path,
    derived_dir: Path,
    total_pages: int,
    *,
    document_category: str | None = None,
) -> tuple[Path, list[dict[str, object]]]:
    page_assets: list[dict[str, object]] = []
    cleaned_images: list[Image.Image] = []
    source_pdf = fitz.open(stored_original_path) if stored_original_path.suffix.lower() == ".pdf" else None
    uses_tr_cleaning = is_tr_document_category(document_category)

    try:
        for page_number in range(1, total_pages + 1):
            original_preview = render_page_preview(stored_original_path, page_number)
            cleaning_metadata: dict[str, object] = {}
            if uses_tr_cleaning:
                tr_clean_result = build_tr_cleaned_image(original_preview)
                cleaned_preview = tr_clean_result.image
                cleaning_metadata = {
                    "watermark_detected": tr_clean_result.analysis.detected,
                    "watermark_score": tr_clean_result.analysis.score,
                    "cleaning_mode": tr_clean_result.cleaning_mode,
                }
            else:
                cleaned_preview = (
                    clean_page_to_image(source_pdf[page_number - 1], dpi=DEFAULT_RENDER_DPI)
                    if source_pdf is not None
                    else clean_pil_image(original_preview)
                )
                cleaning_metadata = {
                    "watermark_detected": None,
                    "watermark_score": None,
                    "cleaning_mode": "default_watermark_cleaned",
                }

            original_preview_path = derived_dir / f"page-{page_number:03d}-original.png"
            cleaned_preview_path = derived_dir / f"page-{page_number:03d}-cleaned.png"
            original_preview.save(original_preview_path, format="PNG")
            cleaned_preview.save(cleaned_preview_path, format="PNG")

            page_assets.append(
                {
                    "page_number": page_number,
                    "original_preview_path": str(original_preview_path),
                    "cleaned_preview_path": str(cleaned_preview_path),
                    **cleaning_metadata,
                }
            )
            cleaned_images.append(cleaned_preview.copy())
    finally:
        if source_pdf is not None:
            source_pdf.close()

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
        category_changed = (
            _normalize_document_category(str(existing.get("document_category") or ""))
            != normalized_document_category
        )
        if category_changed:
            update_fields["document_category"] = normalized_document_category

        if category_changed or _document_needs_background_ocr(existing):
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
            document_category=str(document.get("document_category") or DEFAULT_DOCUMENT_CATEGORY),
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

    try:
        ocr_payload = _generate_import_ocr_payload(
            source_file_path=source_file_path,
            cleaned_file_path=cleaned_file_path,
            page_assets=page_assets,
            settings=active_settings,
            document_category=str(document.get("document_category") or DEFAULT_DOCUMENT_CATEGORY),
        )
    except Exception as exc:
        _log_import_event(
            f"OCR post-processing failed for {document.get('source_filename', import_id)}: {exc}"
        )
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
