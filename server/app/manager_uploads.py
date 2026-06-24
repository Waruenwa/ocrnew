from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.auth_service import (
    AuthenticatedUser,
    StaffUser,
    list_staff_users_from_auth_source,
    require_manager_user,
    require_staff_user,
)
from app.core.config import BASE_DIR, load_settings
from app.core.database import get_imports_collection
from app.schemas import ImportRecord, ImportStatus
from app.tr_corrections import learn_tr_name_corrections_from_review
from app.tr_location_reference import validate_and_correct_tr_address
from app.tr_review import (
    TR_ADDRESS_HOUSE_NUMBER_PATTERN,
    TR_MONTH_NAMES,
    TR_PERSON_ID_PATTERN,
    TR_REQUIRED_FIELD_KEYS,
    TR_REVIEW_VERSION,
    build_tr_review_data,
    get_tr_field_template,
    normalize_tr_field_value,
    validate_tr_field_value,
)
from app.tr_upttr_sql import insert_tr_upttr_from_review_result
from app.tr_watermark_cleaner import build_tr_cleaned_image, detect_tr_watermark
from app.typhoon import compare_ocr_page_sources, render_page_preview, run_ocr_page, run_vision_json


SUPPORTED_DOCUMENT_TYPE = "TR"
MAX_FILES_PER_BATCH = 20
MAX_PAGES_PER_PDF = 50
MAX_RECORDS_PER_BATCH = 100
DATA_STORAGE_DIR = BASE_DIR / "data_storage"
INCOMING_DIR = DATA_STORAGE_DIR / "incoming"
ORIGINAL_ROOT = INCOMING_DIR / "original"
DERIVED_ROOT = INCOMING_DIR / "derived"
METADATA_ROOT = INCOMING_DIR / "metadata"
DERIVED_SUBFOLDERS = ("pages", "watermark_cleaned", "previews", "ocr")

router = APIRouter(prefix="/api/manager", tags=["manager uploads"])
staff_router = APIRouter(prefix="/api/staff", tags=["staff assignments"])


class ManagerUploadRecordResponse(BaseModel):
    record_id: str
    record_no: str | None = None
    batch_id: str
    file_id: str
    original_filename: str
    selected_document_type: str
    page_number: int
    original_path: str
    derived_root: str
    page_asset_path: str | None
    cleaned_page_path: str | None = None
    has_watermark: bool | None = None
    ocr_text: str | None = None
    ocr_result: str | None = None
    ocr_error: str | None = None
    processing_timing: dict[str, Any] | None = None
    ocr_current_stage: dict[str, Any] | None = None
    processed_at: str | None = None
    ocr_status: str
    review_status: str
    assigned_to_user_id: str | None
    assigned_to_username: str | None = None
    assigned_by_user_id: str | None = None
    assigned_by_username: str | None = None
    assigned_at: str | None = None
    save_btn: str | None = "N"
    created_at: str


class ManagerUploadFileResponse(BaseModel):
    file_id: str
    original_filename: str
    stored_filename: str
    mime_type: str | None
    file_size_bytes: int
    page_count: int | None
    original_path: str
    derived_root: str
    status: str
    error_message: str | None = None


class ManagerUploadBatchResponse(BaseModel):
    batch_id: str
    selected_document_type: str
    status: str
    file_count: int
    total_pages: int
    record_count: int
    ocr_pending_count: int = 0
    ocr_processing_count: int = 0
    ocr_succeeded_count: int = 0
    ocr_failed_count: int = 0
    ready_to_assign_count: int = 0
    assigned_count: int = 0
    unassigned_count: int = 0
    in_review_count: int = 0
    completed_count: int = 0
    files: list[ManagerUploadFileResponse]
    records: list[ManagerUploadRecordResponse]


class ManagerDashboardRecordResponse(BaseModel):
    record_id: str
    record_no: str | None = None
    batch_id: str
    file_id: str
    original_filename: str
    selected_document_type: str
    page_number: int
    ocr_status: str
    review_status: str
    has_watermark: bool | None = None
    ocr_error: str | None = None
    processed_at: str | None = None
    assigned_to_user_id: str | None = None
    assigned_to_username: str | None = None
    assigned_at: str | None = None
    created_at: str


class ManagerDashboardResponse(BaseModel):
    batch_count: int
    file_count: int
    record_count: int
    total_pages: int
    ocr_pending_count: int = 0
    ocr_processing_count: int = 0
    ocr_succeeded_count: int = 0
    ocr_failed_count: int = 0
    ready_to_assign_count: int = 0
    assigned_count: int = 0
    unassigned_count: int = 0
    in_review_count: int = 0
    completed_count: int = 0
    batches: list[ManagerUploadBatchResponse]
    records: list[ManagerDashboardRecordResponse]


class ManagerBatchOcrResponse(BaseModel):
    batch_id: str
    status: str
    total_processed: int
    succeeded_count: int
    failed_count: int
    pending_count: int
    processing_count: int = 0
    ready_to_assign_count: int = 0
    errors: list[dict[str, str]] = Field(default_factory=list)


class ManagerStaffUserResponse(BaseModel):
    user_id: str
    username: str
    display_name: str


class ManagerBatchAssignRequest(BaseModel):
    staff_user_id: str | None = None
    staff_username: str | None = None
    count: int = Field(gt=0)


class ManagerRecordsAssignRequest(BaseModel):
    record_ids: list[str] = Field(min_length=1)
    staff_user_id: str | None = None
    staff_username: str | None = None


class StaffAssignedRecordResponse(BaseModel):
    record_id: str
    record_no: str | None = None
    batch_id: str
    file_id: str
    original_filename: str
    selected_document_type: str
    page_number: int
    ocr_status: str
    review_status: str
    assigned_to_user_id: str | None = None
    assigned_to_username: str | None = None
    assigned_at: str | None = None
    processed_at: str | None = None


class StaffRecordDetailResponse(StaffAssignedRecordResponse):
    original_path: str
    derived_root: str
    page_asset_path: str | None = None
    cleaned_page_path: str | None = None
    ocr_input_path: str | None = None
    has_watermark: bool | None = None
    ocr_text: str | None = None
    ocr_result: str | None = None
    corrected_result: Any | None = None
    preview_url: str
    original_filename: str
    completed_at: str | None = None
    updated_at: str | None = None


class StaffRecordProgressPayload(BaseModel):
    corrected_result: Any | None = None


class StaffRecordCompletePayload(BaseModel):
    corrected_result: Any | None = None


def _relative_storage_path(path: Path) -> str:
    return path.relative_to(BASE_DIR).as_posix()


def _resolve_storage_path(path_value: str) -> Path:
    if not path_value:
        raise HTTPException(status_code=400, detail="Storage path is required")
    path = Path(path_value)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (BASE_DIR / path).resolve()
    base = BASE_DIR.resolve()
    if resolved != base and base not in resolved.parents:
        raise HTTPException(status_code=400, detail=f"Invalid storage path: {path_value}")
    return resolved


def _safe_original_filename(filename: str | None) -> str:
    return Path(filename or "upload.pdf").name or "upload.pdf"


def _is_pdf_upload(file: UploadFile) -> bool:
    filename = _safe_original_filename(file.filename)
    if Path(filename).suffix.lower() != ".pdf":
        return False

    content_type = (file.content_type or "").lower()
    return content_type in {"", "application/pdf", "application/x-pdf", "application/octet-stream"}


def _save_uploaded_file(file: UploadFile, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file.file.seek(0)
    with destination.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return destination.stat().st_size


def _count_pdf_pages(path: Path) -> int:
    reader = PdfReader(str(path))
    return len(reader.pages)


def _write_metadata(metadata_path: Path, metadata: dict[str, object]) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _metadata_path_for_batch(batch_id: str) -> Path:
    return METADATA_ROOT / batch_id / "metadata.json"


def _format_record_no(batch_id: str, sequence: int) -> str:
    batch_part = re.sub(r"[^A-Za-z0-9]+", "", batch_id).upper()[:6] or "BATCH"
    return f"TR-{batch_part}-{sequence:04d}"


def _ensure_metadata_record_numbers(metadata: dict[str, Any]) -> bool:
    records = metadata.get("records")
    if not isinstance(records, list):
        return False

    batch_id = str(metadata.get("batch_id") or "")
    changed = False
    seen: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        record_no = str(record.get("record_no") or "").strip()
        if not record_no or record_no in seen:
            record_no = _format_record_no(batch_id, index)
            record["record_no"] = record_no
            changed = True
        seen.add(record_no)
    return changed


def _load_batch_metadata(batch_id: str) -> dict[str, Any]:
    metadata_path = _metadata_path_for_batch(batch_id)
    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="Batch metadata not found")
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Batch metadata is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Batch metadata has an invalid format")
    if _ensure_metadata_record_numbers(payload):
        _write_metadata(metadata_path, payload)
    return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


TR_VISION_FIELD_KEYS = (
    "personId",
    "houseCode",
    "personName",
    "gender",
    "nationality",
    "birthDate",
    "age",
    "status",
    "motherName",
    "motherId",
    "motherNationality",
    "fatherName",
    "fatherId",
    "fatherNationality",
    "address",
    "moveInDate",
    "remark",
    "updateDate",
)

TR_VISION_VERIFY_FIELD_KEYS = {
}

TR_VISION_CANDIDATE_VERIFY_FIELD_KEYS: tuple[str, ...] = ()
TR_VISION_SLOT_RESCUE_FIELD_KEYS: tuple[str, ...] = (
    "personName",
    "motherName",
    "motherId",
    "fatherName",
    "fatherId",
    "motherNationality",
    "fatherNationality",
)
TR_VISION_ALWAYS_SLOT_READ_FIELD_KEYS = {
    "updateDate",
    "motherId",
    "motherNationality",
    "fatherId",
    "fatherNationality",
}
TR_VISION_NAME_FIELD_KEYS = {"personName", "motherName", "fatherName"}
TR_VISION_PARENT_NAME_FIELDS = {"motherName", "fatherName"}
TR_VISION_PRIMARY_NAME_FIELDS = {"personName"}
TR_VISION_ID_FIELD_KEYS = {"personId", "motherId", "fatherId"}
TR_VISION_PARENT_ID_FIELD_KEYS = {"motherId", "fatherId"}
TR_HUMAN_REVIEW_RESCUE_FIELD_KEYS = {
    "personId",
    "houseCode",
    "personName",
    "birthDate",
    "age",
    "motherName",
    "motherId",
    "fatherName",
    "fatherId",
    "address",
    "deceasedDate",
}
TR_VISION_VISION_VALUE_DISABLED_FIELD_KEYS = {
    "personId",
    "houseCode",
    "personName",
    "gender",
    "nationality",
    "birthDate",
    "status",
    "address",
    "moveInDate",
    "remark",
    "updateDate",
}
TR_VISION_LOCKED_FIELD_KEYS = set(TR_VISION_FIELD_KEYS)
TR_VISION_SOURCE_EMPTY_FIELD_KEYS: tuple[str, ...] = ()
TR_SOURCE_EMPTY_SOURCES = {"source_dash", "source_blank"}


def _manager_env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


TR_FIELD_RESCUE_MODES = {"off", "selective", "aggressive"}


def _manager_rescue_mode(mode_name: str, enabled_name: str, default: str = "off") -> str:
    raw_mode = os.getenv(mode_name)
    if raw_mode is not None and raw_mode.strip():
        mode = raw_mode.strip().lower()
        aliases = {
            "0": "off",
            "false": "off",
            "no": "off",
            "disabled": "off",
            "true": "selective",
            "yes": "selective",
            "on": "selective",
        }
        mode = aliases.get(mode, mode)
        return mode if mode in TR_FIELD_RESCUE_MODES else default
    if _manager_env_flag(enabled_name, False):
        return "aggressive"
    return default


def _rescue_mode_enabled(mode: str) -> bool:
    return mode in {"selective", "aggressive"}


TR_CROP_OCR_FIELD_KEYS: tuple[str, ...] = TR_VISION_FIELD_KEYS
TR_CROP_OCR_VOTE_FIELD_KEYS = {
    "personName",
    "motherName",
    "fatherName",
    "houseCode",
    "address",
    "moveInDate",
    "updateDate",
}

TR_VISION_LOCKED_BBOX_OVERRIDES = {
    "houseCode": (0.57, 0.247, 0.84, 0.292),
    "personName": (0.125, 0.286, 0.39, 0.319),
    "motherName": (0.12, 0.352, 0.30, 0.395),
    "fatherName": (0.12, 0.390, 0.30, 0.431),
    "address": (0.125, 0.421, 0.88, 0.482),
    "updateDate": (0.15, 0.64, 0.96, 0.82),
}

TR_VISION_PARENT_ROW_BBOX = {
    "motherName": (0.12, 0.352, 0.72, 0.389),
    "motherId": (0.12, 0.352, 0.72, 0.389),
    "motherNationality": (0.12, 0.352, 0.72, 0.389),
    "fatherName": (0.12, 0.390, 0.72, 0.438),
    "fatherId": (0.12, 0.390, 0.72, 0.438),
    "fatherNationality": (0.12, 0.390, 0.72, 0.438),
}

TR_VISION_FIELD_HINTS = {
    "personId": "Thai citizen ID. Prefer format 1-2345-67890-12-3 if visible.",
    "houseCode": "Thai house code. Prefer format 1234-123456-1 if visible.",
    "personName": "Person full name. Keep Thai title/name exactly; do not include ID/date.",
    "gender": "Gender. Return only male/female Thai value if visible.",
    "nationality": "Nationality. Return the visible nationality only.",
    "birthDate": "Birth date. Return Thai date exactly as visible.",
    "age": "Age. Return only the visible number.",
    "status": "Household status. Return only visible status text.",
    "motherName": "Mother name from this row only. Do not include father name, ID, date, or nationality. If more than one parent name is visible, return only the mother row value.",
    "motherId": "Mother Thai citizen ID. Return null if absent or shown as dash.",
    "motherNationality": "Mother nationality.",
    "fatherName": "Father name from this row only. Do not include mother name, ID, date, or nationality. If more than one parent name is visible, return only the father row value.",
    "fatherId": "Father Thai citizen ID. Return null if absent or shown as dash.",
    "fatherNationality": "Father nationality.",
    "address": "Address line from this row only. Keep visible Thai text exactly; do not include locality/registrar/footer rows below.",
    "moveInDate": "Move-in date. Return Thai date exactly as visible.",
    "remark": "Remark text. Keep visible Thai text exactly.",
    "updateDate": "Last update date. Return Thai date exactly as visible.",
}


def _compact_digits(value: str) -> str:
    translation = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
    return "".join(character for character in value.translate(translation) if character.isdigit())


def _format_tr_identifier_value(field_name: str, value: str) -> str:
    digits = _compact_digits(value)
    if field_name in {"personId", "motherId", "fatherId"} and len(digits) == 13:
        return f"{digits[0]}-{digits[1:5]}-{digits[5:10]}-{digits[10:12]}-{digits[12]}"
    if field_name == "houseCode" and len(digits) == 11:
        return f"{digits[:4]}-{digits[4:10]}-{digits[10]}"
    return value


def _normalize_vision_tr_value(field_name: str, value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or normalized.lower() in {"null", "none", "unknown", "not visible"}:
        return None
    if normalized in {"-", "#", "##########"}:
        return None
    formatted = _format_tr_identifier_value(field_name, normalized)
    if field_name in TR_VISION_ID_FIELD_KEYS and _is_suspicious_tr_id_value(formatted):
        return None
    return normalize_tr_field_value(field_name, formatted)


def _vision_bbox_for_tr_field(field_name: str) -> tuple[float, float, float, float] | None:
    override = TR_VISION_LOCKED_BBOX_OVERRIDES.get(field_name)
    if override is not None:
        return override
    template = get_tr_field_template(field_name)
    return template.bbox if template is not None else None


def _vision_crop_specs_for_tr_field(field_name: str) -> list[tuple[str, tuple[float, float, float, float], float, str]]:
    bbox = _vision_bbox_for_tr_field(field_name)
    if bbox is None:
        return []
    field_spec = (
        "field_crop",
        bbox,
        _vision_crop_padding_for_tr_field(field_name),
        "field_slot",
    )
    parent_row_bbox = TR_VISION_PARENT_ROW_BBOX.get(field_name)
    if parent_row_bbox is not None:
        if field_name in TR_VISION_PARENT_NAME_FIELDS:
            return [
                field_spec,
                ("field_crop_tall", _expand_tr_bbox(bbox, top=0.006, bottom=0.006), 0.002, "field_slot"),
                ("field_crop_right", _expand_tr_bbox(bbox, right=0.035), 0.002, "field_slot"),
                ("field_crop_left", _expand_tr_bbox(bbox, left=0.018), 0.002, "field_slot"),
            ]
        if field_name in TR_VISION_PARENT_ID_FIELD_KEYS:
            return [
                field_spec,
                ("parent_row_crop", parent_row_bbox, 0.0, "parent_row"),
            ]
        return [
            ("parent_row_crop", parent_row_bbox, 0.004, "parent_row"),
            field_spec,
        ]
    if field_name == "personName":
        return [
            field_spec,
            ("field_crop_tall", _expand_tr_bbox(bbox, top=0.006, bottom=0.006), 0.002, "field_slot"),
            ("field_crop_right", _expand_tr_bbox(bbox, right=0.035), 0.002, "field_slot"),
        ]
    if field_name == "houseCode":
        return [
            field_spec,
            ("field_crop_tall", _expand_tr_bbox(bbox, top=0.008, bottom=0.008), 0.003, "field_slot"),
            ("field_crop_wide", _expand_tr_bbox(bbox, left=0.018, right=0.050), 0.003, "field_slot"),
        ]
    if field_name == "address":
        address_number_bbox = (
            bbox[0],
            max(0.0, bbox[1] - 0.004),
            min(1.0, bbox[0] + 0.245),
            min(1.0, bbox[3] + 0.004),
        )
        return [
            ("address_number_crop", address_number_bbox, 0.004, "field_slot"),
            field_spec,
            ("field_crop_tall", _expand_tr_bbox(bbox, top=0.010, bottom=0.010), 0.004, "field_slot"),
            ("field_crop_wide", _expand_tr_bbox(bbox, left=0.015, right=0.030), 0.004, "field_slot"),
        ]
    if field_name in {"moveInDate", "updateDate"}:
        return [
            field_spec,
            ("field_crop_tall", _expand_tr_bbox(bbox, top=0.008, bottom=0.008), 0.003, "field_slot"),
            ("field_crop_wide", _expand_tr_bbox(bbox, left=0.018, right=0.030), 0.003, "field_slot"),
        ]
    return [
        (
            "field_crop",
            bbox,
            _vision_crop_padding_for_tr_field(field_name),
            "field_slot",
        )
    ]


def _vision_crop_padding_for_tr_field(field_name: str) -> float:
    if field_name == "updateDate":
        return 0.012
    if field_name == "houseCode":
        return 0.006
    if field_name in TR_VISION_LOCKED_FIELD_KEYS:
        return 0.003 if field_name != "address" else 0.006
    return 0.012


def _expand_tr_bbox(
    bbox: tuple[float, float, float, float],
    *,
    left: float = 0.0,
    top: float = 0.0,
    right: float = 0.0,
    bottom: float = 0.0,
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    return (
        max(0.0, x1 - left),
        max(0.0, y1 - top),
        min(1.0, x2 + right),
        min(1.0, y2 + bottom),
    )


def _crop_normalized_bbox(
    image: Image.Image,
    bbox: tuple[float, float, float, float],
    *,
    padding: float = 0.012,
) -> Image.Image:
    width, height = image.size
    left, top, right, bottom = bbox
    left_px = max(0, int((left - padding) * width))
    top_px = max(0, int((top - padding) * height))
    right_px = min(width, int((right + padding) * width))
    bottom_px = min(height, int((bottom + padding) * height))
    crop = image.crop((left_px, top_px, max(right_px, left_px + 1), max(bottom_px, top_px + 1)))
    scale = max(1.0, 900 / max(crop.width, 1), 180 / max(crop.height, 1))
    if scale > 1.0:
        crop = crop.resize(
            (int(crop.width * scale), int(crop.height * scale)),
            Image.Resampling.LANCZOS,
        )
    return crop


def _save_vision_debug_crop(
    *,
    debug_crop_dir: Path | None,
    field_name: str,
    source_label: str,
    spec_label: str,
    crop: Image.Image,
) -> str | None:
    if debug_crop_dir is None:
        return None

    safe_parts = [
        re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
        for value in (field_name, source_label, spec_label)
    ]
    filename = "__".join(part or "crop" for part in safe_parts) + ".png"
    try:
        debug_crop_dir.mkdir(parents=True, exist_ok=True)
        crop_path = debug_crop_dir / filename
        crop.save(crop_path, format="PNG")
        return _relative_storage_path(crop_path)
    except Exception:
        return None


def _tr_vision_read_prompt_for_field(field_name: str, crop_scope: str) -> str:
    if crop_scope == "parent_row":
        if field_name in TR_VISION_PARENT_ID_FIELD_KEYS:
            scope_instruction = (
                "The crop is exactly one parent row. It may include the parent name on the left, "
                "the ID slot or dash in the middle, and nationality on the right. "
                f"Return only the Thai citizen ID for {field_name}. "
                "If the ID slot for this row is a dash, blank, unclear, or not visible, return null. "
                "Do not copy an ID from the other parent row, the person row, or any neighboring row. "
            )
        elif field_name in TR_VISION_PARENT_NAME_FIELDS:
            scope_instruction = (
                "The crop is exactly one parent row. Return only the Thai parent name from the left name slot. "
                "Do not include the ID, dash, nationality, labels, or text from another row. "
            )
        elif field_name in {"motherNationality", "fatherNationality"}:
            scope_instruction = (
                "The crop is exactly one parent row. Return only the nationality from the right nationality slot. "
                "Do not include the name, ID, dash, labels, or text from another row. "
            )
        else:
            scope_instruction = "The crop is exactly one row. Return only this field from that row. "
    elif field_name == "updateDate":
        scope_instruction = (
            "The date parts may be visually separated across the same horizontal row inside this crop. "
            "For updateDate, combine the visible day number, Thai month name, and Buddhist year into one full Thai date. "
            "Do not return only the year. "
        )
    else:
        scope_instruction = "The crop is a locked field slot. Never copy text from adjacent rows or columns. "

    return (
        "You are reading one cropped field from a Thai TR fixed-layout form.\n"
        f"Field key: {field_name}\n"
        f"Field instruction: {TR_VISION_FIELD_HINTS.get(field_name, 'Read only the visible field value.')}\n"
        f"{scope_instruction}"
        "Return only JSON in this exact shape: {\"value\": string|null}.\n"
        "For Thai names, pay close attention to upper/lower vowels, tone marks, and similar letters. "
        "Do not normalize, romanize, autocorrect, or rewrite the name. "
        "Do not guess. If the value is not clearly visible, return null. "
        "Do not include labels, explanations, surrounding fields, or markdown."
    )


def _tr_field_value(review_data: dict[str, Any], field_name: str) -> str | None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return None
    field = fields.get(field_name)
    if not isinstance(field, dict):
        return None
    value = field.get("value")
    return str(value).strip() if value is not None and str(value).strip() else None


def _tr_field_source(review_data: dict[str, Any], field_name: str) -> str | None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return None
    field = fields.get(field_name)
    if not isinstance(field, dict):
        return None
    source = field.get("source")
    return str(source).strip() if source is not None and str(source).strip() else None


def _tr_field_empty_in_source(review_data: dict[str, Any], field_name: str) -> bool:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return False
    field = fields.get(field_name)
    if not isinstance(field, dict):
        return False
    source = str(field.get("source") or "")
    review_status = str(field.get("reviewStatus") or "")
    return source in TR_SOURCE_EMPTY_SOURCES or review_status == "empty_in_source"


def _tr_field_review_status(review_data: dict[str, Any], field_name: str) -> str:
    fields = review_data.get("fields")
    field = fields.get(field_name) if isinstance(fields, dict) else None
    if not isinstance(field, dict):
        return ""
    return str(field.get("reviewStatus") or "")


def _tr_field_has_alternatives(review_data: dict[str, Any], field_name: str) -> bool:
    fields = review_data.get("fields")
    field = fields.get(field_name) if isinstance(fields, dict) else None
    if not isinstance(field, dict):
        return False
    alternatives = field.get("alternatives")
    return isinstance(alternatives, list) and len(alternatives) > 1


def _tr_field_has_quality_issue(review_data: dict[str, Any], field_name: str) -> bool:
    issues = review_data.get("qualityIssues")
    if not isinstance(issues, list):
        return False
    return any(isinstance(issue, dict) and issue.get("field") == field_name for issue in issues)


def _tr_field_needs_vision(review_data: dict[str, Any], field_name: str) -> bool:
    if _tr_field_empty_in_source(review_data, field_name):
        return False
    value = _tr_field_value(review_data, field_name)
    if field_name in {"motherId", "fatherId"} and not value:
        return False
    if field_name == "remark" and value:
        if (
            "เธชเธณเธซเธฃเธฑเธเน€เธเนเธฒเธซเธเนเธฒเธ—เธตเน" in value
            or "PageNumber" in value
            or re.search(r"\b[0-9เน-เน]{4}[-+ ][0-9เน-เน]{6}[-+ ][0-9เน-เน]\b", value)
        ):
            return True
    return not validate_tr_field_value(field_name, value)


def _tr_field_should_use_vision(review_data: dict[str, Any], field_name: str) -> bool:
    if _tr_field_needs_vision(review_data, field_name):
        return True
    return field_name in TR_VISION_VERIFY_FIELD_KEYS


def _tr_field_should_rescue_from_slot(
    review_data: dict[str, Any],
    field_name: str,
    *,
    mode: str,
) -> bool:
    if _tr_field_empty_in_source(review_data, field_name):
        return False
    value = _tr_field_value(review_data, field_name)
    review_status = _tr_field_review_status(review_data, field_name)
    if mode == "aggressive":
        if field_name in TR_VISION_PARENT_NAME_FIELDS:
            return True
        if field_name in TR_VISION_ALWAYS_SLOT_READ_FIELD_KEYS:
            return True
        return not validate_tr_field_value(field_name, value)
    if review_status == "needs_review":
        return True
    if not validate_tr_field_value(field_name, value):
        return True
    if _tr_field_has_alternatives(review_data, field_name):
        return True
    if _tr_field_has_quality_issue(review_data, field_name):
        return True
    if field_name in TR_VISION_ID_FIELD_KEYS and _is_suspicious_tr_id_value(value):
        return True
    if field_name in TR_VISION_PARENT_NAME_FIELDS:
        return _tr_parent_name_needs_vision_read(review_data, field_name)
    return False


def _tr_parent_name_needs_vision_read(review_data: dict[str, Any], field_name: str) -> bool:
    value = _tr_field_value(review_data, field_name)
    if not validate_tr_field_value(field_name, value):
        return True
    if value and len(_compact_tr_compare_value(value)) <= 3:
        return True

    mother_name = _tr_field_value(review_data, "motherName")
    father_name = _tr_field_value(review_data, "fatherName")
    if (
        mother_name
        and father_name
        and _compact_tr_compare_value(mother_name) == _compact_tr_compare_value(father_name)
    ):
        return True

    fields = review_data.get("fields")
    field = fields.get(field_name) if isinstance(fields, dict) else None
    review_status = str(field.get("reviewStatus") or "") if isinstance(field, dict) else ""
    return review_status == "needs_review"


def _tr_field_should_use_crop_ocr(
    review_data: dict[str, Any],
    field_name: str,
    *,
    mode: str,
) -> bool:
    if _tr_field_empty_in_source(review_data, field_name):
        return False
    if _vision_bbox_for_tr_field(field_name) is None:
        return False

    fields = review_data.get("fields")
    field = fields.get(field_name) if isinstance(fields, dict) else None
    review_status = str(field.get("reviewStatus") or "") if isinstance(field, dict) else ""
    value = _tr_field_value(review_data, field_name)

    if mode == "aggressive":
        if field_name in TR_VISION_PARENT_NAME_FIELDS:
            return True
        if field_name == "personName":
            return True
        if field_name in {"houseCode", "address"}:
            return True
    if field_name in {"age", "address"}:
        return True
    if not validate_tr_field_value(field_name, value):
        return True
    if review_status == "needs_review":
        return True
    if _tr_field_has_alternatives(review_data, field_name):
        return True
    if _tr_field_has_quality_issue(review_data, field_name):
        return True
    if field_name in TR_VISION_ID_FIELD_KEYS and _is_suspicious_tr_id_value(value):
        return True
    if field_name in TR_VISION_PARENT_NAME_FIELDS:
        return _tr_parent_name_needs_vision_read(review_data, field_name)
    return False


def _should_accept_vision_tr_value(
    *,
    field_name: str,
    current_value: str | None,
    vision_value: str,
) -> bool:
    if field_name in TR_VISION_PARENT_NAME_FIELDS and len(vision_value.split()) > 1:
        return False

    if field_name not in TR_VISION_VERIFY_FIELD_KEYS or not current_value:
        return True

    current_tokens = current_value.split()
    vision_tokens = vision_value.split()
    if len(vision_tokens) > len(current_tokens) and " ".join(vision_tokens[: len(current_tokens)]) == current_value:
        return False

    return True


def _compact_tr_compare_value(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _compact_tr_digits_value(value: str | None) -> str:
    return _compact_digits(str(value or ""))


def _compact_tr_reject_value(value: str | None) -> str:
    return _compact_tr_digits_value(value) or _compact_tr_compare_value(value)


def _address_house_number(value: str | None) -> str | None:
    match = re.match(rf"\s*({TR_ADDRESS_HOUSE_NUMBER_PATTERN})\b", str(value or ""))
    if not match:
        return None
    return re.sub(
        r"\s*-\s*",
        "-",
        match.group(1).translate(str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")),
    )


def _address_moo_number(value: str | None) -> str | None:
    match = re.search(
        r"(?:หมู่|ม\.)\s*([0-9๐-๙]+)",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return _thai_digits_to_ascii(match.group(1))


def _address_numeric_parts(value: str | None) -> dict[str, str]:
    parts: dict[str, str] = {}
    house_number = _address_house_number(value)
    moo_number = _address_moo_number(value)
    if house_number:
        parts["house"] = house_number
    if moo_number:
        parts["moo"] = moo_number
    return parts


def _replace_address_numeric_parts(value: str, parts: dict[str, str]) -> str:
    repaired = value
    house_number = parts.get("house")
    if house_number and _address_house_number(repaired):
        repaired = re.sub(
            rf"^\s*{TR_ADDRESS_HOUSE_NUMBER_PATTERN}",
            house_number,
            repaired,
            count=1,
        )
    moo_number = parts.get("moo")
    if moo_number and _address_moo_number(repaired):
        repaired = re.sub(
            r"(หมู่|ม\.)\s*[0-9๐-๙]+",
            rf"\1 {moo_number}",
            repaired,
            count=1,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\s+", " ", repaired).strip()


def _address_numeric_parts_disagree(
    current_value: str | None,
    candidate_parts: dict[str, str],
) -> bool:
    current_parts = _address_numeric_parts(current_value)
    return any(
        current_parts.get(key)
        and candidate_parts.get(key)
        and current_parts[key] != candidate_parts[key]
        for key in ("house", "moo")
    )


def _address_without_house_number(value: str | None) -> str:
    return re.sub(
        rf"^\s*{TR_ADDRESS_HOUSE_NUMBER_PATTERN}\s*",
        "",
        _compact_tr_compare_value(value),
    )


def _address_house_number_disagrees(current_value: str | None, crop_value: str | None) -> bool:
    current_house_number = _address_house_number(current_value)
    crop_house_number = _address_house_number(crop_value)
    current_moo_number = _address_moo_number(current_value)
    crop_moo_number = _address_moo_number(crop_value)
    house_disagrees = bool(
        current_house_number
        and crop_house_number
        and current_house_number != crop_house_number
    )
    moo_disagrees = bool(
        current_moo_number and crop_moo_number and current_moo_number != crop_moo_number
    )
    if not house_disagrees and not moo_disagrees:
        return False
    current_tail = _address_without_house_number(current_value)
    crop_tail = _address_without_house_number(crop_value)
    if current_tail and crop_tail and current_tail == crop_tail:
        return True
    if not moo_disagrees:
        return False
    current_without_moo = re.sub(
        r"(?:หมู่|ม\.)\s*[0-9๐-๙]+",
        "",
        current_tail,
        count=1,
        flags=re.IGNORECASE,
    )
    crop_without_moo = re.sub(
        r"(?:หมู่|ม\.)\s*[0-9๐-๙]+",
        "",
        crop_tail,
        count=1,
        flags=re.IGNORECASE,
    )
    return bool(current_without_moo and current_without_moo == crop_without_moo)


def _address_house_number_is_more_complete(candidate: str | None, current_value: str | None) -> bool:
    candidate_house_number = _address_house_number(candidate)
    current_house_number = _address_house_number(current_value)
    candidate_moo_number = _address_moo_number(candidate)
    current_moo_number = _address_moo_number(current_value)
    if (
        candidate_moo_number
        and current_moo_number
        and candidate_moo_number != current_moo_number
    ):
        return True
    if not candidate_house_number or not current_house_number:
        return False
    if candidate_house_number == current_house_number:
        return False
    if "-" in candidate_house_number and "-" not in current_house_number:
        return True
    if "/" in candidate_house_number and "/" not in current_house_number:
        return True
    return len(candidate_house_number) > len(current_house_number)


def _parent_name_disagreement_is_soft(current_value: str | None, vision_value: str | None) -> bool:
    if not validate_tr_field_value("motherName", current_value):
        return False
    if not validate_tr_field_value("motherName", vision_value):
        return True
    current_compact = _compact_tr_compare_value(current_value)
    vision_compact = _compact_tr_compare_value(vision_value)
    if not current_compact or not vision_compact:
        return False
    if current_compact == vision_compact:
        return True
    if abs(len(current_compact) - len(vision_compact)) > 2:
        return False
    mismatch_count = sum(
        1
        for left, right in zip(current_compact, vision_compact, strict=False)
        if left != right
    ) + abs(len(current_compact) - len(vision_compact))
    return mismatch_count <= 2


def _candidate_tr_values_for_field(field: dict[str, Any] | None, current_value: str | None) -> list[str]:
    candidates: list[str] = []

    def add(value: Any) -> None:
        raw_value = str(value or "").strip()
        if not raw_value:
            return
        compact = _compact_tr_compare_value(raw_value)
        if not compact:
            return
        if any(_compact_tr_compare_value(candidate) == compact for candidate in candidates):
            return
        candidates.append(raw_value)

    add(current_value)
    alternatives = field.get("alternatives") if isinstance(field, dict) else None
    if isinstance(alternatives, list):
        for alternative in alternatives:
            if isinstance(alternative, dict):
                add(alternative.get("value"))
    return candidates


def _is_plausible_parent_name_crop_value(value: str | None) -> bool:
    if not validate_tr_field_value("motherName", value):
        return False
    cleaned = str(value or "").strip()
    if not cleaned:
        return False
    if len(cleaned.split()) > 2:
        return False
    blocked_parts = (
        "ส่วนราชการ",
        "กรม",
        "กระทรวง",
        "สำนักงาน",
        "เลขที่",
        "วันที่",
        "ที่อยู่",
        "เบอร์โทรศัพท์",
        "อีเมล",
    )
    return not any(part in cleaned for part in blocked_parts)


def _has_actionable_suspicious_reasons(reasons: list[str]) -> bool:
    benign_prefixes = (
        "OCR quality looked usable after comparing",
        "No watermark was detected, so OCR used the original rendered page image",
        "The pipeline compared OCR models:",
    )
    return any(
        reason
        for reason in reasons
        if reason and not any(reason.startswith(prefix) for prefix in benign_prefixes)
    )


def _merge_tr_field_alternative(
    field: dict[str, Any],
    *,
    value: str,
    source: str,
    reason: str | None = None,
) -> bool:
    alternatives = field.get("alternatives")
    if not isinstance(alternatives, list):
        alternatives = []
        field["alternatives"] = alternatives

    compact_value = _compact_tr_compare_value(value)
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            continue
        if _compact_tr_compare_value(str(alternative.get("value") or "")) != compact_value:
            continue
        sources = alternative.get("sources")
        if not isinstance(sources, list):
            sources = []
            alternative["sources"] = sources
        if source and source not in sources:
            sources.append(source)
        if reason and not alternative.get("reason"):
            alternative["reason"] = reason
        return False

    alternatives.append(
        {
            "value": value,
            "sources": [source] if source else [],
            "reason": reason,
        }
    )
    return True


def _matching_tr_field_alternative(field: dict[str, Any], value: str | None) -> str | None:
    compact_value = _compact_tr_compare_value(value)
    if not compact_value:
        return None
    alternatives = field.get("alternatives")
    if not isinstance(alternatives, list):
        return None
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            continue
        alternative_value = str(alternative.get("value") or "").strip()
        if _compact_tr_compare_value(alternative_value) == compact_value:
            return alternative_value
    return None


def _first_valid_parent_name_alternative(
    field_name: str,
    field: dict[str, Any],
) -> tuple[str, str] | None:
    alternatives = field.get("alternatives")
    if not isinstance(alternatives, list):
        return None
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            continue
        value = str(alternative.get("value") or "").strip()
        if not validate_tr_field_value(field_name, value):
            continue
        sources = alternative.get("sources")
        source_values = [str(source) for source in sources] if isinstance(sources, list) else []
        if not source_values and alternative.get("source"):
            source_values = [str(alternative.get("source"))]
        return value, source_values[0] if source_values else "parent_name_alternative"
    return None


def _best_parent_name_crop_alternative(field: dict[str, Any], duplicate_value: str) -> tuple[str, str] | None:
    alternatives = field.get("alternatives")
    if not isinstance(alternatives, list):
        return None
    duplicate_compact = _compact_tr_compare_value(duplicate_value)
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            continue
        value = str(alternative.get("value") or "").strip()
        if not validate_tr_field_value("motherName", value):
            continue
        if _compact_tr_compare_value(value) == duplicate_compact:
            continue
        sources = alternative.get("sources")
        source_values = [str(source) for source in sources] if isinstance(sources, list) else []
        if not any("crop_ocr" in source for source in source_values):
            continue
        return value, source_values[0] if source_values else "crop_ocr_parent_name_alternative"
    return None


def _best_parent_name_field_crop_alternative(field: dict[str, Any], current_value: str) -> tuple[str, str] | None:
    alternatives = field.get("alternatives")
    if not isinstance(alternatives, list):
        return None
    current_compact = _compact_tr_compare_value(current_value)
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            continue
        value = str(alternative.get("value") or "").strip()
        if not _is_plausible_parent_name_crop_value(value):
            continue
        if _compact_tr_compare_value(value) == current_compact:
            continue
        sources = alternative.get("sources")
        source_values = [str(source) for source in sources] if isinstance(sources, list) else []
        field_crop_source = next(
            (
                source
                for source in source_values
                if "crop_ocr" in source and "field_crop" in source
            ),
            None,
        )
        if field_crop_source:
            return value, field_crop_source
    return None


def _best_age_crop_ocr_alternative(field: dict[str, Any]) -> tuple[str, str] | None:
    alternatives = field.get("alternatives")
    if not isinstance(alternatives, list):
        return None
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            continue
        value = str(alternative.get("value") or "").strip()
        if not validate_tr_field_value("age", value):
            continue
        sources = alternative.get("sources")
        source_values = [str(source) for source in sources] if isinstance(sources, list) else []
        crop_source = next((source for source in source_values if "crop_ocr" in source), None)
        if crop_source:
            return value, crop_source
    return None


def _repair_parent_names_from_field_crop_alternatives(review_data: dict[str, Any]) -> list[dict[str, str]]:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return []

    repaired: list[dict[str, str]] = []
    for field_name in TR_VISION_PARENT_NAME_FIELDS:
        field = fields.get(field_name)
        if not isinstance(field, dict):
            continue
        current_value = _tr_field_value(review_data, field_name)
        if not validate_tr_field_value(field_name, current_value):
            continue

        source = str(field.get("source") or "")
        review_status = str(field.get("reviewStatus") or "")
        if "vision" in source or "crop_ocr" in source:
            continue
        if source != "tr_text_parser" and review_status != "needs_review":
            continue

        candidate = _best_parent_name_field_crop_alternative(field, current_value or "")
        if candidate is None:
            continue
        value, candidate_source = candidate
        _merge_tr_field_alternative(
            field,
            value=current_value or "",
            source=source or "previous_parent_name",
            reason="Previous parent-name value before field-crop repair.",
        )
        template = get_tr_field_template(field_name)
        bbox = _vision_bbox_for_tr_field(field_name)
        field["value"] = value
        field["pageNumber"] = template.page_number if template is not None else 1
        field["bbox"] = list(bbox) if bbox is not None else None
        field["source"] = candidate_source
        field["reviewStatus"] = "needs_review"
        field["reviewNote"] = "ชื่อพ่อ/แม่ถูกแก้จาก crop เฉพาะช่อง โปรดตรวจภาพ"
        repaired.append(
            {
                "field": field_name,
                "value": value,
                "source": candidate_source,
                "action": "corrected_from_field_crop_alternative",
            }
        )
    return repaired


def _repair_duplicate_parent_names_from_crop_alternatives(review_data: dict[str, Any]) -> list[dict[str, str]]:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return []
    mother_field = fields.get("motherName")
    father_field = fields.get("fatherName")
    if not isinstance(mother_field, dict) or not isinstance(father_field, dict):
        return []

    mother_name = _tr_field_value(review_data, "motherName")
    father_name = _tr_field_value(review_data, "fatherName")
    if (
        not mother_name
        or not father_name
        or _compact_tr_compare_value(mother_name) != _compact_tr_compare_value(father_name)
    ):
        return []

    repaired: list[dict[str, str]] = []
    for field_name, field in (("motherName", mother_field), ("fatherName", father_field)):
        current_value = _tr_field_value(review_data, field_name)
        if not current_value:
            continue
        candidate = _best_parent_name_crop_alternative(field, current_value)
        if candidate is None:
            continue
        value, source = candidate
        _merge_tr_field_alternative(
            field,
            value=current_value,
            source=str(field.get("source") or "previous_duplicate_parent_name"),
            reason="Previous duplicate parent-name value before crop alternative repair.",
        )
        template = get_tr_field_template(field_name)
        bbox = _vision_bbox_for_tr_field(field_name)
        field["value"] = value
        field["pageNumber"] = template.page_number if template is not None else 1
        field["bbox"] = list(bbox) if bbox is not None else None
        field["source"] = source
        field["reviewStatus"] = "needs_review"
        field["reviewNote"] = "ชื่อพ่อ/แม่ซ้ำกันและถูกแก้จาก crop alternative โปรดตรวจภาพ"
        repaired.append(
            {
                "field": field_name,
                "value": value,
                "source": source,
                "action": "corrected_duplicate_parent_name",
            }
        )
    return repaired


def _append_tr_quality_issue(
    review_data: dict[str, Any],
    *,
    field_name: str,
    issue_type: str,
    message: str,
    alternative_value: str | None = None,
    alternative_source: str | None = None,
) -> None:
    issues = review_data.get("qualityIssues")
    if not isinstance(issues, list):
        issues = []
        review_data["qualityIssues"] = issues

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if issue.get("field") == field_name and issue.get("type") == issue_type:
            alternatives = issue.get("alternatives")
            if alternative_value and isinstance(alternatives, list):
                if not any(
                    isinstance(item, dict)
                    and _compact_tr_compare_value(str(item.get("value") or ""))
                    == _compact_tr_compare_value(alternative_value)
                    for item in alternatives
                ):
                    alternatives.append(
                        {
                            "value": alternative_value,
                            "sources": [alternative_source] if alternative_source else [],
                        }
                    )
            return

    issue: dict[str, Any] = {
        "field": field_name,
        "type": issue_type,
        "severity": "review",
        "message": message,
    }
    if alternative_value:
        issue["alternatives"] = [
            {
                "value": alternative_value,
                "sources": [alternative_source] if alternative_source else [],
            }
        ]
    issues.append(issue)


def _tr_address_candidates_from_raw_ocr(text: str | None) -> list[str]:
    candidates: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"\s+", " ", re.sub(r"^#+\s*", "", raw_line)).strip()
        if not line:
            continue
        value = normalize_tr_field_value("address", line)
        if not value or not validate_tr_field_value("address", value) or not _address_house_number(value):
            continue
        compact_value = _compact_tr_compare_value(value)
        if compact_value and compact_value not in {_compact_tr_compare_value(candidate) for candidate in candidates}:
            candidates.append(value)
    return candidates


def _repair_tr_address_house_number_from_raw_ocr(
    review_data: dict[str, Any],
    raw_ocr_text: str | None,
) -> dict[str, str] | None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return None
    field = fields.get("address")
    if not isinstance(field, dict):
        return None

    current_value = str(field.get("value") or "").strip()
    if not current_value:
        return None

    candidates = [
        candidate
        for candidate in _tr_address_candidates_from_raw_ocr(raw_ocr_text)
        if _address_house_number_disagrees(current_value, candidate)
        and _address_house_number_is_more_complete(candidate, current_value)
    ]
    if not candidates:
        return None

    best_value = max(candidates, key=lambda value: len(_address_house_number(value) or ""))
    previous_source = str(field.get("source") or "tr_text_parser")
    _merge_tr_field_alternative(
        field,
        value=current_value,
        source=previous_source,
        reason="Previous parser address before raw OCR address-number repair.",
    )
    field["value"] = best_value
    field["source"] = "raw_ocr_address_line"
    field["reviewStatus"] = "needs_review"
    field["reviewNote"] = "เลขที่บ้าน/หมู่จาก OCR raw ต่างจาก parser โปรดตรวจภาพ"
    _append_tr_quality_issue(
        review_data,
        field_name="address",
        issue_type="address_number_corrected_from_raw_ocr",
        message="Address number or moo number from raw OCR disagreed with the parser value; verify against the source image.",
        alternative_value=current_value,
        alternative_source=previous_source,
    )
    return {
        "field": "address",
        "value": best_value,
        "previousValue": current_value,
        "source": "raw_ocr_address_line",
        "action": "corrected_address_number_from_raw_ocr",
    }


def _apply_tr_address_reference_validation(review_data: dict[str, Any]) -> dict[str, str] | None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return None
    field = fields.get("address")
    if not isinstance(field, dict):
        return None

    current_value = str(field.get("value") or "").strip()
    if not current_value:
        return None

    result = validate_and_correct_tr_address(current_value)
    field["locationReference"] = {
        "valid": result.valid,
        "confidence": result.confidence,
        "issue": result.issue,
        "message": result.message,
        "postalCode": result.postal_code,
        "candidate": result.candidate,
        "parsed": result.parsed,
    }

    if result.valid and result.corrected_address:
        _set_tr_postal_code_field(review_data, result.postal_code)
        _merge_tr_field_alternative(
            field,
            value=current_value,
            source=str(field.get("source") or "tr_text_parser"),
            reason="Previous OCR/parser address before Thai location reference correction.",
        )
        template = get_tr_field_template("address")
        field["value"] = result.corrected_address
        field["pageNumber"] = template.page_number if template is not None else 1
        field["bbox"] = list(template.bbox) if template is not None and template.bbox is not None else None
        field["source"] = "thai_location_reference"
        field["reviewStatus"] = "needs_review"
        field["reviewNote"] = "แก้ ต./อ./จ. จากฐานข้อมูลอ้างอิง โปรดตรวจภาพก่อนยืนยัน"
        return {
            "field": "address",
            "value": result.corrected_address,
            "postalCode": result.postal_code or "",
            "source": "thai_location_reference",
            "action": "corrected_location_reference",
        }

    if result.valid:
        _set_tr_postal_code_field(review_data, result.postal_code)
        return {
            "field": "address",
            "value": current_value,
            "postalCode": result.postal_code or "",
            "source": "thai_location_reference",
            "action": "verified_location_reference",
        }

    if result.issue == "address_reference_missing":
        return None

    _set_tr_postal_code_field(review_data, result.postal_code)

    candidate_value = None
    if result.candidate:
        candidate_value = (
            f"{result.candidate.get('subdistrict') or ''} / "
            f"{result.candidate.get('district') or ''} / "
            f"{result.candidate.get('province') or ''}"
        ).strip(" /")
    field["reviewStatus"] = "needs_review"
    field["reviewNote"] = "ที่อยู่ไม่ตรงกับฐานข้อมูลจังหวัด-อำเภอ-ตำบล โปรดตรวจภาพ"
    _append_tr_quality_issue(
        review_data,
        field_name="address",
        issue_type=result.issue or "address_location_uncertain",
        message=result.message or "Address locality could not be verified against Thai reference data.",
        alternative_value=candidate_value,
        alternative_source="thai_location_reference",
    )
    return {
        "field": "address",
        "value": current_value,
        "postalCode": result.postal_code or "",
        "source": "thai_location_reference",
        "action": "flagged_location_reference",
    }


def _reapply_address_numeric_crop_after_location_reference(
    review_data: dict[str, Any],
    crop_ocr_rescued_fields: list[dict[str, str]],
) -> dict[str, str] | None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return None
    field = fields.get("address")
    if not isinstance(field, dict):
        return None

    current_value = str(field.get("value") or "").strip()
    if not current_value:
        return None

    crop_entry = next(
        (
            entry
            for entry in reversed(crop_ocr_rescued_fields)
            if isinstance(entry, dict)
            and str(entry.get("field") or "") == "address"
            and "numeric_parts" in str(entry.get("action") or "")
        ),
        None,
    )
    if not crop_entry:
        return None

    crop_value = str(crop_entry.get("value") or "").strip()
    crop_parts = _address_numeric_parts(crop_value)
    if not crop_parts or not _address_numeric_parts_disagree(current_value, crop_parts):
        return None

    repaired_value = _replace_address_numeric_parts(current_value, crop_parts)
    if (
        not repaired_value
        or _compact_tr_compare_value(repaired_value) == _compact_tr_compare_value(current_value)
        or not validate_tr_field_value("address", repaired_value)
    ):
        return None

    previous_source = str(field.get("source") or "thai_location_reference")
    crop_source = str(crop_entry.get("source") or "crop_ocr_address_number_crop")
    _merge_tr_field_alternative(
        field,
        value=current_value,
        source=previous_source,
        reason="Location reference address before reapplying focused crop OCR numeric parts.",
    )
    field["value"] = repaired_value
    field["source"] = f"{previous_source}+{crop_source}"
    field["reviewStatus"] = "needs_review"
    field["reviewNote"] = "เลขที่บ้าน/หมู่จาก crop OCR; ต./อ./จ. จากฐานข้อมูลอ้างอิง โปรดตรวจภาพก่อนยืนยัน"
    return {
        "field": "address",
        "value": repaired_value,
        "source": str(field["source"]),
        "action": "reapplied_address_numeric_crop_after_location_reference",
    }


def _set_tr_postal_code_field(review_data: dict[str, Any], postal_code: str | None) -> None:
    value = str(postal_code or "").strip()
    if not re.fullmatch(r"\d{5}", value):
        return
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return
    field = fields.get("postalCode")
    if not isinstance(field, dict):
        field = {}
        fields["postalCode"] = field
    field["value"] = value
    field["pageNumber"] = None
    field["bbox"] = None
    field["source"] = "thai_location_reference"
    field["reviewStatus"] = "derived"
    field["reviewNote"] = "เติมจากฐานข้อมูลอ้างอิงจังหวัด-อำเภอ-ตำบล"


def _vision_disagreement_from_reason(reason: str | None) -> tuple[str, str] | None:
    if not reason or "candidate-free Vision read disagreed" not in reason:
        return None
    match = re.search(r"\((?P<value>.+?) from (?P<source>[^)]+)\)", reason)
    if not match:
        return None
    value = match.group("value").strip()
    source = match.group("source").strip()
    return (value, source) if value and source else None


def _vision_name_disagreement_value(
    *,
    field_name: str,
    reason: str | None,
) -> tuple[str, str] | None:
    if field_name not in TR_VISION_NAME_FIELD_KEYS:
        return None
    disagreement = _vision_disagreement_from_reason(reason)
    if disagreement is None:
        return None
    raw_value, source = disagreement
    value = _normalize_vision_tr_value(field_name, raw_value)
    if not validate_tr_field_value(field_name, value):
        return None
    return value, source


def _append_vision_name_alternative_from_reason(
    review_data: dict[str, Any],
    *,
    field_name: str,
    reason: str | None,
) -> bool:
    disagreement = _vision_name_disagreement_value(
        field_name=field_name,
        reason=reason,
    )
    if disagreement is None:
        return False
    value, source = disagreement

    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return False
    field = fields.get(field_name)
    if not isinstance(field, dict):
        field = {}
        fields[field_name] = field

    current_value = str(field.get("value") or "").strip()
    if current_value and _compact_tr_compare_value(current_value) == _compact_tr_compare_value(value):
        return False

    added = _merge_tr_field_alternative(
        field,
        value=value,
        source=source,
        reason=reason,
    )
    field["reviewStatus"] = "needs_review"
    field["reviewNote"] = "OCR/Vision อ่านชื่อไม่ตรงกัน"
    _append_tr_quality_issue(
        review_data,
        field_name=field_name,
        issue_type="vision_name_disagreement",
        message="Vision crop read disagrees with the selected Thai name; review the field crop before completing.",
        alternative_value=value,
        alternative_source=source,
    )
    return added


def _mark_vision_name_correction_for_review(
    review_data: dict[str, Any],
    *,
    field_name: str,
    value: str,
    source: str | None,
    reason: str | None,
) -> None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return
    field = fields.get(field_name)
    if not isinstance(field, dict):
        field = {}
        fields[field_name] = field

    _merge_tr_field_alternative(
        field,
        value=value,
        source=source or "vision_field_verify",
        reason=reason or "Vision suggested a different Thai name.",
    )
    field["reviewStatus"] = "needs_review"
    field["reviewNote"] = "OCR/Vision อ่านชื่อไม่ตรงกัน"
    _append_tr_quality_issue(
        review_data,
        field_name=field_name,
        issue_type="vision_name_correction_candidate",
        message="Vision suggested a different Thai name; review the field crop before completing.",
        alternative_value=value,
        alternative_source=source,
    )


def _parent_id_field_for_name(field_name: str) -> str | None:
    if field_name == "motherName":
        return "motherId"
    if field_name == "fatherName":
        return "fatherId"
    return None


def _mark_parent_name_without_id_for_review(review_data: dict[str, Any]) -> None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return

    for field_name in ("motherName", "fatherName"):
        parent_id_field = _parent_id_field_for_name(field_name)
        if not parent_id_field or _tr_field_value(review_data, parent_id_field):
            continue
        if _tr_field_empty_in_source(review_data, parent_id_field):
            continue

        field = fields.get(field_name)
        if not isinstance(field, dict):
            continue
        value = str(field.get("value") or "").strip()
        alternatives = field.get("alternatives")
        has_alternatives = isinstance(alternatives, list) and any(
            isinstance(item, dict) and str(item.get("value") or "").strip()
            for item in alternatives
        )
        if not value and not has_alternatives:
            continue

        field["reviewStatus"] = "needs_review"
        if has_alternatives:
            if value:
                field["reviewNote"] = "เลข ID พ่อ/แม่ว่าง และชื่อมีหลายตัวเลือก โปรดตรวจภาพ"
            else:
                candidate = _first_valid_parent_name_alternative(field_name, field)
                if candidate is not None:
                    candidate_value, candidate_source = candidate
                    field["value"] = candidate_value
                    field["source"] = candidate_source
                field["reviewNote"] = "เลข ID พ่อ/แม่ว่าง และชื่อมีหลายตัวเลือก โปรดตรวจภาพ"
        else:
            field["reviewNote"] = "เลข ID พ่อ/แม่ว่าง โปรดตรวจชื่อ"
        _append_tr_quality_issue(
            review_data,
            field_name=field_name,
            issue_type="parent_name_without_id",
            message="Parent ID is blank/dash; verify this Thai name against the source image.",
        )


def _is_duplicate_tr_id_value(
    review_data: dict[str, Any],
    field_name: str,
    value: str,
) -> bool:
    if field_name not in {"personId", "motherId", "fatherId"}:
        return False
    for other_field in ("personId", "motherId", "fatherId"):
        if other_field == field_name:
            continue
        if _tr_field_value(review_data, other_field) == value:
            return True
    return False


def _is_suspicious_tr_id_value(value: str | None) -> bool:
    digits = _compact_tr_digits_value(value)
    if len(digits) != 13:
        return False
    return digits.count("0") >= 8 or "00000" in digits


def _add_tr_validation_issue(
    issues: list[dict[str, str]],
    seen: set[tuple[str, str]],
    *,
    field_name: str,
    issue: str,
    message: str | None = None,
) -> None:
    key = (field_name, issue)
    if key in seen:
        return
    seen.add(key)
    entry = {"field": field_name, "issue": issue}
    if message:
        entry["message"] = message
    issues.append(entry)


def _build_tr_field_validation_issues(review_data: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    fields = review_data.get("fields")
    field_map = fields if isinstance(fields, dict) else {}

    for field_name in TR_REQUIRED_FIELD_KEYS:
        if _tr_field_empty_in_source(review_data, field_name):
            continue
        if _tr_field_needs_vision(review_data, field_name):
            _add_tr_validation_issue(
                issues,
                seen,
                field_name=field_name,
                issue="missing_or_invalid",
            )

    for field_name, field in field_map.items():
        if not isinstance(field, dict):
            continue
        if str(field.get("reviewStatus") or "") == "needs_review":
            _add_tr_validation_issue(
                issues,
                seen,
                field_name=str(field_name),
                issue="needs_review",
                message=str(field.get("reviewNote") or ""),
            )

    for name_field, id_field in (("motherName", "motherId"), ("fatherName", "fatherId")):
        if _tr_field_empty_in_source(review_data, id_field):
            continue
        if _tr_field_value(review_data, name_field) and not _tr_field_value(review_data, id_field):
            _add_tr_validation_issue(
                issues,
                seen,
                field_name=id_field,
                issue="parent_id_missing",
                message=f"{name_field} exists but {id_field} is missing.",
            )

    id_values: dict[str, str] = {}
    for field_name in ("personId", "motherId", "fatherId"):
        field_value = _tr_field_value(review_data, field_name)
        if _is_suspicious_tr_id_value(field_value):
            _add_tr_validation_issue(
                issues,
                seen,
                field_name=field_name,
                issue="suspicious_id",
                message="ID has an unusually long zero run; verify against the source image.",
            )
        compact = _compact_tr_digits_value(field_value)
        if compact:
            id_values[field_name] = compact
    for field_name, compact in id_values.items():
        duplicate_fields = [
            other_field
            for other_field, other_compact in id_values.items()
            if other_field != field_name and other_compact == compact
        ]
        if duplicate_fields:
            _add_tr_validation_issue(
                issues,
                seen,
                field_name=field_name,
                issue="duplicate_id",
                message=f"Duplicate ID also appears in {', '.join(duplicate_fields)}.",
            )

    mother_name = _tr_field_value(review_data, "motherName")
    father_name = _tr_field_value(review_data, "fatherName")
    if mother_name and father_name and _compact_tr_compare_value(mother_name) == _compact_tr_compare_value(father_name):
        for field_name in ("motherName", "fatherName"):
            _add_tr_validation_issue(
                issues,
                seen,
                field_name=field_name,
                issue="duplicate_parent_name",
                message="Mother and father names are identical.",
            )

    for field_name in ("motherName", "fatherName"):
        value = _tr_field_value(review_data, field_name)
        parent_id_field = "motherId" if field_name == "motherName" else "fatherId"
        parent_id_value = _tr_field_value(review_data, parent_id_field)
        source = _tr_field_source(review_data, field_name) or ""
        trusted_short_parent_name = (
            value
            and validate_tr_field_value(parent_id_field, parent_id_value)
            and any(token in source for token in ("crop_ocr", "vision"))
        )
        if value and len(_compact_tr_compare_value(value)) <= 3 and not trusted_short_parent_name:
            _add_tr_validation_issue(
                issues,
                seen,
                field_name=field_name,
                issue="short_parent_name",
                message="ชื่อพ่อ/แม่สั้นมาก โปรดตรวจภาพ",
            )

    return issues


def _read_tr_field_with_vision(
    *,
    field_name: str,
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
    allow_ocr_fallback: bool = True,
    reject_values: set[str] | None = None,
    allowed_values: list[str] | None = None,
    debug_crop_dir: Path | None = None,
) -> tuple[str | None, str | None]:
    crop_specs = _vision_crop_specs_for_tr_field(field_name)
    if not crop_specs:
        return None, "field has no configured crop"

    source_images: list[tuple[str, Image.Image]] = [("vision_original", page_image)]
    if cleaned_image is not None:
        source_images.append(("vision_cleaned", cleaned_image))

    errors: list[str] = []
    rejected_values = reject_values or set()
    normalized_allowed_values = [
        value
        for value in (allowed_values or [])
        if validate_tr_field_value(field_name, value)
    ]

    def is_rejected(value: str | None) -> bool:
        return bool(value and _compact_tr_reject_value(value) in rejected_values)

    def allowed_match(value: str | None) -> str | None:
        if not value or not normalized_allowed_values:
            return value
        compact_value = _compact_tr_compare_value(value)
        for allowed_value in normalized_allowed_values:
            if _compact_tr_compare_value(allowed_value) == compact_value:
                return allowed_value
        return None

    if field_name == "updateDate":
        for source_label, source_image in source_images:
            for spec_label, bbox, padding, _crop_scope in crop_specs:
                crop = _crop_normalized_bbox(source_image, bbox, padding=padding)
                _save_vision_debug_crop(
                    debug_crop_dir=debug_crop_dir,
                    field_name=field_name,
                    source_label=source_label.replace("vision_", "ocr_"),
                    spec_label=spec_label,
                    crop=crop,
                )
                value, error = _read_tr_date_field_with_ocr_crop(
                    field_name=field_name,
                    crop=crop,
                    active_settings=active_settings,
                )
                if validate_tr_field_value(field_name, value):
                    if is_rejected(value):
                        errors.append(f"{source_label.replace('vision_', 'ocr_')}_{spec_label}: rejected duplicate value {value}")
                        continue
                    return value, f"{source_label.replace('vision_', 'ocr_')}_{spec_label}"
                if error:
                    errors.append(f"{source_label.replace('vision_', 'ocr_')}_{spec_label}: {error}")

    if allow_ocr_fallback and field_name == "houseCode":
        for source_label, source_image in source_images:
            for spec_label, bbox, padding, _crop_scope in crop_specs:
                crop = _crop_normalized_bbox(source_image, bbox, padding=padding)
                _save_vision_debug_crop(
                    debug_crop_dir=debug_crop_dir,
                    field_name=field_name,
                    source_label=source_label.replace("vision_", "ocr_"),
                    spec_label=spec_label,
                    crop=crop,
                )
                value, error = _read_tr_house_code_field_with_ocr_crop(
                    crop=crop,
                    active_settings=active_settings,
                )
                if validate_tr_field_value(field_name, value):
                    return value, f"{source_label.replace('vision_', 'ocr_')}_{spec_label}"
                if error:
                    errors.append(f"{source_label.replace('vision_', 'ocr_')}_{spec_label}: {error}")

    if allow_ocr_fallback and field_name in TR_VISION_ID_FIELD_KEYS:
        for source_label, source_image in source_images:
            for spec_label, bbox, padding, _crop_scope in crop_specs:
                crop = _crop_normalized_bbox(source_image, bbox, padding=padding)
                _save_vision_debug_crop(
                    debug_crop_dir=debug_crop_dir,
                    field_name=field_name,
                    source_label=source_label.replace("vision_", "ocr_"),
                    spec_label=spec_label,
                    crop=crop,
                )
                value, error = _read_tr_id_field_with_ocr_crop(
                    field_name=field_name,
                    crop=crop,
                    active_settings=active_settings,
                )
                if validate_tr_field_value(field_name, value):
                    if is_rejected(value):
                        errors.append(f"{source_label.replace('vision_', 'ocr_')}_{spec_label}: rejected duplicate value {value}")
                        continue
                    return value, f"{source_label.replace('vision_', 'ocr_')}_{spec_label}"
                if error:
                    errors.append(f"{source_label.replace('vision_', 'ocr_')}_{spec_label}: {error}")

    for source_label, source_image in source_images:
        for spec_label, bbox, padding, crop_scope in crop_specs:
            if field_name in TR_VISION_VISION_VALUE_DISABLED_FIELD_KEYS:
                continue
            crop = _crop_normalized_bbox(source_image, bbox, padding=padding)
            _save_vision_debug_crop(
                debug_crop_dir=debug_crop_dir,
                field_name=field_name,
                source_label=source_label,
                spec_label=spec_label,
                crop=crop,
            )
            if normalized_allowed_values:
                prompt = (
                    "You are choosing the exact visible value for one cropped Thai TR form field.\n"
                    f"Field key: {field_name}\n"
                    f"Field instruction: {TR_VISION_FIELD_HINTS.get(field_name, 'Read only the visible field value.')}\n"
                    f"Allowed values: {json.dumps(normalized_allowed_values, ensure_ascii=False)}\n"
                    "Look only at this crop. Return one value only if the crop clearly matches it exactly, "
                    "including Thai upper/lower vowels, tone marks, and similar letters. "
                    "If none of the allowed values is clearly exact, return null. "
                    "Do not invent, correct, normalize, or output any value outside the allowed list.\n"
                    "Return only JSON in this exact shape: {\"value\": string|null}."
                )
            else:
                prompt = _tr_vision_read_prompt_for_field(field_name, crop_scope)
            try:
                payload = run_vision_json(image=crop, prompt=prompt, settings=active_settings)
            except Exception as exc:
                errors.append(f"{source_label}_{spec_label}: {exc}")
                continue

            value = _normalize_vision_tr_value(field_name, payload.get("value"))
            value = allowed_match(value)
            if validate_tr_field_value(field_name, value):
                if is_rejected(value):
                    errors.append(f"{source_label}_{spec_label}: rejected duplicate value {value}")
                    continue
                return value, f"{source_label}_{spec_label}"
            if value:
                errors.append(f"{source_label}_{spec_label}: invalid value {value}")

    if allow_ocr_fallback and field_name in TR_VISION_NAME_FIELD_KEYS:
        for source_label, source_image in source_images:
            for spec_label, bbox, padding, _crop_scope in crop_specs:
                crop = _crop_normalized_bbox(source_image, bbox, padding=padding)
                _save_vision_debug_crop(
                    debug_crop_dir=debug_crop_dir,
                    field_name=field_name,
                    source_label=source_label.replace("vision_", "ocr_"),
                    spec_label=spec_label,
                    crop=crop,
                )
                value, error = _read_tr_name_field_with_ocr_crop(
                    field_name=field_name,
                    crop=crop,
                    active_settings=active_settings,
                )
                if validate_tr_field_value(field_name, value):
                    if is_rejected(value):
                        errors.append(f"{source_label.replace('vision_', 'ocr_')}_{spec_label}: rejected duplicate value {value}")
                        continue
                    return value, f"{source_label.replace('vision_', 'ocr_')}_{spec_label}"
                if error:
                    errors.append(f"{source_label.replace('vision_', 'ocr_')}_{spec_label}: {error}")

    return None, " | ".join(errors) if errors else "no vision value returned"


def _verify_tr_field_candidate_with_vision(
    *,
    field_name: str,
    current_value: str | None,
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
) -> tuple[str, str | None, str | None, str | None]:
    if not current_value:
        value, source = _read_tr_field_with_vision(
            field_name=field_name,
            page_image=page_image,
            cleaned_image=cleaned_image,
            active_settings=active_settings,
        )
        return ("rescued" if value else "uncertain", value, source, "missing OCR candidate")

    bbox = _vision_bbox_for_tr_field(field_name)
    if bbox is None:
        return "uncertain", None, None, "field has no configured crop"

    prompt = (
        "You are verifying one cropped field from the original Thai TR form image.\n"
        f"Field key: {field_name}\n"
        f"Field instruction: {TR_VISION_FIELD_HINTS.get(field_name, 'Read only the visible field value.')}\n"
        f"OCR candidate: {json.dumps(current_value, ensure_ascii=False)}\n"
        "Look only at this crop. Check whether the OCR candidate is exactly correct, "
        "including Thai upper/lower vowels, tone marks, and similar letters.\n"
        "If the candidate is exactly correct, return status \"correct\" and corrected_value null.\n"
        "If it is wrong and the exact visible value is clear, return status \"incorrect\" and corrected_value.\n"
        "If it is not clear, return status \"uncertain\" and corrected_value null.\n"
        "Never copy text from adjacent rows or columns. Do not normalize, romanize, or guess.\n"
        "Return only JSON in this exact shape: "
        "{\"status\":\"correct|incorrect|uncertain\",\"corrected_value\":string|null,\"reason\":string}."
    )
    source_images: list[tuple[str, Image.Image]] = [("vision_original_verify", page_image)]
    if cleaned_image is not None:
        source_images.append(("vision_cleaned_verify", cleaned_image))

    errors: list[str] = []
    for source_label, source_image in source_images:
        crop = _crop_normalized_bbox(
            source_image,
            bbox,
            padding=_vision_crop_padding_for_tr_field(field_name),
        )
        try:
            payload = run_vision_json(image=crop, prompt=prompt, settings=active_settings)
        except Exception as exc:
            errors.append(f"{source_label}: {exc}")
            continue

        raw_status = str(payload.get("status") or "").strip().lower()
        reason = str(payload.get("reason") or "").strip() or None
        corrected_value = _normalize_vision_tr_value(field_name, payload.get("corrected_value"))

        if raw_status == "correct":
            if field_name in TR_VISION_NAME_FIELD_KEYS:
                independent_value, independent_source = _read_tr_field_with_vision(
                    field_name=field_name,
                    page_image=page_image,
                    cleaned_image=cleaned_image,
                    active_settings=active_settings,
                    allow_ocr_fallback=False,
                )
                if (
                    validate_tr_field_value(field_name, independent_value)
                    and _compact_tr_compare_value(independent_value)
                    != _compact_tr_compare_value(current_value)
                ):
                    return (
                        "uncertain",
                        None,
                        source_label,
                        "candidate-free Vision read disagreed "
                        f"({independent_value} from {independent_source or 'vision_field_read'})",
                    )
            return "correct", current_value, source_label, reason
        if raw_status == "incorrect" and validate_tr_field_value(field_name, corrected_value):
            return "incorrect", corrected_value, source_label, reason
        if raw_status == "uncertain":
            return "uncertain", None, source_label, reason
        if validate_tr_field_value(field_name, corrected_value):
            return "incorrect", corrected_value, source_label, reason or "Vision returned a corrected value"

        errors.append(f"{source_label}: invalid verification payload {payload}")

    return "uncertain", None, None, " | ".join(errors) if errors else "no vision verification returned"


def _extract_tr_id_candidates_from_crop_text(field_name: str, text: str) -> list[str]:
    candidates: list[str] = []

    def add_candidate(raw_value: str) -> None:
        value = _normalize_vision_tr_value(field_name, raw_value)
        if validate_tr_field_value(field_name, value) and value not in candidates:
            candidates.append(value)

    for match in re.finditer(TR_PERSON_ID_PATTERN, text):
        add_candidate(match.group(0))

    for match in re.finditer(r"[0-9เน-เน][0-9เน-เน\-\s.]{11,24}[0-9เน-เน]", text):
        raw_value = match.group(0)
        if len(_compact_digits(raw_value)) == 13:
            add_candidate(raw_value)

    return candidates


def _extract_tr_house_code_candidates_from_crop_text(text: str) -> list[str]:
    candidates: list[str] = []

    def add_candidate(raw_value: str) -> None:
        value = _normalize_vision_tr_value("houseCode", raw_value)
        if validate_tr_field_value("houseCode", value) and value not in candidates:
            candidates.append(str(value))

    for match in re.finditer(r"[0-9๐-๙]{4}[-+\s.][0-9๐-๙]{6}[-+\s.][0-9๐-๙]", text):
        add_candidate(match.group(0))

    for match in re.finditer(r"[0-9๐-๙][0-9๐-๙\-\s.]{9,18}[0-9๐-๙]", text):
        raw_value = match.group(0)
        if len(_compact_digits(raw_value)) == 11:
            add_candidate(raw_value)

    return candidates


def _read_tr_house_code_field_with_ocr_crop(
    *,
    crop: Image.Image,
    active_settings: Any,
) -> tuple[str | None, str | None]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        text = run_ocr_page(
            temp_path,
            1,
            active_settings,
            source_is_cleaned=True,
        )
    except Exception as exc:
        return None, str(exc)
    finally:
        temp_path.unlink(missing_ok=True)

    candidates = _extract_tr_house_code_candidates_from_crop_text(text)
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, f"multiple houseCode candidates in OCR crop: {', '.join(candidates)}"
    return None, f"no valid houseCode in OCR crop: {text[:120]}"


def _read_tr_id_field_with_ocr_crop(
    *,
    field_name: str,
    crop: Image.Image,
    active_settings: Any,
) -> tuple[str | None, str | None]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        text = run_ocr_page(
            temp_path,
            1,
            active_settings,
            source_is_cleaned=True,
        )
    except Exception as exc:
        return None, str(exc)
    finally:
        temp_path.unlink(missing_ok=True)

    candidates = _extract_tr_id_candidates_from_crop_text(field_name, text)
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, f"multiple ID candidates in OCR crop: {', '.join(candidates)}"
    return None, f"no valid {field_name} in OCR crop: {text[:120]}"


def _read_tr_name_field_with_ocr_crop(
    *,
    field_name: str,
    crop: Image.Image,
    active_settings: Any,
) -> tuple[str | None, str | None]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        text = run_ocr_page(
            temp_path,
            1,
            active_settings,
            source_is_cleaned=True,
        )
    except Exception as exc:
        return None, str(exc)
    finally:
        temp_path.unlink(missing_ok=True)

    for raw_line in text.splitlines():
        line = re.sub(r"^#+\s*", "", raw_line).strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            continue
        if any(character.isdigit() for character in line):
            continue
        if any(token in line for token in ("PageNumber", "เธซเธกเธนเน", "เธเธญเธข", "เธ–เธเธ", "เธ•.", "เธญ.", "เธ.")):
            continue
        if validate_tr_field_value(field_name, line):
            return line, None
    return None, f"no valid {field_name} in OCR crop"


def _read_tr_date_field_with_ocr_crop(
    *,
    field_name: str,
    crop: Image.Image,
    active_settings: Any,
) -> tuple[str | None, str | None]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        text = run_ocr_page(
            temp_path,
            1,
            active_settings,
            source_is_cleaned=True,
        )
    except Exception as exc:
        return None, str(exc)
    finally:
        temp_path.unlink(missing_ok=True)

    month_pattern = (
        r"มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|"
        r"กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม|"
        r"พวาคม|พวฤษภาคม|พคษภาคม|พศจิกายน"
    )
    compact_text = re.sub(r"<[^>]+>", " ", text)
    compact_text = re.sub(r"[^\S\r\n]+", " ", compact_text)
    date_pattern = re.compile(
        rf"([0-9๐-๙]{{1,2}})\s*[.)]?\s*({month_pattern})\s*([0-9๐-๙]{{4}})"
    )
    for match in date_pattern.finditer(compact_text):
        value = normalize_tr_field_value(
            field_name,
            f"{match.group(1)} {match.group(2)} {match.group(3)}",
        )
        if validate_tr_field_value(field_name, value):
            return value, None
    return None, f"no valid {field_name} in OCR crop: {text[:120]}"


def _read_tr_free_text_field_with_ocr_crop(
    *,
    field_name: str,
    crop: Image.Image,
    active_settings: Any,
) -> tuple[str | None, str | None]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        text = run_ocr_page(
            temp_path,
            1,
            active_settings,
            source_is_cleaned=True,
        )
    except Exception as exc:
        return None, str(exc)
    finally:
        temp_path.unlink(missing_ok=True)

    cleaned_lines = [
        re.sub(r"\s+", " ", re.sub(r"^#+\s*", "", line)).strip()
        for line in text.splitlines()
    ]
    cleaned_text = " ".join(line for line in cleaned_lines if line)
    value = normalize_tr_field_value(field_name, cleaned_text)
    if validate_tr_field_value(field_name, value):
        return value, None
    return None, f"no valid {field_name} in OCR crop: {text[:120]}"


def _extract_tr_age_candidates_from_crop_text(text: str | None) -> list[str]:
    candidates: list[str] = []
    cleaned = re.sub(r"<[^>]+>", " ", str(text or ""))
    cleaned = _thai_digits_to_ascii(cleaned)
    for match in re.finditer(r"(?<!\d)([0-9]{1,3})(?!\d)", cleaned):
        value = match.group(1)
        if not validate_tr_field_value("age", value):
            continue
        if value not in candidates:
            candidates.append(value)
    return candidates


def _read_tr_age_field_with_ocr_crop(
    *,
    crop: Image.Image,
    active_settings: Any,
) -> tuple[str | None, str | None]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        text = run_ocr_page(
            temp_path,
            1,
            active_settings,
            source_is_cleaned=True,
        )
    except Exception as exc:
        return None, str(exc)
    finally:
        temp_path.unlink(missing_ok=True)

    candidates = _extract_tr_age_candidates_from_crop_text(text)
    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, f"multiple age candidates in OCR crop: {', '.join(candidates)}"
    return None, f"no valid age in OCR crop: {text[:120]}"


def _read_tr_address_field_with_ocr_crop(
    *,
    crop: Image.Image,
    active_settings: Any,
) -> tuple[str | None, str | None]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        text = run_ocr_page(
            temp_path,
            1,
            active_settings,
            source_is_cleaned=True,
        )
    except Exception as exc:
        return None, str(exc)
    finally:
        temp_path.unlink(missing_ok=True)

    candidates: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", re.sub(r"^#+\s*", "", raw_line)).strip()
        if not line:
            continue
        value = normalize_tr_field_value("address", line)
        if validate_tr_field_value("address", value) and _address_house_number(value):
            candidates.append(str(value))

    if not candidates:
        cleaned_text = " ".join(
            re.sub(r"\s+", " ", re.sub(r"^#+\s*", "", line)).strip()
            for line in text.splitlines()
            if line.strip()
        )
        value = normalize_tr_field_value("address", cleaned_text)
        if validate_tr_field_value("address", value) and _address_house_number(value):
            candidates.append(str(value))

    unique_candidates: list[str] = []
    for candidate in candidates:
        if _compact_tr_compare_value(candidate) not in {
            _compact_tr_compare_value(existing)
            for existing in unique_candidates
        }:
            unique_candidates.append(candidate)

    if len(unique_candidates) == 1:
        return unique_candidates[0], None
    if len(unique_candidates) > 1:
        return None, f"multiple address candidates in OCR crop: {', '.join(unique_candidates)}"
    return None, f"no valid address in OCR crop: {text[:120]}"


def _extract_tr_address_numeric_parts_from_crop_text(text: str | None) -> dict[str, str]:
    cleaned = re.sub(r"<[^>]+>", " ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = _thai_digits_to_ascii(cleaned)
    parts: dict[str, str] = {}

    house_match = re.match(rf"\s*({TR_ADDRESS_HOUSE_NUMBER_PATTERN})\b", cleaned)
    if house_match:
        parts["house"] = re.sub(r"\s*-\s*", "-", house_match.group(1)).strip()

    moo_match = re.search(
        r"(?:หมู่|ม\.?|หมู|หม)\s*([0-9]{1,3})",
        cleaned,
        flags=re.IGNORECASE,
    )
    if moo_match:
        parts["moo"] = moo_match.group(1)
    return parts


def _read_tr_address_numeric_parts_with_ocr_crop(
    *,
    crop: Image.Image,
    active_settings: Any,
) -> tuple[dict[str, str] | None, str | None]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        text = run_ocr_page(
            temp_path,
            1,
            active_settings,
            source_is_cleaned=True,
        )
    except Exception as exc:
        return None, str(exc)
    finally:
        temp_path.unlink(missing_ok=True)

    parts = _extract_tr_address_numeric_parts_from_crop_text(text)
    if parts:
        return parts, None
    return None, f"no address numeric parts in OCR crop: {str(text or '')[:120]}"


def _read_tr_address_numeric_parts_from_page_crops(
    *,
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
    debug_crop_dir: Path | None = None,
) -> tuple[dict[str, str] | None, str | None]:
    specs = [
        spec
        for spec in _vision_crop_specs_for_tr_field("address")
        if spec[0] == "address_number_crop"
    ]
    if not specs:
        return None, "address number crop is not configured"

    source_images: list[tuple[str, Image.Image]] = [("crop_ocr_original", page_image)]
    if cleaned_image is not None:
        source_images.append(("crop_ocr_cleaned", cleaned_image))

    candidates: list[tuple[dict[str, str], str]] = []
    errors: list[str] = []
    for source_label, source_image in source_images:
        for spec_label, bbox, padding, _crop_scope in specs:
            crop = _crop_normalized_bbox(source_image, bbox, padding=padding)
            _save_vision_debug_crop(
                debug_crop_dir=debug_crop_dir,
                field_name="address",
                source_label=source_label,
                spec_label=spec_label,
                crop=crop,
            )
            parts, error = _read_tr_address_numeric_parts_with_ocr_crop(
                crop=crop,
                active_settings=active_settings,
            )
            if parts:
                candidates.append((parts, f"{source_label}_{spec_label}"))
            elif error:
                errors.append(f"{source_label}_{spec_label}: {error}")

    if not candidates:
        return None, " | ".join(errors) if errors else "no address numeric crop value returned"

    def compact(parts: dict[str, str]) -> str:
        return "|".join(f"{key}:{parts.get(key, '')}" for key in ("house", "moo"))

    grouped: dict[str, dict[str, Any]] = {}
    for parts, source in candidates:
        key = compact(parts)
        entry = grouped.setdefault(key, {"parts": parts, "sources": []})
        entry["sources"].append(source)

    best = max(
        grouped.values(),
        key=lambda entry: (
            len(entry.get("sources") or []),
            1 if (entry.get("parts") or {}).get("moo") else 0,
            1 if (entry.get("parts") or {}).get("house") else 0,
        ),
    )
    sources = best.get("sources") if isinstance(best.get("sources"), list) else []
    return best.get("parts"), ",".join(str(source) for source in sources)


def _read_tr_simple_field_with_ocr_crop(
    *,
    field_name: str,
    crop: Image.Image,
    active_settings: Any,
) -> tuple[str | None, str | None]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        crop.save(temp_path, format="PNG")
        text = run_ocr_page(
            temp_path,
            1,
            active_settings,
            source_is_cleaned=True,
        )
    except Exception as exc:
        return None, str(exc)
    finally:
        temp_path.unlink(missing_ok=True)

    candidates: list[str] = []
    for raw_part in re.split(r"[\s|,;:()\[\]{}<>]+", text):
        value = normalize_tr_field_value(field_name, raw_part)
        if validate_tr_field_value(field_name, value) and value not in candidates:
            candidates.append(str(value))

    if not candidates:
        for raw_line in text.splitlines():
            value = normalize_tr_field_value(field_name, raw_line)
            if validate_tr_field_value(field_name, value) and str(value) not in candidates:
                candidates.append(str(value))

    if len(candidates) == 1:
        return candidates[0], None
    if len(candidates) > 1:
        return None, f"multiple {field_name} candidates in OCR crop: {', '.join(candidates)}"
    return None, f"no valid {field_name} in OCR crop: {text[:120]}"


def _read_tr_field_with_crop_ocr(
    *,
    field_name: str,
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
    reject_values: set[str] | None = None,
    debug_crop_dir: Path | None = None,
) -> tuple[str | None, str | None]:
    crop_specs = _vision_crop_specs_for_tr_field(field_name)
    if not crop_specs:
        return None, "field has no configured crop"

    source_images: list[tuple[str, Image.Image]] = [("crop_ocr_original", page_image)]
    if cleaned_image is not None:
        source_images.append(("crop_ocr_cleaned", cleaned_image))

    errors: list[str] = []
    rejected_values = reject_values or set()
    candidates: list[tuple[str, str]] = []
    should_vote = field_name in TR_CROP_OCR_VOTE_FIELD_KEYS

    def is_rejected(value: str | None) -> bool:
        return bool(value and _compact_tr_reject_value(value) in rejected_values)

    for source_label, source_image in source_images:
        for spec_label, bbox, padding, _crop_scope in crop_specs:
            crop = _crop_normalized_bbox(source_image, bbox, padding=padding)
            _save_vision_debug_crop(
                debug_crop_dir=debug_crop_dir,
                field_name=field_name,
                source_label=source_label,
                spec_label=spec_label,
                crop=crop,
            )

            if field_name == "houseCode":
                value, error = _read_tr_house_code_field_with_ocr_crop(
                    crop=crop,
                    active_settings=active_settings,
                )
            elif field_name in TR_VISION_ID_FIELD_KEYS:
                value, error = _read_tr_id_field_with_ocr_crop(
                    field_name=field_name,
                    crop=crop,
                    active_settings=active_settings,
                )
            elif field_name in {"birthDate", "moveInDate", "updateDate"}:
                value, error = _read_tr_date_field_with_ocr_crop(
                    field_name=field_name,
                    crop=crop,
                    active_settings=active_settings,
                )
            elif field_name in TR_VISION_NAME_FIELD_KEYS:
                value, error = _read_tr_name_field_with_ocr_crop(
                    field_name=field_name,
                    crop=crop,
                    active_settings=active_settings,
                )
            elif field_name == "address":
                value, error = _read_tr_address_field_with_ocr_crop(
                    crop=crop,
                    active_settings=active_settings,
                )
            elif field_name == "age":
                value, error = _read_tr_age_field_with_ocr_crop(
                    crop=crop,
                    active_settings=active_settings,
                )
            elif field_name == "remark":
                value, error = _read_tr_free_text_field_with_ocr_crop(
                    field_name=field_name,
                    crop=crop,
                    active_settings=active_settings,
                )
            else:
                value, error = _read_tr_simple_field_with_ocr_crop(
                    field_name=field_name,
                    crop=crop,
                    active_settings=active_settings,
                )

            if validate_tr_field_value(field_name, value):
                if is_rejected(value):
                    errors.append(f"{source_label}_{spec_label}: rejected duplicate value {value}")
                    continue
                if should_vote:
                    candidates.append((value, f"{source_label}_{spec_label}"))
                    continue
                return value, f"{source_label}_{spec_label}"
            if error:
                errors.append(f"{source_label}_{spec_label}: {error}")

    if candidates:
        return _select_crop_ocr_voted_candidate(field_name, candidates)

    return None, " | ".join(errors) if errors else "no crop OCR value returned"


def _select_crop_ocr_voted_candidate(
    field_name: str,
    candidates: list[tuple[str, str]],
) -> tuple[str | None, str | None]:
    grouped: dict[str, dict[str, Any]] = {}
    for value, source in candidates:
        compact = _compact_tr_compare_value(value)
        if not compact:
            continue
        entry = grouped.setdefault(compact, {"value": value, "sources": []})
        entry["sources"].append(source)

    if not grouped:
        return None, "crop OCR candidates were empty after normalization"

    def score(item: tuple[str, dict[str, Any]]) -> tuple[int, int, int]:
        _compact, entry = item
        value = str(entry.get("value") or "")
        sources = entry.get("sources") if isinstance(entry.get("sources"), list) else []
        plausible = 1
        if field_name in TR_VISION_PARENT_NAME_FIELDS:
            plausible = 1 if _is_plausible_parent_name_crop_value(value) else 0
        focused = 1 if any("field_crop" in str(source) for source in sources) else 0
        return (plausible, len(sources), focused)

    ranked = sorted(grouped.items(), key=score, reverse=True)
    best_compact, best_entry = ranked[0]
    best_score = score((best_compact, best_entry))
    tied = [
        entry
        for compact, entry in ranked
        if compact != best_compact and score((compact, entry)) == best_score
    ]
    if tied and field_name in TR_VISION_PARENT_NAME_FIELDS:
        source_summary = ", ".join(
            f"{entry.get('value')} ({len(entry.get('sources') or [])})"
            for _compact, entry in ranked
        )
        return None, f"multiple similarly strong parent-name crop candidates: {source_summary}"

    value = str(best_entry.get("value") or "")
    sources = [str(source) for source in best_entry.get("sources") or [] if source]
    source = sources[0] if len(sources) == 1 else f"{sources[0]}_consensus"
    return value, source


def _vision_endpoint_available(active_settings: Any) -> tuple[bool, str | None]:
    raw_url = str(getattr(active_settings, "vision_base_url", "") or "")
    if not raw_url:
        return False, "VISION_BASE_URL is not configured"
    try:
        parsed = urlparse(raw_url)
    except ValueError as exc:
        return False, f"invalid VISION_BASE_URL: {exc}"

    hostname = parsed.hostname
    if not hostname:
        return False, "VISION_BASE_URL has no hostname"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((hostname, port), timeout=1.5):
            return True, None
    except OSError as exc:
        return False, f"vision endpoint is not reachable at {hostname}:{port}: {exc}"


def _mark_tr_field_empty_in_source(
    review_data: dict[str, Any],
    *,
    field_name: str,
    state: str,
    source: str,
    reason: str | None,
) -> None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return
    field = fields.get(field_name)
    if not isinstance(field, dict):
        field = {}
        fields[field_name] = field

    template = get_tr_field_template(field_name)
    bbox = _vision_bbox_for_tr_field(field_name)
    field["value"] = None
    field["pageNumber"] = template.page_number if template is not None else 1
    field["bbox"] = list(bbox) if bbox is not None else None
    field["source"] = "source_dash" if state == "dash" else "source_blank"
    field["reviewStatus"] = "empty_in_source"
    if reason:
        field["reviewNote"] = reason
    field.pop("alternatives", None)


def _read_tr_empty_state_with_vision(
    *,
    field_name: str,
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
) -> tuple[str | None, str | None, str | None]:
    bbox = _vision_bbox_for_tr_field(field_name)
    if bbox is None:
        return None, None, "field has no configured crop"

    prompt = (
        "You are checking one cropped field from a Thai TR fixed-layout form.\n"
        f"Field key: {field_name}\n"
        f"Field instruction: {TR_VISION_FIELD_HINTS.get(field_name, 'Read only the visible field value.')}\n"
        "Look only inside this field slot, not neighboring rows or columns.\n"
        "Classify the visible slot as:\n"
        "- value: a real field value is visible\n"
        "- dash: the slot contains only a dash or similar absent-value mark\n"
        "- blank: the slot is intentionally empty/has no value\n"
        "- unclear: the crop is not clear enough to decide\n"
        "Return only JSON in this exact shape: "
        "{\"state\":\"value|dash|blank|unclear\",\"reason\":string}."
    )
    source_images: list[tuple[str, Image.Image]] = [("vision_original_empty_check", page_image)]
    if cleaned_image is not None:
        source_images.append(("vision_cleaned_empty_check", cleaned_image))

    errors: list[str] = []
    for source_label, source_image in source_images:
        crop = _crop_normalized_bbox(
            source_image,
            bbox,
            padding=_vision_crop_padding_for_tr_field(field_name),
        )
        try:
            payload = run_vision_json(image=crop, prompt=prompt, settings=active_settings)
        except Exception as exc:
            errors.append(f"{source_label}: {exc}")
            continue

        state = str(payload.get("state") or "").strip().lower()
        reason = str(payload.get("reason") or "").strip() or None
        if state in {"dash", "blank", "value"}:
            return state, source_label, reason
        if state == "unclear":
            errors.append(f"{source_label}: unclear ({reason or 'no reason'})")
            continue
        errors.append(f"{source_label}: invalid empty-state payload {payload}")

    return None, None, " | ".join(errors) if errors else "no empty-state result"


def _mark_source_empty_parent_fields_with_vision(
    *,
    review_data: dict[str, Any],
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
) -> list[str]:
    errors: list[str] = []
    for field_name in TR_VISION_SOURCE_EMPTY_FIELD_KEYS:
        if _tr_field_value(review_data, field_name) or _tr_field_empty_in_source(review_data, field_name):
            continue
        state, source, reason = _read_tr_empty_state_with_vision(
            field_name=field_name,
            page_image=page_image,
            cleaned_image=cleaned_image,
            active_settings=active_settings,
        )
        if state in {"dash", "blank"} and source:
            _mark_tr_field_empty_in_source(
                review_data,
                field_name=field_name,
                state=state,
                source=source,
                reason=reason,
            )
        elif state is None and reason:
            errors.append(f"{field_name}: empty-source check uncertain ({reason})")
    return errors


def _mark_visual_dash_parent_id_fields(
    *,
    review_data: dict[str, Any],
    page_image: Image.Image,
    debug_crop_dir: Path | None = None,
) -> list[dict[str, str]]:
    marked: list[dict[str, str]] = []
    for field_name in ("motherId", "fatherId"):
        if _tr_field_empty_in_source(review_data, field_name):
            continue
        # A valid structured OCR value is stronger evidence than the visual
        # dash heuristic. This prevents a dash in the adjacent parent row from
        # clearing a valid ID.
        if validate_tr_field_value(
            field_name,
            _tr_field_value(review_data, field_name),
        ):
            continue
        if not _field_crop_has_visual_dash(page_image, field_name, debug_crop_dir=debug_crop_dir):
            continue
        _mark_tr_field_empty_in_source(
            review_data,
            field_name=field_name,
            state="dash",
            source="visual_dash_detector",
            reason="The source image crop contains a dash in this parent ID slot.",
        )
        marked.append({"field": field_name, "source": "visual_dash_detector"})
    return marked


def _retry_duplicate_parent_ids_with_row_crops(
    *,
    review_data: dict[str, Any],
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
    debug_crop_dir: Path | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    mother_id = _tr_field_value(review_data, "motherId")
    father_id = _tr_field_value(review_data, "fatherId")
    if not mother_id or not father_id:
        return [], []
    if _compact_tr_digits_value(mother_id) != _compact_tr_digits_value(father_id):
        return [], []
    if not getattr(active_settings, "vision_ready", False):
        return [], ["parent IDs are duplicated; row-crop retry skipped because vision settings are not ready"]

    endpoint_available, endpoint_error = _vision_endpoint_available(active_settings)
    if not endpoint_available:
        return [], [f"parent IDs are duplicated; row-crop retry skipped: {endpoint_error}"]

    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return [], ["parent IDs are duplicated; row-crop retry skipped because review fields were missing"]

    repaired: list[dict[str, str]] = []
    errors: list[str] = []
    duplicate_compact = _compact_tr_reject_value(mother_id)
    for field_name in ("motherId", "fatherId"):
        value, source = _read_tr_field_with_vision(
            field_name=field_name,
            page_image=page_image,
            cleaned_image=cleaned_image,
            active_settings=active_settings,
            allow_ocr_fallback=True,
            reject_values={duplicate_compact},
            debug_crop_dir=debug_crop_dir,
        )
        if validate_tr_field_value(field_name, value):
            field = fields.get(field_name)
            if not isinstance(field, dict):
                field = {}
                fields[field_name] = field
            template = get_tr_field_template(field_name)
            bbox = _vision_bbox_for_tr_field(field_name)
            field["value"] = value
            field["pageNumber"] = template.page_number if template is not None else 1
            field["bbox"] = list(bbox) if bbox is not None else None
            field["source"] = source or "vision_duplicate_parent_id_retry"
            field["reviewStatus"] = "needs_review"
            field["reviewNote"] = "เลข ID พ่อ/แม่ซ้ำกันและถูกอ่านใหม่จาก row crop โปรดตรวจภาพ"
            repaired.append(
                {
                    "field": field_name,
                    "value": value or "",
                    "source": source or "vision_duplicate_parent_id_retry",
                    "action": "duplicate_parent_id_retry",
                }
            )
            continue

        field = fields.get(field_name)
        if isinstance(field, dict):
            field["reviewStatus"] = "needs_review"
            field["reviewNote"] = (
                "Mother and father IDs were duplicated; row-specific crop retry did not find a distinct valid ID."
            )
        errors.append(f"{field_name}: duplicate parent ID row-crop retry did not return a distinct valid ID")

    return repaired, errors


def _field_crop_has_visual_dash(
    image: Image.Image,
    field_name: str,
    *,
    debug_crop_dir: Path | None = None,
) -> bool:
    bbox = _vision_bbox_for_tr_field(field_name)
    if bbox is None:
        return False

    crop = _crop_normalized_bbox(image, bbox, padding=0.002).convert("L")
    _save_vision_debug_crop(
        debug_crop_dir=debug_crop_dir,
        field_name=field_name,
        source_label="visual_dash_detector",
        spec_label="field_crop",
        crop=crop,
    )
    width, height = crop.size
    if width < 20 or height < 8:
        return False

    threshold = 150
    min_dash_width = max(6, int(width * 0.014))
    max_dash_width = max(min_dash_width + 4, int(width * 0.08))
    max_dash_height = max(4, int(height * 0.08))
    max_row_ink = max(max_dash_width * 3, int(width * 0.12))

    pixels = crop.load()
    candidate_rows: list[tuple[int, int, int]] = []
    dark_count = 0

    for y in range(height):
        row_ink = 0
        row_run = 0
        row_max_run = 0
        for x in range(width):
            is_dark = pixels[x, y] < threshold
            if is_dark:
                dark_count += 1
                row_ink += 1
                row_run += 1
                if row_run > row_max_run:
                    row_max_run = row_run
            else:
                row_run = 0
        if min_dash_width <= row_max_run <= max_dash_width and row_ink <= max_row_ink:
            candidate_rows.append((y, row_max_run, row_ink))

    if not candidate_rows:
        return False
    if dark_count / float(width * height) > 0.025:
        return False

    group: list[tuple[int, int, int]] = []
    groups: list[list[tuple[int, int, int]]] = []
    for row in candidate_rows:
        if group and row[0] - group[-1][0] > 1:
            groups.append(group)
            group = []
        group.append(row)
    if group:
        groups.append(group)

    for group in groups:
        row_span = group[-1][0] - group[0][0] + 1
        if row_span > max_dash_height:
            continue
        if max(row_max_run for _y, row_max_run, _row_ink in group) < min_dash_width:
            continue
        return True
    return False


def _reconcile_age_from_dates(review_data: dict[str, Any]) -> dict[str, str] | None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return None

    birth_date = _tr_field_value(review_data, "birthDate")
    reference_date = _tr_field_value(review_data, "updateDate")
    age = _calculate_age_from_thai_dates(birth_date, reference_date)
    if not validate_tr_field_value("age", age):
        return None

    current_age = _tr_field_value(review_data, "age")
    current_age_is_valid = validate_tr_field_value("age", current_age)
    if current_age_is_valid and _thai_digits_to_ascii(str(current_age)) == str(age):
        return {
            "field": "age",
            "source": "derived_from_birth_update_dates",
            "action": "verified",
            "value": str(age),
        }

    field = fields.get("age")
    if not isinstance(field, dict):
        field = {}
        fields["age"] = field
    previous_value = str(current_age or "").strip() or None
    previous_source = str(field.get("source") or "").strip() or None
    crop_age_candidate = _best_age_crop_ocr_alternative(field)
    if (
        crop_age_candidate is not None
        and previous_value
        and _compact_tr_compare_value(previous_value)
        != _compact_tr_compare_value(crop_age_candidate[0])
    ):
        crop_value, crop_source = crop_age_candidate
        _merge_tr_field_alternative(
            field,
            value=previous_value,
            source=previous_source or "previous_age",
            reason="Previous age before promoting focused crop OCR age.",
        )
        if _compact_tr_compare_value(crop_value) != _compact_tr_compare_value(str(age)):
            _merge_tr_field_alternative(
                field,
                value=str(age),
                source="derived_from_birth_update_dates",
                reason="Calculated age from birthDate and updateDate disagreed with crop OCR age.",
            )
            field["reviewStatus"] = "needs_review"
            field["reviewNote"] = (
                f"Age crop OCR value {crop_value} did not match calculated value {age}; "
                "keeping crop OCR value."
            )
        else:
            field["reviewStatus"] = "rescued_by_crop_ocr"
            field["reviewNote"] = "อายุจาก crop OCR ตรงกับวันเกิด/update"
        field["value"] = crop_value
        field["source"] = crop_source
        return {
            "field": "age",
            "source": crop_source,
            "action": "promoted_crop_ocr_alternative",
            "previousValue": previous_value,
            "previousSource": previous_source or "",
            "value": crop_value,
            "calculatedValue": str(age),
            "calculatedSource": "derived_from_birth_update_dates",
        }
    template = get_tr_field_template("age")
    if current_age_is_valid:
        _merge_tr_field_alternative(
            field,
            value=str(age),
            source="derived_from_birth_update_dates",
            reason="Calculated age from birthDate and updateDate disagreed with OCR/crop age.",
        )
        if previous_value:
            field["value"] = previous_value
        field["source"] = previous_source or field.get("source") or "previous_age"
        field["reviewStatus"] = "needs_review"
        field["reviewNote"] = (
            f"Age OCR value {previous_value} did not match birth/update dates; "
            f"keeping OCR/crop value and showing calculated value {age} as an option."
        )
        return {
            "field": "age",
            "source": previous_source or "previous_age",
            "action": "flagged_mismatch_kept_ocr",
            "value": previous_value or "",
            "previousSource": previous_source or "",
            "calculatedValue": str(age),
            "calculatedSource": "derived_from_birth_update_dates",
        }

    field["value"] = age
    field["pageNumber"] = template.page_number if template is not None else 1
    field["bbox"] = list(template.bbox) if template is not None and template.bbox is not None else None
    field["source"] = "derived_from_birth_update_dates"
    field["reviewStatus"] = "derived"
    return {
        "field": "age",
        "source": "derived_from_birth_update_dates",
        "action": "derived_missing",
        "value": str(age),
    }


def _rescue_missing_age_with_vision(
    *,
    review_data: dict[str, Any],
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
    debug_crop_dir: Path | None = None,
) -> tuple[dict[str, str] | None, str | None]:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return None, "age: vision fallback skipped because review fields were missing"
    if _tr_field_empty_in_source(review_data, "age"):
        return None, None
    if validate_tr_field_value("age", _tr_field_value(review_data, "age")):
        return None, None
    if not getattr(active_settings, "vision_ready", False):
        return None, "age: vision fallback skipped because vision settings are not ready"

    endpoint_available, endpoint_error = _vision_endpoint_available(active_settings)
    if not endpoint_available:
        return None, f"age: vision fallback skipped: {endpoint_error}"

    value, source = _read_tr_field_with_vision(
        field_name="age",
        page_image=page_image,
        cleaned_image=cleaned_image,
        active_settings=active_settings,
        debug_crop_dir=debug_crop_dir,
    )
    if not validate_tr_field_value("age", value):
        return None, f"age: vision fallback did not return a valid age ({source or 'no vision value'})"

    field = fields.get("age")
    if not isinstance(field, dict):
        field = {}
        fields["age"] = field
    template = get_tr_field_template("age")
    bbox = _vision_bbox_for_tr_field("age")
    field["value"] = value
    field["pageNumber"] = template.page_number if template is not None else 1
    field["bbox"] = list(bbox) if bbox is not None else None
    field["source"] = source or "vision_field_read_last_resort"
    field["reviewStatus"] = "rescued_by_crop"
    return {
        "field": "age",
        "source": source or "vision_field_read_last_resort",
        "action": "last_resort_rescued",
    }, None


def _calculate_age_from_thai_dates(
    birth_date: str | None,
    reference_date: str | None,
) -> str | None:
    birth_parts = _parse_thai_date_parts(birth_date)
    reference_parts = _parse_thai_date_parts(reference_date)
    if birth_parts is None or reference_parts is None:
        return None

    birth_day, birth_month, birth_year = birth_parts
    reference_day, reference_month, reference_year = reference_parts
    age = reference_year - birth_year
    if (reference_month, reference_day) < (birth_month, birth_day):
        age -= 1
    return str(age) if 0 <= age <= 130 else None


def _parse_thai_date_parts(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"([0-9\u0E50-\u0E59]{1,2})\s+(\S+)\s+([0-9\u0E50-\u0E59]{4})", value.strip())
    if not match:
        return None
    month_by_name = {name: index for index, name in enumerate(TR_MONTH_NAMES, start=1)}
    month = month_by_name.get(match.group(2))
    if month is None:
        return None
    return (
        int(_thai_digits_to_ascii(match.group(1))),
        month,
        int(_thai_digits_to_ascii(match.group(3))),
    )


def _thai_digits_to_ascii(value: str) -> str:
    return value.translate({ord("\u0E50") + index: str(index) for index in range(10)})


def _snapshot_tr_fields(review_data: dict[str, Any]) -> dict[str, dict[str, str]]:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return {}

    snapshot: dict[str, dict[str, str]] = {}
    for field_name, field in fields.items():
        if not isinstance(field, dict):
            continue
        snapshot[str(field_name)] = {
            "value": str(field.get("value") or ""),
            "source": str(field.get("source") or ""),
            "reviewStatus": str(field.get("reviewStatus") or ""),
        }
    return snapshot


def _build_tr_field_decision_map(
    *,
    review_data: dict[str, Any],
    parser_snapshot: dict[str, dict[str, str]],
    derived_fields: list[dict[str, str]],
    crop_ocr_rescued_fields: list[dict[str, str]],
    vision_rescued_fields: list[dict[str, str]],
    visual_dash_fields: list[dict[str, str]],
) -> dict[str, dict[str, str]]:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return {}

    derived_by_field = {
        str(entry.get("field") or ""): entry
        for entry in derived_fields
        if isinstance(entry, dict) and entry.get("field")
    }
    vision_by_field = {
        str(entry.get("field") or ""): entry
        for entry in vision_rescued_fields
        if isinstance(entry, dict) and entry.get("field")
    }
    crop_ocr_by_field = {
        str(entry.get("field") or ""): entry
        for entry in crop_ocr_rescued_fields
        if isinstance(entry, dict) and entry.get("field")
    }
    dash_fields = {
        str(entry.get("field") or "")
        for entry in visual_dash_fields
        if isinstance(entry, dict) and entry.get("field")
    }

    decisions: dict[str, dict[str, str]] = {}
    for field_name, field in fields.items():
        if not isinstance(field, dict):
            continue
        key = str(field_name)
        selected_value = str(field.get("value") or "")
        selected_source = str(field.get("source") or "")
        parser_entry = parser_snapshot.get(key, {})
        derived_entry = derived_by_field.get(key, {})
        crop_ocr_entry = crop_ocr_by_field.get(key, {})
        vision_entry = vision_by_field.get(key, {})

        decision = {
            "selectedValue": selected_value,
            "selectedSource": selected_source,
            "selectedReviewStatus": str(field.get("reviewStatus") or ""),
            "selectionReason": _tr_field_selection_reason(
                field_name=key,
                selected_source=selected_source,
                derived_action=str(derived_entry.get("action") or ""),
                crop_ocr_action=str(crop_ocr_entry.get("action") or ""),
                vision_action=str(vision_entry.get("action") or ""),
                visual_dash=key in dash_fields,
            ),
        }

        if parser_entry:
            decision["parserValue"] = parser_entry.get("value", "")
            decision["parserSource"] = parser_entry.get("source", "")
        if derived_entry:
            decision["derivedValue"] = str(derived_entry.get("value") or selected_value)
            decision["derivedAction"] = str(derived_entry.get("action") or "")
            if derived_entry.get("previousValue") is not None:
                decision["previousValue"] = str(derived_entry.get("previousValue") or "")
                decision["previousSource"] = str(derived_entry.get("previousSource") or "")
        if vision_entry:
            decision["visionValue"] = str(vision_entry.get("value") or selected_value)
            decision["visionSource"] = str(vision_entry.get("source") or "")
            decision["visionAction"] = str(vision_entry.get("action") or "")
        if crop_ocr_entry:
            decision["cropOcrValue"] = str(crop_ocr_entry.get("value") or selected_value)
            decision["cropOcrSource"] = str(crop_ocr_entry.get("source") or "")
            decision["cropOcrAction"] = str(crop_ocr_entry.get("action") or "")
        if key in dash_fields:
            decision["visualDash"] = "true"
        consensus = _tr_candidate_consensus_label(decision)
        if consensus:
            decision["candidateConsensus"] = consensus

        decisions[key] = decision
    return decisions


def _tr_candidate_consensus_label(decision: dict[str, str]) -> str | None:
    selected = _compact_tr_compare_value(decision.get("selectedValue"))
    if not selected:
        return None

    candidates = {
        "parser": _compact_tr_compare_value(decision.get("parserValue")),
        "crop": _compact_tr_compare_value(decision.get("cropOcrValue")),
        "vision": _compact_tr_compare_value(decision.get("visionValue")),
    }
    matches = [name for name, value in candidates.items() if value and value == selected]
    distinct_values = {value for value in candidates.values() if value}

    if len(matches) >= 2:
        return "confirmed_by_" + "_".join(matches)
    if len(distinct_values) > 1:
        return "candidate_conflict"
    if matches:
        return f"single_{matches[0]}"
    return None


def _tr_field_selection_reason(
    *,
    field_name: str,
    selected_source: str,
    derived_action: str,
    crop_ocr_action: str,
    vision_action: str,
    visual_dash: bool,
) -> str:
    if visual_dash:
        return "source image showed a dash in this field"
    if field_name == "age" and derived_action == "verified":
        return "OCR/parser age matched birthDate and updateDate"
    if field_name == "age" and derived_action == "promoted_crop_ocr_alternative":
        return "crop OCR age was promoted over the parser/calculated mismatch"
    if field_name == "age" and derived_action == "flagged_mismatch_kept_ocr":
        return "OCR/crop age was kept and calculated age was shown as an alternative"
    if field_name == "age" and derived_action == "corrected_mismatch":
        return "calculated age replaced an OCR/Vision mismatch"
    if field_name == "age" and derived_action == "derived_missing":
        return "age was calculated from birthDate and updateDate"
    if crop_ocr_action:
        return "crop OCR filled or corrected this validated field"
    if field_name == "age" and vision_action:
        return "Vision read age only after parser and date calculation were unavailable"
    if vision_action:
        return "Vision filled or corrected this field from its crop"
    if selected_source:
        return f"selected from {selected_source}"
    return "no selected value"


def _apply_crop_ocr_tr_field_rescue(
    *,
    review_data: dict[str, Any],
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
    mode: str = "selective",
    debug_crop_dir: Path | None = None,
    on_stage_update: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[str]]:
    if not getattr(active_settings, "ocr_ready", False):
        return review_data, [], ["TR crop OCR rescue skipped because OCR settings are not ready."]

    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return review_data, [], ["TR crop OCR rescue skipped because review fields were missing."]

    rescued: list[dict[str, str]] = []
    errors: list[str] = []

    for field_name in TR_CROP_OCR_FIELD_KEYS:
        if not _tr_field_should_use_crop_ocr(review_data, field_name, mode=mode):
            continue
        if on_stage_update is not None:
            on_stage_update("crop_ocr_rescue", field_name)
        current_value = _tr_field_value(review_data, field_name)
        current_valid = validate_tr_field_value(field_name, current_value)

        if field_name == "address" and current_valid:
            numeric_parts, numeric_source = _read_tr_address_numeric_parts_from_page_crops(
                page_image=page_image,
                cleaned_image=cleaned_image,
                active_settings=active_settings,
                debug_crop_dir=debug_crop_dir,
            )
            if numeric_parts and _address_numeric_parts_disagree(current_value, numeric_parts):
                repaired_value = _replace_address_numeric_parts(current_value or "", numeric_parts)
                if (
                    repaired_value
                    and _compact_tr_compare_value(repaired_value)
                    != _compact_tr_compare_value(current_value)
                    and validate_tr_field_value(field_name, repaired_value)
                ):
                    field = fields.get(field_name)
                    if isinstance(field, dict):
                        _merge_tr_field_alternative(
                            field,
                            value=current_value or "",
                            source=str(field.get("source") or "previous_address"),
                            reason="Previous parser/reference address before focused numeric crop correction.",
                        )
                        template = get_tr_field_template(field_name)
                        bbox = _vision_bbox_for_tr_field(field_name)
                        field["value"] = repaired_value
                        field["pageNumber"] = template.page_number if template is not None else 1
                        field["bbox"] = list(bbox) if bbox is not None else None
                        field["source"] = numeric_source or "crop_ocr_address_number_crop"
                        field["reviewStatus"] = "needs_review"
                        field["reviewNote"] = "เลขที่บ้าน/หมู่จาก crop เฉพาะเลขต่างจาก parser โปรดตรวจภาพ"
                        rescued.append(
                            {
                                "field": field_name,
                                "value": repaired_value,
                                "source": numeric_source or "crop_ocr_address_number_crop",
                                "action": "corrected_address_numeric_parts_from_crop_needs_review",
                            }
                        )
                        errors.append(
                            f"{field_name}: crop OCR corrected address numeric parts from {current_value} to {repaired_value}; verify against image"
                        )
                        continue
        reject_values = {
            _compact_tr_reject_value(_tr_field_value(review_data, other_field))
            for other_field in TR_VISION_ID_FIELD_KEYS
            if other_field != field_name
            and _tr_field_value(review_data, other_field)
        }
        reject_values.discard("")

        value, source = _read_tr_field_with_crop_ocr(
            field_name=field_name,
            page_image=page_image,
            cleaned_image=cleaned_image,
            active_settings=active_settings,
            reject_values=reject_values,
            debug_crop_dir=debug_crop_dir,
        )

        if not validate_tr_field_value(field_name, value):
            if not current_valid:
                field = fields.get(field_name)
                if isinstance(field, dict):
                    field["reviewStatus"] = "needs_review"
                    field["reviewNote"] = source or "crop OCR could not produce one valid value"
                errors.append(f"{field_name}: crop OCR could not produce one valid value ({source or 'no value'})")
            continue

        if _is_duplicate_tr_id_value(review_data, field_name, value):
            field = fields.get(field_name)
            if isinstance(field, dict):
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "crop OCR returned an ID already used by another field"
            errors.append(f"{field_name}: crop OCR returned duplicate ID already used by another field")
            continue

        if current_valid and _compact_tr_compare_value(current_value) != _compact_tr_compare_value(value):
            field = fields.get(field_name)
            if field_name == "age" and isinstance(field, dict):
                _merge_tr_field_alternative(
                    field,
                    value=current_value or "",
                    source=str(field.get("source") or "previous_age"),
                    reason="Previous age before focused crop OCR read.",
                )
                template = get_tr_field_template(field_name)
                bbox = _vision_bbox_for_tr_field(field_name)
                field["value"] = value
                field["pageNumber"] = template.page_number if template is not None else 1
                field["bbox"] = list(bbox) if bbox is not None else None
                field["source"] = source or "crop_ocr_field_read"
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "อายุจาก parser/crop อ่านไม่ตรงกัน ใช้ค่า crop OCR เป็นค่าเริ่มต้น โปรดตรวจภาพ"
                rescued.append(
                    {
                        "field": field_name,
                        "value": value or "",
                        "source": source or "crop_ocr_field_read",
                        "action": "corrected_age_from_field_crop_needs_review",
                    }
                )
                errors.append(
                    f"{field_name}: crop OCR corrected parser value from {current_value} to {value}; verify against image"
                )
                continue
            if field_name == "houseCode" and isinstance(field, dict):
                _merge_tr_field_alternative(
                    field,
                    value=current_value or "",
                    source=str(field.get("source") or "previous_house_code"),
                    reason="Previous parser/OCR house code before focused crop read.",
                )
                template = get_tr_field_template(field_name)
                bbox = _vision_bbox_for_tr_field(field_name)
                field["value"] = value
                field["pageNumber"] = template.page_number if template is not None else 1
                field["bbox"] = list(bbox) if bbox is not None else None
                field["source"] = source or "crop_ocr_field_read"
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "รหัสบ้านจาก parser/crop อ่านไม่ตรงกัน โปรดตรวจภาพ"
                rescued.append(
                    {
                        "field": field_name,
                        "value": value or "",
                        "source": source or "crop_ocr_field_read",
                        "action": "corrected_from_field_crop_needs_review",
                    }
                )
                errors.append(
                    f"{field_name}: crop OCR corrected parser value from {current_value} to {value}; verify against image"
                )
                continue
            if (
                field_name == "address"
                and isinstance(field, dict)
                and _address_house_number_disagrees(current_value, value)
            ):
                _merge_tr_field_alternative(
                    field,
                    value=current_value or "",
                    source=str(field.get("source") or "previous_address"),
                    reason="Previous parser address before focused crop read.",
                )
                template = get_tr_field_template(field_name)
                bbox = _vision_bbox_for_tr_field(field_name)
                field["value"] = value
                field["pageNumber"] = template.page_number if template is not None else 1
                field["bbox"] = list(bbox) if bbox is not None else None
                field["source"] = source or "crop_ocr_field_read"
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "เลขที่บ้าน/หมู่จาก parser/crop อ่านไม่ตรงกัน โปรดตรวจภาพ"
                rescued.append(
                    {
                        "field": field_name,
                        "value": value or "",
                        "source": source or "crop_ocr_field_read",
                        "action": "corrected_address_number_from_field_crop_needs_review",
                    }
                )
                errors.append(
                    f"{field_name}: crop OCR corrected address number from {current_value} to {value}; verify against image"
                )
                continue
            if field_name in TR_VISION_PARENT_NAME_FIELDS and isinstance(field, dict):
                if _is_plausible_parent_name_crop_value(value):
                    _merge_tr_field_alternative(
                        field,
                        value=current_value or "",
                        source=str(field.get("source") or "previous_parent_name"),
                        reason="Previous parent-name value before focused crop correction.",
                    )
                    template = get_tr_field_template(field_name)
                    bbox = _vision_bbox_for_tr_field(field_name)
                    field["value"] = value
                    field["pageNumber"] = template.page_number if template is not None else 1
                    field["bbox"] = list(bbox) if bbox is not None else None
                    field["source"] = source or "crop_ocr_field_read"
                    field["reviewStatus"] = "needs_review"
                    field["reviewNote"] = (
                        "ชื่อพ่อ/แม่สั้นมาก โปรดตรวจภาพ"
                        if len(_compact_tr_compare_value(value)) <= 3
                        else "แก้ชื่อพ่อ/แม่จาก crop เฉพาะช่อง โปรดตรวจภาพ"
                    )
                    rescued.append(
                        {
                            "field": field_name,
                            "value": value or "",
                            "source": source or "crop_ocr_field_read",
                            "action": "corrected_from_field_crop",
                        }
                    )
                    continue
                _merge_tr_field_alternative(
                    field,
                    value=value or "",
                    source=source or "crop_ocr_field_read",
                    reason="Focused crop OCR parent-name value looked implausible.",
                )
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "crop อ่านชื่อไม่ชัด โปรดตรวจภาพ"
                errors.append(
                    f"{field_name}: crop OCR suggested implausible parent name {value}; selected value was kept"
                )
                continue
            if field_name == "personName" and isinstance(field, dict):
                _merge_tr_field_alternative(
                    field,
                    value=current_value or "",
                    source=str(field.get("source") or "previous_person_name"),
                    reason="Previous parser person-name value before focused crop correction.",
                )
                template = get_tr_field_template(field_name)
                bbox = _vision_bbox_for_tr_field(field_name)
                field["value"] = value
                field["pageNumber"] = template.page_number if template is not None else 1
                field["bbox"] = list(bbox) if bbox is not None else None
                field["source"] = source or "crop_ocr_field_read"
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "แก้ชื่อจาก crop เฉพาะช่อง โปรดตรวจภาพ"
                rescued.append(
                    {
                        "field": field_name,
                        "value": value or "",
                        "source": source or "crop_ocr_field_read",
                        "action": "corrected_from_field_crop",
                    }
                )
                continue
            if isinstance(field, dict):
                _merge_tr_field_alternative(
                    field,
                    value=value,
                    source=source or "crop_ocr_field_read",
                    reason="Crop OCR disagreed with the selected parser/OCR value.",
                )
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "parser/crop อ่านไม่ตรงกัน"
            errors.append(
                f"{field_name}: crop OCR suggested alternate value {value}"
                " but selected OCR/parser value was kept"
            )
            continue

        field = fields.get(field_name)
        if not isinstance(field, dict):
            field = {}
            fields[field_name] = field
        template = get_tr_field_template(field_name)
        bbox = _vision_bbox_for_tr_field(field_name)
        action = "verified" if current_valid else "rescued"
        field["value"] = value
        field["pageNumber"] = template.page_number if template is not None else 1
        field["bbox"] = list(bbox) if bbox is not None else None
        field["source"] = source or "crop_ocr_field_read"
        if field_name in TR_HUMAN_REVIEW_RESCUE_FIELD_KEYS and action != "verified":
            field["reviewStatus"] = "needs_review"
            field["reviewNote"] = "crop OCR ช่วยเติมค่าที่ parser/OCR อ่านไม่ได้ โปรดตรวจภาพ"
        else:
            field["reviewStatus"] = "parsed" if action == "verified" else "rescued_by_crop_ocr"
        rescued.append(
            {
                "field": field_name,
                "value": value or "",
                "source": source or "crop_ocr_field_read",
                "action": action,
            }
        )

    return review_data, rescued, errors


def _mark_unconfirmed_name_fields_for_review(
    review_data: dict[str, Any],
    *,
    active_settings: Any,
    field_names: set[str] | None = None,
) -> None:
    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return

    vision_ready = bool(getattr(active_settings, "vision_ready", False))
    target_field_names = field_names or TR_VISION_NAME_FIELD_KEYS
    for field_name in target_field_names:
        field = fields.get(field_name)
        if not isinstance(field, dict):
            continue
        value = _tr_field_value(review_data, field_name)
        if not validate_tr_field_value(field_name, value):
            continue

        source = str(field.get("source") or "")
        review_status = str(field.get("reviewStatus") or "")
        if field_name in TR_VISION_PARENT_NAME_FIELDS and value and len(_compact_tr_compare_value(value)) <= 3:
            parent_id_field = "motherId" if field_name == "motherName" else "fatherId"
            if validate_tr_field_value(parent_id_field, _tr_field_value(review_data, parent_id_field)) and any(
                token in source for token in ("crop_ocr", "vision")
            ):
                continue
            field["reviewStatus"] = "needs_review"
            field["reviewNote"] = "ชื่อพ่อ/แม่สั้นมาก โปรดตรวจภาพ"
            continue
        if "vision" in source or review_status == "confirmed_by_vision":
            continue
        if review_status == "corrected_by_rule":
            continue
        if vision_ready and review_status == "corrected_by_vision":
            continue
        if review_status == "needs_review" and field.get("reviewNote"):
            continue

        field["reviewStatus"] = "needs_review"
        field["reviewNote"] = (
            "ชื่อจาก OCR เท่านั้น"
            if field_name in TR_VISION_PRIMARY_NAME_FIELDS
            else "ชื่อพ่อ/แม่มาจาก OCR เท่านั้น"
        )


def _apply_vision_tr_field_rescue(
    *,
    review_data: dict[str, Any],
    page_image: Image.Image,
    cleaned_image: Image.Image | None,
    active_settings: Any,
    mode: str = "selective",
    debug_crop_dir: Path | None = None,
    on_stage_update: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[str]]:
    if not getattr(active_settings, "vision_ready", False):
        return review_data, [], []
    endpoint_available, endpoint_error = _vision_endpoint_available(active_settings)
    if not endpoint_available:
        return review_data, [], [f"TR Vision rescue skipped: {endpoint_error}"]

    fields = review_data.get("fields")
    if not isinstance(fields, dict):
        return review_data, [], ["TR Vision rescue skipped because review fields were missing."]

    rescued: list[dict[str, str]] = []
    errors: list[str] = []
    verification_entries: list[dict[str, str]] = []

    errors.extend(
        _mark_source_empty_parent_fields_with_vision(
            review_data=review_data,
            page_image=page_image,
            cleaned_image=cleaned_image,
            active_settings=active_settings,
        )
    )

    def apply_vision_value(field_name: str, value: str, source: str | None, action: str, status: str) -> None:
        template = get_tr_field_template(field_name)
        bbox = _vision_bbox_for_tr_field(field_name)
        field = fields.get(field_name)
        if not isinstance(field, dict):
            field = {}
            fields[field_name] = field
        field["value"] = value
        field["pageNumber"] = template.page_number if template is not None else 1
        field["bbox"] = list(bbox) if bbox is not None else None
        field["source"] = source or "vision_field_read"
        if field_name in TR_HUMAN_REVIEW_RESCUE_FIELD_KEYS:
            field["reviewStatus"] = "needs_review"
            field["reviewNote"] = (
                "Vision เสนอค่าใหม่แทน parser/OCR โปรดตรวจภาพ"
                if action == "corrected"
                else "Vision ช่วยเติมค่าที่ parser/OCR อ่านไม่ได้ โปรดตรวจภาพ"
            )
        else:
            field["reviewStatus"] = (
                "corrected_by_vision"
                if action == "corrected"
                else "rescued_by_crop"
            )
        rescued.append(
            {
                "field": field_name,
                "value": value,
                "source": source or "vision_field_read",
                "action": action,
            }
        )
        verification_entries.append(
            {
                "field": field_name,
                "source": source or "vision_field_read",
                "status": status,
                "action": action,
            }
        )

    for field_name in TR_VISION_SLOT_RESCUE_FIELD_KEYS:
        if not _tr_field_should_rescue_from_slot(review_data, field_name, mode=mode):
            continue
        if on_stage_update is not None:
            on_stage_update("vision_field_rescue", field_name)
        reject_values = {
            _compact_tr_reject_value(_tr_field_value(review_data, other_field))
            for other_field in TR_VISION_ID_FIELD_KEYS
            if other_field != field_name
            and _tr_field_value(review_data, other_field)
        }
        reject_values.discard("")
        current_value = _tr_field_value(review_data, field_name)
        value, source = _read_tr_field_with_vision(
            field_name=field_name,
            page_image=page_image,
            cleaned_image=cleaned_image,
            active_settings=active_settings,
            reject_values=reject_values,
            debug_crop_dir=debug_crop_dir,
        )
        if value is None:
            continue
        if _is_duplicate_tr_id_value(review_data, field_name, value):
            errors.append(f"{field_name}: vision returned duplicate ID already used by another field")
            continue
        if field_name in TR_VISION_PARENT_NAME_FIELDS and current_value:
            if _compact_tr_compare_value(current_value) != _compact_tr_compare_value(value):
                field = fields.get(field_name)
                if isinstance(field, dict):
                    _merge_tr_field_alternative(
                        field,
                        value=value,
                        source=source or "vision_field_read",
                        reason="Vision read disagreed with the selected crop/parser parent-name value.",
                    )
                    if _parent_name_disagreement_is_soft(current_value, value):
                        field["reviewStatus"] = "needs_review"
                        field["reviewNote"] = "OCR/Vision อ่านชื่อคล้ายกันแต่ไม่ตรงกัน โปรดตรวจภาพ"
                        verification_entries.append(
                            {
                                "field": field_name,
                                "source": source or "vision_field_read",
                                "status": "soft_disagreement",
                                "action": "kept_selected_value",
                            }
                        )
                        continue
                    field["reviewStatus"] = "needs_review"
                    field["reviewNote"] = "OCR/Vision อ่านชื่อไม่ตรงกัน"
                    errors.append(
                        f"{field_name}: vision suggested parent name {value}; selected crop/parser value was kept"
                    )
                    verification_entries.append(
                        {
                            "field": field_name,
                            "source": source or "vision_field_read",
                            "status": "uncertain",
                            "action": "flagged_alternative",
                        }
                    )
                    continue
        if not _should_accept_vision_tr_value(
            field_name=field_name,
            current_value=current_value,
            vision_value=value,
        ):
            field = fields.get(field_name)
            if isinstance(field, dict):
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "Vision อ่านติดข้อความข้างเคียง"
            errors.append(f"{field_name}: vision value looked like it included neighboring text ({value})")
            continue
        action = "corrected" if current_value and _compact_tr_compare_value(current_value) != _compact_tr_compare_value(value) else "rescued"
        apply_vision_value(field_name, value, source, action, "rescued")

    for field_name in TR_VISION_CANDIDATE_VERIFY_FIELD_KEYS:
        if _tr_field_empty_in_source(review_data, field_name):
            continue
        if on_stage_update is not None:
            on_stage_update("vision_field_rescue", field_name)
        current_value = _tr_field_value(review_data, field_name)
        status, value, source, reason = _verify_tr_field_candidate_with_vision(
            field_name=field_name,
            current_value=current_value,
            page_image=page_image,
            cleaned_image=cleaned_image,
            active_settings=active_settings,
        )

        if status == "correct":
            field = fields.get(field_name)
            if isinstance(field, dict):
                field["reviewStatus"] = "confirmed_by_vision"
            verification_entries.append(
                {
                    "field": field_name,
                    "source": source or "vision_field_verify",
                    "status": status,
                    "action": "confirmed",
                }
            )
            continue

        if value is None:
            field = fields.get(field_name)
            if isinstance(field, dict):
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = (
                    "OCR/Vision อ่านไม่ตรงกัน"
                    if _vision_name_disagreement_value(field_name=field_name, reason=reason)
                    else reason or source or "no corrected value"
                )
            errors.append(f"{field_name}: vision verification {status} ({reason or source or 'no corrected value'})")
            continue
        if _is_duplicate_tr_id_value(review_data, field_name, value):
            field = fields.get(field_name)
            if isinstance(field, dict):
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "Vision อ่าน ID ซ้ำกับช่องอื่น"
            errors.append(f"{field_name}: vision returned duplicate ID already used by another parent field")
            continue
        if field_name in TR_VISION_PARENT_NAME_FIELDS or (
            field_name == "personName" and current_value
        ):
            _mark_vision_name_correction_for_review(
                review_data,
                field_name=field_name,
                value=value,
                source=source,
                reason=reason,
            )
            errors.append(
                f"{field_name}: vision suggested alternate name {value}"
                " but selected OCR/parser value was kept"
            )
            verification_entries.append(
                {
                    "field": field_name,
                    "source": source or "vision_field_verify",
                    "status": status,
                    "action": "flagged_alternative",
                }
            )
            continue
        if not _should_accept_vision_tr_value(
            field_name=field_name,
            current_value=current_value,
            vision_value=value,
        ):
            field = fields.get(field_name)
            if isinstance(field, dict):
                field["reviewStatus"] = "needs_review"
                field["reviewNote"] = "Vision อ่านติดข้อความข้างเคียง"
            errors.append(f"{field_name}: vision value looked like it included neighboring text ({value})")
            continue

        action = "corrected" if current_value else "rescued"
        apply_vision_value(field_name, value, source or "vision_field_verify", action, status)

    _mark_parent_name_without_id_for_review(review_data)

    if verification_entries or errors:
        review_data["visionFieldVerification"] = {
            "verifiedFields": verification_entries,
            "errors": errors,
        }
    return review_data, rescued, errors


def _validate_upload_request(selected_document_type: str, files: list[UploadFile]) -> str:
    normalized_document_type = selected_document_type.strip().upper()
    if normalized_document_type != SUPPORTED_DOCUMENT_TYPE:
        raise HTTPException(status_code=400, detail="selected_document_type must be TR")

    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF file is required")

    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum files per batch is {MAX_FILES_PER_BATCH}",
        )

    invalid_file = next((file for file in files if not _is_pdf_upload(file)), None)
    if invalid_file:
        filename = _safe_original_filename(invalid_file.filename)
        raise HTTPException(status_code=400, detail=f"Only PDF files are supported: {filename}")

    return normalized_document_type


def _save_ocr_record_outputs(
    *,
    record: dict[str, Any],
    markdown: str,
    ocr_output_dir: Path,
    metadata: dict[str, Any],
) -> tuple[str, str]:
    ocr_output_dir.mkdir(parents=True, exist_ok=True)
    record_id = str(record.get("record_id") or uuid4().hex)
    markdown_path = ocr_output_dir / f"{record_id}.md"
    metadata_path = ocr_output_dir / f"{record_id}.json"
    markdown_path.write_text(markdown, encoding="utf-8")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return _relative_storage_path(markdown_path), _relative_storage_path(metadata_path)


def _build_batch_response(metadata: dict[str, Any]) -> ManagerUploadBatchResponse:
    raw_files = metadata.get("files") or []
    raw_records = metadata.get("records") or []
    files = [ManagerUploadFileResponse(**file) for file in raw_files if isinstance(file, dict)]
    records = [ManagerUploadRecordResponse(**record) for record in raw_records if isinstance(record, dict)]
    ready_to_assign_count = sum(
        1
        for record in raw_records
        if isinstance(record, dict)
        and str(record.get("ocr_status") or "") == "succeeded"
        and str(record.get("review_status") or "") == "unassigned"
    )
    assigned_count = sum(
        1
        for record in raw_records
        if isinstance(record, dict) and str(record.get("review_status") or "") == "assigned"
    )
    unassigned_count = sum(
        1
        for record in raw_records
        if isinstance(record, dict) and str(record.get("review_status") or "") == "unassigned"
    )
    in_review_count = sum(
        1
        for record in raw_records
        if isinstance(record, dict) and str(record.get("review_status") or "") == "in_review"
    )
    completed_count = sum(
        1
        for record in raw_records
        if isinstance(record, dict) and str(record.get("review_status") or "") == "completed"
    )
    return ManagerUploadBatchResponse(
        batch_id=str(metadata.get("batch_id") or ""),
        selected_document_type=str(metadata.get("selected_document_type") or ""),
        status=str(metadata.get("status") or "uploaded"),
        file_count=int(metadata.get("file_count") or len(files)),
        total_pages=int(metadata.get("total_pages") or len(records)),
        record_count=int(metadata.get("record_count") or len(records)),
        ocr_pending_count=int(metadata.get("ocr_pending_count") or 0),
        ocr_processing_count=int(metadata.get("ocr_processing_count") or 0),
        ocr_succeeded_count=int(metadata.get("ocr_succeeded_count") or 0),
        ocr_failed_count=int(metadata.get("ocr_failed_count") or 0),
        ready_to_assign_count=ready_to_assign_count,
        assigned_count=assigned_count,
        unassigned_count=unassigned_count,
        in_review_count=in_review_count,
        completed_count=completed_count,
        files=files,
        records=records,
    )


def _load_all_batch_responses() -> list[ManagerUploadBatchResponse]:
    batches: list[tuple[str, ManagerUploadBatchResponse]] = []
    for metadata_path in METADATA_ROOT.glob("*/metadata.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if _ensure_metadata_record_numbers(payload):
            _write_metadata(metadata_path, payload)
        created_at = str(payload.get("created_at") or "")
        batches.append((created_at, _build_batch_response(payload)))
    return [
        batch
        for _, batch in sorted(
            batches,
            key=lambda item: item[0],
            reverse=True,
        )
    ]


def _build_manager_dashboard_response() -> ManagerDashboardResponse:
    batches = _load_all_batch_responses()
    records: list[ManagerDashboardRecordResponse] = []
    for batch in batches:
        for record in batch.records:
            records.append(
                ManagerDashboardRecordResponse(
                    record_id=record.record_id,
                    record_no=record.record_no,
                    batch_id=record.batch_id,
                    file_id=record.file_id,
                    original_filename=record.original_filename,
                    selected_document_type=record.selected_document_type,
                    page_number=record.page_number,
                    ocr_status=record.ocr_status,
                    review_status=record.review_status,
                    has_watermark=record.has_watermark,
                    ocr_error=record.ocr_error,
                    processed_at=record.processed_at,
                    assigned_to_user_id=record.assigned_to_user_id,
                    assigned_to_username=record.assigned_to_username,
                    assigned_at=record.assigned_at,
                    created_at=record.created_at,
                )
            )

    return ManagerDashboardResponse(
        batch_count=len(batches),
        file_count=sum(batch.file_count for batch in batches),
        record_count=sum(batch.record_count for batch in batches),
        total_pages=sum(batch.total_pages for batch in batches),
        ocr_pending_count=sum(batch.ocr_pending_count for batch in batches),
        ocr_processing_count=sum(batch.ocr_processing_count for batch in batches),
        ocr_succeeded_count=sum(batch.ocr_succeeded_count for batch in batches),
        ocr_failed_count=sum(batch.ocr_failed_count for batch in batches),
        ready_to_assign_count=sum(batch.ready_to_assign_count for batch in batches),
        assigned_count=sum(batch.assigned_count for batch in batches),
        unassigned_count=sum(batch.unassigned_count for batch in batches),
        in_review_count=sum(batch.in_review_count for batch in batches),
        completed_count=sum(batch.completed_count for batch in batches),
        batches=batches,
        records=sorted(records, key=lambda record: record.created_at, reverse=True),
    )


def _process_tr_ocr_record(
    record: dict[str, Any],
    active_settings: Any,
    *,
    on_processing_started: Any | None = None,
) -> dict[str, Any]:
    if str(record.get("selected_document_type") or "").strip().upper() != SUPPORTED_DOCUMENT_TYPE:
        return record
    if str(record.get("ocr_status") or "") != "pending":
        return record

    processing_started_at = perf_counter()
    processing_stage_ms: dict[str, float] = {}

    def finish_timing_stage(stage_name: str, started_at: float) -> None:
        processing_stage_ms[stage_name] = round((perf_counter() - started_at) * 1000, 1)

    def report_live_stage(stage_key: str, field_name: str | None = None) -> None:
        record["ocr_current_stage"] = {
            "key": stage_key,
            "field": field_name,
            "started_at": _utc_now(),
        }
        _upsert_staff_import_document(record)
        if on_processing_started is not None:
            on_processing_started()

    now = _utc_now()
    record["ocr_status"] = "processing"
    record["ocr_started_at"] = now
    record["ocr_error"] = None
    _upsert_staff_import_document(record)
    if on_processing_started is not None:
        on_processing_started()

    original_path = _resolve_storage_path(str(record.get("original_path") or ""))
    derived_root = _resolve_storage_path(str(record.get("derived_root") or ""))
    page_number = int(record.get("page_number") or 0)
    if page_number < 1:
        raise RuntimeError("Record page_number is required")
    if not original_path.exists():
        raise FileNotFoundError(f"Original file not found: {original_path}")

    preview_path = derived_root / "previews" / f"page-{page_number:03d}-original.png"
    cleaned_page_path = derived_root / "watermark_cleaned" / f"page-{page_number:03d}-cleaned.png"
    ocr_output_dir = derived_root / "ocr"
    vision_debug_crop_dir = derived_root / "vision_debug_crops" / f"page-{page_number:03d}"

    report_live_stage("image_preparation")
    stage_started_at = perf_counter()
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    page_image = render_page_preview(original_path, page_number)
    page_image.save(preview_path, format="PNG")
    record["page_asset_path"] = _relative_storage_path(preview_path)
    finish_timing_stage("image_preparation", stage_started_at)

    report_live_stage("watermark_detection")
    stage_started_at = perf_counter()
    watermark_analysis = detect_tr_watermark(page_image)
    record["has_watermark"] = watermark_analysis.detected
    record["watermark_score"] = watermark_analysis.score
    record["watermark_kind"] = watermark_analysis.watermark_kind
    finish_timing_stage("watermark_detection", stage_started_at)

    ocr_source_path = preview_path
    record["cleaned_page_path"] = None
    record["cleaning_mode"] = None
    cleaned_markdown: str | None = None
    original_markdown: str | None = None
    selected_score: float | None = None
    selected_ocr_model: str | None = None
    selected_candidate_source: str | None = None
    ocr_candidate_scores: list[dict[str, Any]] = []
    original_score: float | None = None
    cleaned_score: float | None = None
    original_error: str | None = None
    cleaned_error: str | None = None
    diff_similarity: float | None = None
    suspicious_reasons: list[str] = []
    selected_source = "original"
    cleaned_image_for_vision: Image.Image | None = None

    if watermark_analysis.detected:
        report_live_stage("watermark_cleanup")
        stage_started_at = perf_counter()
        clean_result = build_tr_cleaned_image(page_image)
        cleaned_image_for_vision = clean_result.image
        cleaned_page_path.parent.mkdir(parents=True, exist_ok=True)
        clean_result.image.save(cleaned_page_path, format="PNG")
        record["cleaned_page_path"] = _relative_storage_path(cleaned_page_path)
        record["cleaning_mode"] = clean_result.cleaning_mode
        finish_timing_stage("watermark_cleanup", stage_started_at)

        report_live_stage("primary_ocr")
        stage_started_at = perf_counter()
        comparison = compare_ocr_page_sources(
            original_file_path=preview_path,
            cleaned_file_path=cleaned_page_path,
            page_number=page_number,
            settings=active_settings,
        )
        markdown = str(comparison.get("selected_markdown") or "")
        selected_source = str(comparison.get("selected_source") or "cleaned")
        selected_score = (
            float(comparison["selected_score"])
            if isinstance(comparison.get("selected_score"), (int, float))
            else None
        )
        selected_ocr_model = (
            str(comparison["selected_ocr_model"])
            if isinstance(comparison.get("selected_ocr_model"), str)
            else None
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
        if selected_candidate_source == "original":
            ocr_source_path = preview_path
        else:
            ocr_source_path = cleaned_page_path
        finish_timing_stage("primary_ocr", stage_started_at)
    else:
        report_live_stage("primary_ocr")
        stage_started_at = perf_counter()
        markdown = run_ocr_page(
            preview_path,
            page_number,
            active_settings,
            source_is_cleaned=True,
        )
        original_markdown = markdown
        selected_ocr_model = str(getattr(active_settings, "ocr_model", "") or "")
        selected_candidate_source = "original"
        ocr_candidate_scores = [
            {
                "label": f"original:{selected_ocr_model.rsplit('/', 1)[-1].replace(':latest', '')}",
                "source": "original",
                "model": selected_ocr_model,
                "score": None,
            }
        ]
        suspicious_reasons = [
            "No watermark was detected, so OCR used the original rendered page image without a cleaned-image comparison.",
        ]
        finish_timing_stage("primary_ocr", stage_started_at)

    record["ocr_input_path"] = _relative_storage_path(ocr_source_path)
    pages = [
        {
            "page_number": page_number,
            "original_preview_path": record["page_asset_path"],
            "cleaned_preview_path": record.get("cleaned_page_path") or record["page_asset_path"],
            "watermark_detected": record["has_watermark"],
            "watermark_score": record["watermark_score"],
            "cleaning_mode": record.get("cleaning_mode"),
            "markdown": markdown,
            "raw_markdown": markdown,
            "corrected_markdown": markdown,
            "original_markdown": original_markdown,
            "cleaned_markdown": cleaned_markdown,
            "selected_markdown_source": selected_source,
            "selected_markdown_score": selected_score,
            "selected_ocr_model": selected_ocr_model,
            "selected_candidate_source": selected_candidate_source,
            "ocr_candidate_scores": ocr_candidate_scores,
            "original_markdown_score": original_score,
            "cleaned_markdown_score": cleaned_score,
            "original_ocr_error": original_error,
            "cleaned_ocr_error": cleaned_error,
            "diff_similarity": diff_similarity,
            "suspicious_reasons": suspicious_reasons,
            "segments": [],
        }
    ]
    report_live_stage("parser")
    stage_started_at = perf_counter()
    review_data = build_tr_review_data(pages)
    finish_timing_stage("parser", stage_started_at)
    crop_ocr_rescued_fields: list[dict[str, str]] = []
    crop_ocr_errors: list[str] = []
    vision_rescued_fields: list[dict[str, str]] = []
    vision_errors: list[str] = []
    visual_dash_fields: list[dict[str, str]] = []
    derived_fields: list[dict[str, str]] = []
    address_house_number_repairs: list[dict[str, str]] = []
    address_reference_fields: list[dict[str, str]] = []
    parser_field_snapshot: dict[str, dict[str, str]] = {}
    field_decision_map: dict[str, dict[str, str]] = {}
    crop_ocr_mode = _manager_rescue_mode(
        "TR_CROP_OCR_MODE",
        "TR_CROP_OCR_ENABLED",
        default="off",
    )
    vision_rescue_mode = _manager_rescue_mode(
        "TR_VISION_FIELD_RESCUE_MODE",
        "TR_VISION_FIELD_RESCUE_ENABLED",
        default="off",
    )
    if isinstance(review_data, dict):
        report_live_stage("local_field_analysis")
        stage_started_at = perf_counter()
        parser_field_snapshot = _snapshot_tr_fields(review_data)
        visual_dash_fields = _mark_visual_dash_parent_id_fields(
            review_data=review_data,
            page_image=page_image,
            debug_crop_dir=vision_debug_crop_dir,
        )
        _mark_unconfirmed_name_fields_for_review(
            review_data,
            active_settings=active_settings,
            field_names=TR_VISION_NAME_FIELD_KEYS,
        )
        finish_timing_stage("local_field_analysis", stage_started_at)
        if _rescue_mode_enabled(crop_ocr_mode):
            report_live_stage("crop_ocr_rescue")
            stage_started_at = perf_counter()
            review_data, crop_ocr_rescued_fields, crop_ocr_errors = _apply_crop_ocr_tr_field_rescue(
                review_data=review_data,
                page_image=page_image,
                cleaned_image=cleaned_image_for_vision,
                active_settings=active_settings,
                mode=crop_ocr_mode,
                debug_crop_dir=vision_debug_crop_dir,
                on_stage_update=report_live_stage,
            )
            parent_name_crop_repairs = _repair_parent_names_from_field_crop_alternatives(review_data)
            duplicate_name_repairs = _repair_duplicate_parent_names_from_crop_alternatives(review_data)
            crop_ocr_rescued_fields.extend(parent_name_crop_repairs)
            crop_ocr_rescued_fields.extend(duplicate_name_repairs)
            finish_timing_stage("crop_ocr_rescue", stage_started_at)
        if _rescue_mode_enabled(vision_rescue_mode):
            report_live_stage("vision_field_rescue")
            stage_started_at = perf_counter()
            review_data, vision_rescued_fields, vision_errors = _apply_vision_tr_field_rescue(
                review_data=review_data,
                page_image=page_image,
                cleaned_image=cleaned_image_for_vision,
                active_settings=active_settings,
                mode=vision_rescue_mode,
                debug_crop_dir=vision_debug_crop_dir,
                on_stage_update=report_live_stage,
            )
            report_live_stage("vision_field_rescue", "duplicate_parent_id")
            duplicate_parent_repairs, duplicate_parent_errors = _retry_duplicate_parent_ids_with_row_crops(
                review_data=review_data,
                page_image=page_image,
                cleaned_image=cleaned_image_for_vision,
                active_settings=active_settings,
                debug_crop_dir=vision_debug_crop_dir,
            )
            vision_rescued_fields.extend(duplicate_parent_repairs)
            vision_errors.extend(duplicate_parent_errors)
            finish_timing_stage("vision_field_rescue", stage_started_at)

        report_live_stage("local_validation")
        stage_started_at = perf_counter()
        if _manager_env_flag("TR_PARENT_NAME_REVIEW_REQUIRED", False):
            _mark_unconfirmed_name_fields_for_review(
                review_data,
                active_settings=active_settings,
            )
        derived_age = _reconcile_age_from_dates(review_data)
        if derived_age is not None:
            derived_fields.append(derived_age)
        address_house_number_repair = _repair_tr_address_house_number_from_raw_ocr(review_data, markdown)
        if address_house_number_repair is not None:
            address_house_number_repairs.append(address_house_number_repair)
        address_reference_result = _apply_tr_address_reference_validation(review_data)
        if address_reference_result is not None:
            address_reference_fields.append(address_reference_result)
        address_numeric_reapply = _reapply_address_numeric_crop_after_location_reference(
            review_data,
            crop_ocr_rescued_fields,
        )
        if address_numeric_reapply is not None:
            crop_ocr_rescued_fields.append(address_numeric_reapply)
        finish_timing_stage("local_validation", stage_started_at)
        if _rescue_mode_enabled(vision_rescue_mode):
            report_live_stage("vision_age_rescue", "age")
            stage_started_at = perf_counter()
            vision_age_rescue, vision_age_error = _rescue_missing_age_with_vision(
                review_data=review_data,
                page_image=page_image,
                cleaned_image=cleaned_image_for_vision,
                active_settings=active_settings,
                debug_crop_dir=vision_debug_crop_dir,
            )
            if vision_age_rescue is not None:
                vision_rescued_fields.append(vision_age_rescue)
            if vision_age_error:
                vision_errors.append(vision_age_error)
            finish_timing_stage("vision_age_rescue", stage_started_at)

        report_live_stage("field_decision_summary")
        stage_started_at = perf_counter()
        field_decision_map = _build_tr_field_decision_map(
            review_data=review_data,
            parser_snapshot=parser_field_snapshot,
            derived_fields=derived_fields,
            crop_ocr_rescued_fields=crop_ocr_rescued_fields,
            vision_rescued_fields=vision_rescued_fields,
            visual_dash_fields=visual_dash_fields,
        )
        finish_timing_stage("field_decision_summary", stage_started_at)
    raw_fields = review_data.get("fields") if isinstance(review_data, dict) else {}
    fields = raw_fields if isinstance(raw_fields, dict) else {}
    field_validation_issues = _build_tr_field_validation_issues(review_data) if isinstance(review_data, dict) else []
    processed_at = _utc_now()
    processing_timing = {
        "unit": "ms",
        "stages_ms": processing_stage_ms,
        "total_before_persistence_ms": round((perf_counter() - processing_started_at) * 1000, 1),
    }
    record["ocr_quality"] = (
        "low_confidence"
        if len(markdown.strip()) < 40
        else "needs_review"
        if (
            field_validation_issues
            or crop_ocr_errors
            or vision_errors
            or _has_actionable_suspicious_reasons(suspicious_reasons)
        )
        else "auto_verified"
    )
    record["field_validation_issues"] = field_validation_issues
    record["ocr_candidate_outputs"] = {
        "selected_source": selected_source,
        "selected_candidate_source": selected_candidate_source,
        "candidate_scores": ocr_candidate_scores,
        "suspicious_reasons": suspicious_reasons,
        "visual_dash_fields": visual_dash_fields,
        "rescue_modes": {
            "crop_ocr": crop_ocr_mode,
            "vision": vision_rescue_mode,
        },
        "field_decisions": field_decision_map,
        "derived_fields": derived_fields,
        "address_house_number_repairs": address_house_number_repairs,
        "address_reference_fields": address_reference_fields,
        "crop_ocr_rescued_fields": crop_ocr_rescued_fields,
        "crop_ocr_errors": crop_ocr_errors,
        "vision_rescued_fields": vision_rescued_fields,
        "vision_errors": vision_errors,
        "processing_timing": processing_timing,
        "vision_debug_crop_dir": _relative_storage_path(vision_debug_crop_dir) if vision_debug_crop_dir.exists() else None,
    }
    record["field_decision_map"] = field_decision_map
    record["processing_timing"] = processing_timing
    record["field_source_map"] = {
        field_name: field.get("source")
        for field_name, field in fields.items()
        if isinstance(field, dict)
    }
    if vision_debug_crop_dir.exists():
        record["vision_debug_crop_dir"] = _relative_storage_path(vision_debug_crop_dir)
    record["review_data"] = review_data
    ocr_metadata = {
        "record_id": record.get("record_id"),
        "batch_id": record.get("batch_id"),
        "file_id": record.get("file_id"),
        "page_number": page_number,
        "ocr_input_path": record["ocr_input_path"],
        "has_watermark": record["has_watermark"],
        "watermark_score": record["watermark_score"],
        "watermark_kind": record["watermark_kind"],
        "cleaned_page_path": record["cleaned_page_path"],
        "selected_source": selected_source,
        "selected_score": selected_score,
        "selected_ocr_model": selected_ocr_model,
        "selected_candidate_source": selected_candidate_source,
        "ocr_candidate_scores": ocr_candidate_scores,
        "original_score": original_score,
        "cleaned_score": cleaned_score,
        "diff_similarity": diff_similarity,
        "suspicious_reasons": suspicious_reasons,
        "visual_dash_fields": visual_dash_fields,
        "field_decisions": field_decision_map,
        "derived_fields": derived_fields,
        "crop_ocr_rescued_fields": crop_ocr_rescued_fields,
        "crop_ocr_errors": crop_ocr_errors,
        "vision_rescued_fields": vision_rescued_fields,
        "vision_errors": vision_errors,
        "processing_timing": processing_timing,
        "ocr_quality": record["ocr_quality"],
        "field_validation_issues": field_validation_issues,
        "processed_at": processed_at,
    }
    ocr_output_path, ocr_metadata_path = _save_ocr_record_outputs(
        record=record,
        markdown=markdown,
        ocr_output_dir=ocr_output_dir,
        metadata=ocr_metadata,
    )

    record["ocr_text"] = markdown
    record["ocr_result"] = markdown
    record["original_ocr_markdown"] = original_markdown
    record["cleaned_ocr_markdown"] = cleaned_markdown
    record["selected_markdown_source"] = selected_source
    record["selected_markdown_score"] = selected_score
    record["selected_ocr_model"] = selected_ocr_model
    record["selected_candidate_source"] = selected_candidate_source
    record["ocr_candidate_scores"] = ocr_candidate_scores
    record["original_markdown_score"] = original_score
    record["cleaned_markdown_score"] = cleaned_score
    record["original_ocr_error"] = original_error
    record["cleaned_ocr_error"] = cleaned_error
    record["diff_similarity"] = diff_similarity
    record["suspicious_reasons"] = suspicious_reasons
    record["crop_ocr_rescued_fields"] = crop_ocr_rescued_fields
    record["crop_ocr_errors"] = crop_ocr_errors
    record["vision_rescued_fields"] = vision_rescued_fields
    record["vision_errors"] = vision_errors
    record["ocr_current_stage"] = None
    record["ocr_output_path"] = ocr_output_path
    record["ocr_metadata_path"] = ocr_metadata_path
    record["ocr_status"] = "succeeded"
    record["review_status"] = "unassigned"
    record["processed_at"] = processed_at
    _upsert_staff_import_document(record)
    return record


def _batch_ocr_counts(records: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    pending = sum(1 for record in records if str(record.get("ocr_status") or "") == "pending")
    processing = sum(1 for record in records if str(record.get("ocr_status") or "") == "processing")
    succeeded = sum(1 for record in records if str(record.get("ocr_status") or "") == "succeeded")
    failed = sum(1 for record in records if str(record.get("ocr_status") or "") == "failed")
    return pending, processing, succeeded, failed


def _record_ocr_errors(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for record in records:
        error = str(record.get("ocr_error") or "").strip()
        if not error:
            continue
        errors.append(
            {
                "record_id": str(record.get("record_id") or ""),
                "file_id": str(record.get("file_id") or ""),
                "original_filename": str(record.get("original_filename") or ""),
                "page_number": str(record.get("page_number") or ""),
                "error": error,
            }
        )
    return errors


def _resolve_batch_ocr_status(records: list[dict[str, Any]]) -> str:
    pending, processing, succeeded, failed = _batch_ocr_counts(records)
    if pending or processing:
        return "ocr_processing"
    if failed and succeeded:
        return "partially_failed"
    if failed:
        return "failed"
    return "ocr_completed" if succeeded else "records_created"


def _apply_batch_ocr_summary(metadata: dict[str, Any], records: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    pending, processing, succeeded, failed = _batch_ocr_counts(records)
    ready_to_assign = sum(
        1
        for record in records
        if str(record.get("ocr_status") or "") == "succeeded"
        and str(record.get("review_status") or "") == "unassigned"
    )
    assigned_count = sum(
        1
        for record in records
        if str(record.get("review_status") or "") == "assigned"
    )
    unassigned_count = sum(
        1
        for record in records
        if str(record.get("review_status") or "") == "unassigned"
    )
    in_review_count = sum(
        1
        for record in records
        if str(record.get("review_status") or "") == "in_review"
    )
    completed_count = sum(
        1
        for record in records
        if str(record.get("review_status") or "") == "completed"
    )
    metadata["status"] = _resolve_batch_ocr_status(records)
    metadata["ocr_pending_count"] = pending
    metadata["ocr_processing_count"] = processing
    metadata["ocr_succeeded_count"] = succeeded
    metadata["ocr_failed_count"] = failed
    metadata["ready_to_assign_count"] = ready_to_assign
    metadata["assigned_count"] = assigned_count
    metadata["unassigned_count"] = unassigned_count
    metadata["in_review_count"] = in_review_count
    metadata["completed_count"] = completed_count
    metadata["ocr_last_processed_at"] = _utc_now()
    metadata["records"] = records
    return pending, processing, succeeded, failed


def _write_batch_ocr_progress(metadata: dict[str, Any], records: list[dict[str, Any]]) -> None:
    _apply_batch_ocr_summary(metadata, records)
    _write_metadata(_metadata_path_for_batch(str(metadata.get("batch_id") or "")), metadata)


def _process_batch_ocr_sync(batch_id: str) -> ManagerBatchOcrResponse:
    metadata = _load_batch_metadata(batch_id)
    if str(metadata.get("selected_document_type") or "").strip().upper() != SUPPORTED_DOCUMENT_TYPE:
        raise HTTPException(status_code=400, detail="Only TR batches can be processed")

    raw_records = metadata.get("records")
    if not isinstance(raw_records, list):
        raise HTTPException(status_code=400, detail="Batch has no page records to process")

    records = [record for record in raw_records if isinstance(record, dict)]
    processable_records = [
        record
        for record in records
        if str(record.get("selected_document_type") or "").strip().upper() == SUPPORTED_DOCUMENT_TYPE
        and str(record.get("ocr_status") or "") == "pending"
    ]
    if not processable_records:
        pending, processing, succeeded, failed = _apply_batch_ocr_summary(metadata, records)
        _write_metadata(_metadata_path_for_batch(batch_id), metadata)
        return ManagerBatchOcrResponse(
            batch_id=batch_id,
            status=str(metadata["status"]),
            total_processed=0,
            succeeded_count=succeeded,
            failed_count=failed,
            pending_count=pending,
            processing_count=processing,
            ready_to_assign_count=int(metadata.get("ready_to_assign_count") or 0),
            errors=_record_ocr_errors(records),
        )

    active_settings = load_settings()
    processed_count = 0
    for record in processable_records:
        try:
            _process_tr_ocr_record(
                record,
                active_settings,
                on_processing_started=lambda: _write_batch_ocr_progress(metadata, records),
            )
        except Exception as exc:
            record["ocr_status"] = "failed"
            record["ocr_error"] = str(exc)
            record["ocr_current_stage"] = None
            record["review_status"] = "unassigned"
            record["processed_at"] = _utc_now()
            _upsert_staff_import_document(record)
        finally:
            processed_count += 1
            pending, processing, succeeded, failed = _apply_batch_ocr_summary(metadata, records)
            _write_metadata(_metadata_path_for_batch(batch_id), metadata)

    pending, processing, succeeded, failed = _apply_batch_ocr_summary(metadata, records)
    _write_metadata(_metadata_path_for_batch(batch_id), metadata)

    return ManagerBatchOcrResponse(
        batch_id=batch_id,
        status=str(metadata["status"]),
        total_processed=processed_count,
        succeeded_count=succeeded,
        failed_count=failed,
        pending_count=pending,
        processing_count=processing,
        ready_to_assign_count=int(metadata.get("ready_to_assign_count") or 0),
        errors=_record_ocr_errors(records),
    )


def _process_batch_ocr_background(batch_id: str) -> None:
    try:
        _process_batch_ocr_sync(batch_id)
    except Exception as exc:
        print(f"[manager-upload] automatic OCR failed for batch {batch_id}: {exc}", flush=True)


@router.post("/uploads", response_model=ManagerUploadBatchResponse, status_code=201)
async def create_manager_upload_batch(
    background_tasks: BackgroundTasks,
    selected_document_type: str = Form(...),
    files: list[UploadFile] = File(...),
    current_user: AuthenticatedUser = Depends(require_manager_user),
) -> ManagerUploadBatchResponse:
    normalized_document_type = _validate_upload_request(selected_document_type, files)
    batch_id = uuid4().hex
    batch_original_dir = ORIGINAL_ROOT / batch_id
    batch_derived_dir = DERIVED_ROOT / batch_id
    metadata_path = METADATA_ROOT / batch_id / "metadata.json"
    created_at = datetime.now(timezone.utc).isoformat()
    saved_files: list[ManagerUploadFileResponse] = []
    records: list[ManagerUploadRecordResponse] = []

    try:
        for upload in files:
            file_id = uuid4().hex
            stored_filename = f"{file_id}.pdf"
            original_path = batch_original_dir / stored_filename
            file_derived_root = batch_derived_dir / file_id

            for folder_name in DERIVED_SUBFOLDERS:
                (file_derived_root / folder_name).mkdir(parents=True, exist_ok=True)

            file_size = await asyncio.to_thread(_save_uploaded_file, upload, original_path)
            original_path_value = _relative_storage_path(original_path)
            derived_root_value = _relative_storage_path(file_derived_root)
            original_filename = _safe_original_filename(upload.filename)

            page_count: int | None
            file_status = "records_created"
            error_message: str | None = None
            try:
                page_count = await asyncio.to_thread(_count_pdf_pages, original_path)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unable to read PDF page count for {original_filename}: {exc}",
                ) from exc

            if page_count is not None and page_count > MAX_PAGES_PER_PDF:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Record limit exceeded: {original_filename} has {page_count} pages. "
                        f"Maximum is {MAX_PAGES_PER_PDF} records/pages per PDF. "
                        "Please split the PDF and upload again."
                    ),
                )

            if page_count is not None and page_count <= MAX_PAGES_PER_PDF:
                projected_record_count = len(records) + page_count
                if projected_record_count > MAX_RECORDS_PER_BATCH:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Record limit exceeded: this upload would create "
                            f"{projected_record_count} records. Maximum records per batch "
                            f"is {MAX_RECORDS_PER_BATCH}."
                        ),
                    )
                for page_number in range(1, page_count + 1):
                    record_sequence = len(records) + 1
                    records.append(
                        ManagerUploadRecordResponse(
                            record_id=uuid4().hex,
                            record_no=_format_record_no(batch_id, record_sequence),
                            batch_id=batch_id,
                            file_id=file_id,
                            original_filename=original_filename,
                            selected_document_type=normalized_document_type,
                            page_number=page_number,
                            original_path=original_path_value,
                            derived_root=derived_root_value,
                            page_asset_path=None,
                            cleaned_page_path=None,
                            has_watermark=None,
                            ocr_text=None,
                            ocr_result=None,
                            ocr_error=None,
                            processed_at=None,
                            ocr_status="pending",
                            review_status="unassigned",
                            assigned_to_user_id=None,
                            assigned_to_username=None,
                            assigned_by_user_id=None,
                            assigned_by_username=None,
                            assigned_at=None,
                            save_btn="N",
                            created_at=created_at,
                        )
                    )

            saved_files.append(
                ManagerUploadFileResponse(
                    file_id=file_id,
                    original_filename=original_filename,
                    stored_filename=stored_filename,
                    mime_type=upload.content_type,
                    file_size_bytes=file_size,
                    page_count=page_count,
                    original_path=original_path_value,
                    derived_root=derived_root_value,
                    status=file_status,
                    error_message=error_message,
                )
            )

        failed_files = [file for file in saved_files if file.status != "records_created"]
        batch_status = "records_created"
        if failed_files and not records:
            batch_status = "page_limit_exceeded"
        ocr_pending_count = len(records)

        metadata = {
            "batch_id": batch_id,
            "selected_document_type": normalized_document_type,
            "status": batch_status,
            "created_by": current_user.user_id or current_user.username,
            "created_by_username": current_user.username,
            "created_at": created_at,
            "file_count": len(saved_files),
            "total_pages": len(records),
            "record_count": len(records),
            "ocr_pending_count": ocr_pending_count,
            "ocr_processing_count": 0,
            "ocr_succeeded_count": 0,
            "ocr_failed_count": 0,
            "ready_to_assign_count": 0,
            "assigned_count": 0,
            "unassigned_count": len(records),
            "in_review_count": 0,
            "completed_count": 0,
            "files": [file.model_dump() for file in saved_files],
            "records": [record.model_dump() for record in records],
            "notes": {
                "page_asset_generation": "automatic_during_ocr",
                "ocr": "automatic_after_records_created" if records else "not_started",
                "watermark_detection": "automatic_during_ocr" if records else "not_started",
            },
        }
        await asyncio.to_thread(_write_metadata, metadata_path, metadata)
        await asyncio.to_thread(
            _upsert_staff_import_documents,
            [record.model_dump() for record in records],
        )
    except Exception:
        shutil.rmtree(batch_original_dir, ignore_errors=True)
        shutil.rmtree(batch_derived_dir, ignore_errors=True)
        shutil.rmtree(metadata_path.parent, ignore_errors=True)
        raise

    if records:
        background_tasks.add_task(_process_batch_ocr_background, batch_id)

    return ManagerUploadBatchResponse(
        batch_id=batch_id,
        selected_document_type=normalized_document_type,
        status=batch_status,
        file_count=len(saved_files),
        total_pages=len(records),
        record_count=len(records),
        ocr_pending_count=len(records),
        ocr_processing_count=0,
        ocr_succeeded_count=0,
        ocr_failed_count=0,
        ready_to_assign_count=0,
        assigned_count=0,
        unassigned_count=len(records),
        in_review_count=0,
        completed_count=0,
        files=saved_files,
        records=records,
    )


def _is_record_ready_to_assign(record: dict[str, Any]) -> bool:
    return (
        str(record.get("ocr_status") or "") == "succeeded"
        and str(record.get("review_status") or "") == "unassigned"
        and not str(record.get("assigned_to_user_id") or "").strip()
        and not str(record.get("assigned_to_username") or "").strip()
    )


def _ready_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if _is_record_ready_to_assign(record)]


def _assign_batch_records(
    *,
    batch_id: str,
    staff_user_id: str | None,
    staff_username: str | None,
    count: int,
    assigned_by: AuthenticatedUser,
) -> ManagerUploadBatchResponse:
    metadata = _load_batch_metadata(batch_id)
    raw_records = metadata.get("records")
    if not isinstance(raw_records, list):
        raise HTTPException(status_code=400, detail="Batch has no page records to assign")

    records = [record for record in raw_records if isinstance(record, dict)]
    normalized_staff_user_id = (staff_user_id or "").strip()
    normalized_staff_username = (staff_username or "").strip()
    if not normalized_staff_user_id and not normalized_staff_username:
        raise HTTPException(status_code=400, detail="staff_user_id or staff_username is required")
    if count < 1:
        raise HTTPException(status_code=400, detail="count must be greater than zero")

    ready_records = _ready_records(records)
    if count > len(ready_records):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot assign {count} records; only {len(ready_records)} are ready to assign.",
        )

    assigned_at = _utc_now()
    for record in ready_records[:count]:
        record["review_status"] = "assigned"
        record["assigned_to_user_id"] = normalized_staff_user_id or normalized_staff_username
        record["assigned_to_username"] = normalized_staff_username or normalized_staff_user_id
        record["assigned_by_user_id"] = assigned_by.user_id or assigned_by.username
        record["assigned_by_username"] = assigned_by.username
        record["assigned_at"] = assigned_at
        _upsert_staff_import_document(record)

    _apply_batch_ocr_summary(metadata, records)
    metadata["updated_at"] = assigned_at
    _write_metadata(_metadata_path_for_batch(batch_id), metadata)
    return _build_batch_response(metadata)


def _assign_selected_records(
    *,
    record_ids: list[str],
    staff_user_id: str | None,
    staff_username: str | None,
    assigned_by: AuthenticatedUser,
) -> ManagerDashboardResponse:
    normalized_record_ids = list(dict.fromkeys(record_id.strip() for record_id in record_ids if record_id.strip()))
    if not normalized_record_ids:
        raise HTTPException(status_code=400, detail="At least one record_id is required")

    normalized_staff_user_id = (staff_user_id or "").strip()
    normalized_staff_username = (staff_username or "").strip()
    if not normalized_staff_user_id and not normalized_staff_username:
        raise HTTPException(status_code=400, detail="staff_user_id or staff_username is required")

    selected_ids = set(normalized_record_ids)
    matched_records: list[dict[str, Any]] = []
    touched_batches: dict[Path, tuple[dict[str, Any], list[dict[str, Any]]]] = {}

    for metadata_path in sorted(METADATA_ROOT.glob("*/metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        if _ensure_metadata_record_numbers(metadata):
            _write_metadata(metadata_path, metadata)
        raw_records = metadata.get("records")
        if not isinstance(raw_records, list):
            continue

        records = [record for record in raw_records if isinstance(record, dict)]
        for record in records:
            record_id = str(record.get("record_id") or "")
            if record_id not in selected_ids:
                continue
            matched_records.append(record)
            touched_batches[metadata_path] = (metadata, records)

    matched_ids = {str(record.get("record_id") or "") for record in matched_records}
    missing_ids = [record_id for record_id in normalized_record_ids if record_id not in matched_ids]
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Selected records were not found: {', '.join(missing_ids)}",
        )

    blocked_ids = [
        str(record.get("record_id") or "")
        for record in matched_records
        if not _is_record_ready_to_assign(record)
    ]
    if blocked_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Selected records are no longer ready to assign: {', '.join(blocked_ids)}",
        )

    assigned_at = _utc_now()
    for record in matched_records:
        record["review_status"] = "assigned"
        record["assigned_to_user_id"] = normalized_staff_user_id or normalized_staff_username
        record["assigned_to_username"] = normalized_staff_username or normalized_staff_user_id
        record["assigned_by_user_id"] = assigned_by.user_id or assigned_by.username
        record["assigned_by_username"] = assigned_by.username
        record["assigned_at"] = assigned_at
        _upsert_staff_import_document(record)

    for metadata_path, (metadata, records) in touched_batches.items():
        _apply_batch_ocr_summary(metadata, records)
        metadata["updated_at"] = assigned_at
        _write_metadata(metadata_path, metadata)

    return _build_manager_dashboard_response()


def _manager_self_as_assignee(current_user: AuthenticatedUser) -> StaffUser:
    return StaffUser(
        user_id=current_user.user_id or current_user.username,
        username=current_user.username or current_user.user_id,
        display_name=current_user.username or current_user.user_id,
    )


def _resolve_review_assignee(
    *,
    staff_users: list[StaffUser],
    current_user: AuthenticatedUser,
    requested_user_id: str,
    requested_username: str,
) -> StaffUser | None:
    candidates = [*staff_users, _manager_self_as_assignee(current_user)]
    return next(
        (
            candidate
            for candidate in candidates
            if (
                requested_user_id
                and candidate.user_id == requested_user_id
            )
            or (
                requested_username
                and candidate.username == requested_username
            )
        ),
        None,
    )


def _list_staff_assigned_records(current_user: AuthenticatedUser) -> list[StaffAssignedRecordResponse]:
    assigned_records: list[StaffAssignedRecordResponse] = []
    current_user_ids = {current_user.user_id, current_user.username}
    for metadata_path in sorted(METADATA_ROOT.glob("*/metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        if _ensure_metadata_record_numbers(metadata):
            _write_metadata(metadata_path, metadata)
        records = metadata.get("records")
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            assignee_values = {
                str(record.get("assigned_to_user_id") or ""),
                str(record.get("assigned_to_username") or ""),
            }
            if current_user_ids.isdisjoint(assignee_values):
                continue
            assigned_records.append(
                StaffAssignedRecordResponse(
                    record_id=str(record.get("record_id") or ""),
                    record_no=str(record.get("record_no") or ""),
                    batch_id=str(record.get("batch_id") or metadata.get("batch_id") or ""),
                    file_id=str(record.get("file_id") or ""),
                    original_filename=str(record.get("original_filename") or ""),
                    selected_document_type=str(record.get("selected_document_type") or ""),
                    page_number=int(record.get("page_number") or 0),
                    ocr_status=str(record.get("ocr_status") or ""),
                    review_status=str(record.get("review_status") or ""),
                    assigned_to_user_id=record.get("assigned_to_user_id"),
                    assigned_to_username=record.get("assigned_to_username"),
                    assigned_at=record.get("assigned_at"),
                    processed_at=record.get("processed_at"),
                )
            )
    return sorted(assigned_records, key=lambda record: record.assigned_at or "", reverse=True)


def _record_belongs_to_staff(record: dict[str, Any], current_user: AuthenticatedUser) -> bool:
    assignee_values = {
        str(record.get("assigned_to_user_id") or ""),
        str(record.get("assigned_to_username") or ""),
    }
    return not {current_user.user_id, current_user.username}.isdisjoint(assignee_values)


def _find_staff_record(
    record_id: str,
    current_user: AuthenticatedUser,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    for metadata_path in sorted(METADATA_ROOT.glob("*/metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        if _ensure_metadata_record_numbers(metadata):
            _write_metadata(metadata_path, metadata)
        raw_records = metadata.get("records")
        if not isinstance(raw_records, list):
            continue
        records = [record for record in raw_records if isinstance(record, dict)]
        for record in records:
            if str(record.get("record_id") or "") != record_id:
                continue
            if not _record_belongs_to_staff(record, current_user):
                raise HTTPException(status_code=404, detail="Assigned record not found")
            if str(record.get("ocr_status") or "") != "succeeded":
                raise HTTPException(status_code=404, detail="Assigned record not found")
            return metadata_path, metadata, records, record
    raise HTTPException(status_code=404, detail="Assigned record not found")


def _find_manager_record(
    record_id: str,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    for metadata_path in sorted(METADATA_ROOT.glob("*/metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        raw_records = metadata.get("records")
        if not isinstance(raw_records, list):
            continue
        records = [record for record in raw_records if isinstance(record, dict)]
        for record in records:
            if str(record.get("record_id") or "") != record_id:
                continue
            if str(record.get("ocr_status") or "") != "succeeded":
                raise HTTPException(status_code=404, detail="Record is not ready for review")
            return metadata_path, metadata, records, record
    raise HTTPException(status_code=404, detail="Record not found")


def _build_staff_record_detail(record: dict[str, Any]) -> StaffRecordDetailResponse:
    record_id = str(record.get("record_id") or "")
    return StaffRecordDetailResponse(
        record_id=record_id,
        record_no=str(record.get("record_no") or ""),
        batch_id=str(record.get("batch_id") or ""),
        file_id=str(record.get("file_id") or ""),
        original_filename=str(record.get("original_filename") or ""),
        selected_document_type=str(record.get("selected_document_type") or ""),
        page_number=int(record.get("page_number") or 0),
        original_path=str(record.get("original_path") or ""),
        derived_root=str(record.get("derived_root") or ""),
        page_asset_path=record.get("page_asset_path"),
        cleaned_page_path=record.get("cleaned_page_path"),
        ocr_input_path=record.get("ocr_input_path"),
        has_watermark=record.get("has_watermark"),
        ocr_status=str(record.get("ocr_status") or ""),
        review_status=str(record.get("review_status") or ""),
        assigned_to_user_id=record.get("assigned_to_user_id"),
        assigned_to_username=record.get("assigned_to_username"),
        assigned_at=record.get("assigned_at"),
        processed_at=record.get("processed_at"),
        ocr_text=record.get("ocr_text"),
        ocr_result=record.get("ocr_result"),
        corrected_result=record.get("corrected_result"),
        preview_url=f"/api/staff/records/{record_id}/preview",
        completed_at=record.get("completed_at"),
        updated_at=record.get("updated_at"),
    )


def _stringify_review_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _normalize_save_btn(value: Any) -> str:
    normalized = str(value or "N").strip().upper()
    return "Y" if normalized == "Y" else "N"


def _record_save_btn_is_yes(record: dict[str, Any]) -> bool:
    return _normalize_save_btn(record.get("save_btn")) == "Y"


def _staff_import_status(record: dict[str, Any]) -> ImportStatus:
    if _record_save_btn_is_yes(record):
        return ImportStatus.checked
    ocr_status = str(record.get("ocr_status") or "")
    if ocr_status == "pending":
        return ImportStatus.ocr_queued
    if ocr_status == "processing":
        return ImportStatus.ocr_running
    if ocr_status == "failed":
        return ImportStatus.ocr_failed
    return ImportStatus.ready_for_review


def _build_staff_import_record(record: dict[str, Any]) -> ImportRecord:
    record_id = str(record.get("record_id") or "")
    page_number = int(record.get("page_number") or 1)
    corrected_result = record.get("corrected_result")
    corrected_markdown = "" if isinstance(corrected_result, dict) else _stringify_review_value(corrected_result)
    markdown = corrected_markdown or str(record.get("ocr_result") or record.get("ocr_text") or "")
    original_preview_path = str(record.get("page_asset_path") or record.get("ocr_input_path") or "")
    cleaned_preview_path = str(record.get("cleaned_page_path") or record.get("ocr_input_path") or original_preview_path)
    original_markdown = str(record.get("original_ocr_markdown") or record.get("ocr_text") or "")
    cleaned_markdown = str(record.get("cleaned_ocr_markdown") or record.get("ocr_result") or record.get("ocr_text") or "")
    selected_source = str(
        record.get("selected_markdown_source")
        or ("manual" if record.get("corrected_result") else "cleaned")
    )
    ocr_candidate_scores = record.get("ocr_candidate_scores")
    if not isinstance(ocr_candidate_scores, list):
        ocr_candidate_scores = []
    suspicious_reasons = record.get("suspicious_reasons")
    if not isinstance(suspicious_reasons, list):
        suspicious_reasons = []
    now = str(record.get("updated_at") or record.get("processed_at") or record.get("created_at") or _utc_now())

    pages = [
        {
            "page_number": page_number,
            "original_preview_path": original_preview_path,
            "cleaned_preview_path": cleaned_preview_path,
            "watermark_detected": record.get("has_watermark"),
            "watermark_score": record.get("watermark_score"),
            "cleaning_mode": record.get("cleaning_mode"),
            "markdown": markdown,
            "raw_markdown": str(record.get("ocr_text") or ""),
            "corrected_markdown": markdown,
            "original_markdown": original_markdown,
            "cleaned_markdown": cleaned_markdown,
            "selected_markdown_source": selected_source,
            "selected_markdown_score": record.get("selected_markdown_score"),
            "selected_ocr_model": record.get("selected_ocr_model"),
            "selected_candidate_source": record.get("selected_candidate_source"),
            "ocr_candidate_scores": ocr_candidate_scores,
            "original_markdown_score": record.get("original_markdown_score"),
            "cleaned_markdown_score": record.get("cleaned_markdown_score"),
            "correction_model": None,
            "correction_error": None,
            "correction_similarity": None,
            "original_ocr_error": record.get("original_ocr_error") or record.get("ocr_error"),
            "cleaned_ocr_error": record.get("cleaned_ocr_error") or record.get("ocr_error"),
            "diff_similarity": record.get("diff_similarity"),
            "suspicious_reasons": suspicious_reasons,
            "processing_timing": record.get("processing_timing")
            if isinstance(record.get("processing_timing"), dict)
            else None,
            "ocr_current_stage": record.get("ocr_current_stage")
            if isinstance(record.get("ocr_current_stage"), dict)
            else None,
            "segments": [],
        }
    ]

    review_data = _build_tr_review_data_from_corrected_result(corrected_result)
    if review_data is None:
        stored_review_data = record.get("review_data")
        stored_version = (
            int(stored_review_data.get("version") or 0)
            if isinstance(stored_review_data, dict)
            else 0
        )
        review_data = (
            stored_review_data
            if isinstance(stored_review_data, dict) and stored_version >= TR_REVIEW_VERSION
            else build_tr_review_data(pages)
        )

    return ImportRecord(
        id=record_id,
        source_filename=str(record.get("original_filename") or record_id),
        document_category=str(record.get("selected_document_type") or "TR"),
        source_path=str(record.get("original_path") or ""),
        cleaned_file_path=str(record.get("cleaned_page_path") or record.get("ocr_input_path") or ""),
        source_fingerprint=record_id,
        status=_staff_import_status(record),
        total_pages=1,
        created_at=str(record.get("created_at") or now),
        updated_at=now,
        checked_at=record.get("completed_at"),
        checked_by=record.get("assigned_to_username"),
        save_btn=_normalize_save_btn(record.get("save_btn")),
        review_status=str(record.get("review_status") or ""),
        assigned_to_user_id=record.get("assigned_to_user_id"),
        assigned_to_username=record.get("assigned_to_username"),
        assigned_at=record.get("assigned_at"),
        note=None,
        ocr_markdown=markdown,
        raw_ocr_markdown=str(record.get("ocr_text") or ""),
        corrected_ocr_markdown=markdown,
        original_ocr_markdown=str(record.get("ocr_text") or ""),
        cleaned_ocr_markdown=str(record.get("ocr_result") or record.get("ocr_text") or ""),
        correction_model=None,
        ocr_error_message=record.get("ocr_error"),
        ocr_completed_at=record.get("processed_at"),
        ocr_quality=record.get("ocr_quality"),
        field_validation_issues=record.get("field_validation_issues")
        if isinstance(record.get("field_validation_issues"), list)
        else [],
        review_data=review_data,
        pages=pages,
    )


def _build_tr_review_data_from_corrected_result(corrected_result: Any | None) -> dict[str, Any] | None:
    if not isinstance(corrected_result, dict):
        return None
    raw_fields = corrected_result.get("fields")
    if not isinstance(raw_fields, dict):
        return None

    generated = build_tr_review_data([])
    fields = generated.get("fields")
    if not isinstance(fields, dict):
        return None

    for key, value in raw_fields.items():
        if not isinstance(key, str) or key not in fields:
            continue
        field = fields.get(key)
        if not isinstance(field, dict):
            continue
        if isinstance(value, dict):
            field["value"] = value.get("value")
            field["pageNumber"] = value.get("pageNumber")
            field["bbox"] = value.get("bbox")
            field["source"] = value.get("source") or "staff_verified"
            if value.get("reviewStatus"):
                field["reviewStatus"] = value.get("reviewStatus")
            if value.get("reviewNote"):
                field["reviewNote"] = value.get("reviewNote")
            if isinstance(value.get("appliedCorrections"), list):
                field["appliedCorrections"] = value.get("appliedCorrections")
        else:
            field["value"] = value
            field["source"] = "staff_verified"
            field["reviewStatus"] = "staff_verified"
    _apply_tr_address_reference_validation(generated)
    return generated


def _build_staff_import_document(record: dict[str, Any]) -> dict[str, Any]:
    import_record = _build_staff_import_record(record)
    document = import_record.model_dump()
    document["_id"] = import_record.id
    document["source_fingerprint"] = f"manager-review-record:{import_record.id}"
    document["manager_batch_id"] = str(record.get("batch_id") or "")
    document["manager_file_id"] = str(record.get("file_id") or "")
    document["manager_record_id"] = import_record.id
    document["ocr_status"] = str(record.get("ocr_status") or "")
    document["review_status"] = str(record.get("review_status") or "")
    document["assigned_to_user_id"] = record.get("assigned_to_user_id")
    document["assigned_to_username"] = record.get("assigned_to_username")
    document["assigned_by_user_id"] = record.get("assigned_by_user_id")
    document["assigned_by_username"] = record.get("assigned_by_username")
    document["assigned_at"] = record.get("assigned_at")
    document["completed_at"] = record.get("completed_at")
    document["save_btn"] = _normalize_save_btn(record.get("save_btn"))
    document["storage_source"] = "manager_upload"
    return document


def _load_staff_import_record(record_id: str) -> ImportRecord | None:
    document = get_imports_collection().find_one(
        {
            "_id": record_id,
            "storage_source": "manager_upload",
        }
    )
    if document is None:
        return None
    document["id"] = str(document.get("id") or document.get("_id") or record_id)
    document.pop("_id", None)
    return ImportRecord(**document)


def _has_tr_review_flags(import_record: ImportRecord) -> bool:
    review_data = import_record.review_data
    return isinstance(review_data, dict) and isinstance(review_data.get("flags"), dict)


def _upsert_staff_import_document(record: dict[str, Any]) -> None:
    record_id = str(record.get("record_id") or "")
    if not record_id:
        return
    document = _build_staff_import_document(record)
    get_imports_collection().replace_one({"_id": record_id}, document, upsert=True)


def _upsert_staff_import_documents(records: list[dict[str, Any]]) -> None:
    for record in records:
        _upsert_staff_import_document(record)


def _save_staff_record_progress(
    record_id: str,
    current_user: AuthenticatedUser,
    corrected_result: Any | None,
) -> StaffRecordDetailResponse:
    metadata_path, metadata, records, record = _find_staff_record(record_id, current_user)
    if _record_save_btn_is_yes(record):
        raise HTTPException(status_code=400, detail="Completed records cannot be edited")
    now = _utc_now()
    record["corrected_result"] = corrected_result
    record["review_status"] = "in_review"
    record["updated_at"] = now
    metadata["updated_at"] = now
    _apply_batch_ocr_summary(metadata, records)
    _write_metadata(metadata_path, metadata)
    _upsert_staff_import_document(record)
    return _build_staff_record_detail(record)


def _complete_staff_record(
    record_id: str,
    current_user: AuthenticatedUser,
    corrected_result: Any | None,
) -> StaffRecordDetailResponse:
    metadata_path, metadata, records, record = _find_staff_record(record_id, current_user)
    if _record_save_btn_is_yes(record):
        return _build_staff_record_detail(record)

    now = _utc_now()
    if corrected_result is not None:
        final_corrected_result = corrected_result
    elif record.get("corrected_result") is None:
        final_corrected_result = record.get("ocr_result") or record.get("ocr_text")
    else:
        final_corrected_result = record.get("corrected_result")

    if str(record.get("selected_document_type") or "").upper() == SUPPORTED_DOCUMENT_TYPE:
        if isinstance(final_corrected_result, dict):
            _apply_tr_address_reference_validation(final_corrected_result)
        learned_corrections = learn_tr_name_corrections_from_review(
            original_review_data=record.get("review_data"),
            corrected_result=final_corrected_result,
            updated_by=current_user.username,
        )
        if learned_corrections:
            record["learned_name_corrections"] = [
                {
                    "source": entry.get("source"),
                    "replacement": entry.get("replacement"),
                    "count": entry.get("count"),
                }
                for entry in learned_corrections
            ]
        insert_tr_upttr_from_review_result(
            final_corrected_result,
            updated_by=current_user.username,
        )

    record["corrected_result"] = final_corrected_result
    record["review_status"] = "completed"
    record["save_btn"] = "Y"
    record["completed_at"] = now
    record["updated_at"] = now
    metadata["updated_at"] = now
    _apply_batch_ocr_summary(metadata, records)
    _write_metadata(metadata_path, metadata)
    _upsert_staff_import_document(record)
    return _build_staff_record_detail(record)


def _get_staff_record_preview_path(record_id: str, current_user: AuthenticatedUser) -> Path:
    _, _, _, record = _find_staff_record(record_id, current_user)
    return _get_record_preview_path(record)


def _get_manager_record_preview_path(record_id: str) -> Path:
    _, _, _, record = _find_manager_record(record_id)
    return _get_record_preview_path(record)


def _get_record_preview_path(record: dict[str, Any]) -> Path:
    for field_name in ("page_asset_path", "cleaned_page_path", "ocr_input_path"):
        path_value = str(record.get(field_name) or "")
        if not path_value:
            continue
        path = _resolve_storage_path(path_value)
        if path.exists() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            return path

    original_path = _resolve_storage_path(str(record.get("original_path") or ""))
    derived_root = _resolve_storage_path(str(record.get("derived_root") or ""))
    page_number = int(record.get("page_number") or 0)
    if page_number < 1:
        raise HTTPException(status_code=404, detail="Record preview not found")
    preview_path = derived_root / "previews" / f"page-{page_number:03d}-staff-preview.png"
    if not preview_path.exists():
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        page_image = render_page_preview(original_path, page_number)
        page_image.save(preview_path, format="PNG")
        record["page_asset_path"] = _relative_storage_path(preview_path)
    return preview_path


@router.get("/dashboard", response_model=ManagerDashboardResponse)
async def get_manager_dashboard(
    current_user: AuthenticatedUser = Depends(require_manager_user),
) -> ManagerDashboardResponse:
    _ = current_user
    return await asyncio.to_thread(_build_manager_dashboard_response)


@router.get("/batches", response_model=list[ManagerUploadBatchResponse])
async def list_manager_batches(
    current_user: AuthenticatedUser = Depends(require_manager_user),
) -> list[ManagerUploadBatchResponse]:
    _ = current_user
    return await asyncio.to_thread(_load_all_batch_responses)


@router.get("/batches/{batch_id}", response_model=ManagerUploadBatchResponse)
async def get_manager_batch(
    batch_id: str,
    current_user: AuthenticatedUser = Depends(require_manager_user),
) -> ManagerUploadBatchResponse:
    _ = current_user
    metadata = await asyncio.to_thread(_load_batch_metadata, batch_id)
    return _build_batch_response(metadata)


@router.get("/staff", response_model=list[ManagerStaffUserResponse])
async def list_manager_staff(
    current_user: AuthenticatedUser = Depends(require_manager_user),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> list[ManagerStaffUserResponse]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        staff_users = await asyncio.to_thread(list_staff_users_from_auth_source, authorization)
    except HTTPException:
        staff_users = []
    assignees = [_manager_self_as_assignee(current_user), *staff_users]
    seen: set[str] = set()
    unique_assignees: list[StaffUser] = []
    for assignee in assignees:
        key = assignee.user_id or assignee.username
        if not key or key in seen:
            continue
        seen.add(key)
        unique_assignees.append(assignee)
    return [
        ManagerStaffUserResponse(
            user_id=staff_user.user_id,
            username=staff_user.username,
            display_name=staff_user.display_name,
        )
        for staff_user in unique_assignees
    ]


@router.post("/batches/{batch_id}/assign", response_model=ManagerUploadBatchResponse)
async def assign_manager_batch_records(
    batch_id: str,
    payload: ManagerBatchAssignRequest,
    current_user: AuthenticatedUser = Depends(require_manager_user),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ManagerUploadBatchResponse:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    requested_staff_user_id = (payload.staff_user_id or "").strip()
    requested_staff_username = (payload.staff_username or "").strip()
    selected_staff = _resolve_review_assignee(
        staff_users=[],
        current_user=current_user,
        requested_user_id=requested_staff_user_id,
        requested_username=requested_staff_username,
    )
    if selected_staff is None:
        staff_users = await asyncio.to_thread(list_staff_users_from_auth_source, authorization)
        selected_staff = _resolve_review_assignee(
            staff_users=staff_users,
            current_user=current_user,
            requested_user_id=requested_staff_user_id,
            requested_username=requested_staff_username,
        )
    if selected_staff is None:
        raise HTTPException(status_code=400, detail="Selected reviewer was not found")
    return await asyncio.to_thread(
        _assign_batch_records,
        batch_id=batch_id,
        staff_user_id=selected_staff.user_id,
        staff_username=selected_staff.username,
        count=payload.count,
        assigned_by=current_user,
    )


@router.post("/records/assign", response_model=ManagerDashboardResponse)
async def assign_manager_records(
    payload: ManagerRecordsAssignRequest,
    current_user: AuthenticatedUser = Depends(require_manager_user),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ManagerDashboardResponse:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    requested_staff_user_id = (payload.staff_user_id or "").strip()
    requested_staff_username = (payload.staff_username or "").strip()
    selected_staff = _resolve_review_assignee(
        staff_users=[],
        current_user=current_user,
        requested_user_id=requested_staff_user_id,
        requested_username=requested_staff_username,
    )
    if selected_staff is None:
        staff_users = await asyncio.to_thread(list_staff_users_from_auth_source, authorization)
        selected_staff = _resolve_review_assignee(
            staff_users=staff_users,
            current_user=current_user,
            requested_user_id=requested_staff_user_id,
            requested_username=requested_staff_username,
        )
    if selected_staff is None:
        raise HTTPException(status_code=400, detail="Selected reviewer was not found")
    return await asyncio.to_thread(
        _assign_selected_records,
        record_ids=payload.record_ids,
        staff_user_id=selected_staff.user_id,
        staff_username=selected_staff.username,
        assigned_by=current_user,
    )


@router.post("/batches/{batch_id}/process-ocr", response_model=ManagerBatchOcrResponse)
async def process_manager_batch_ocr(
    batch_id: str,
    current_user: AuthenticatedUser = Depends(require_manager_user),
) -> ManagerBatchOcrResponse:
    _ = current_user
    return await asyncio.to_thread(_process_batch_ocr_sync, batch_id)


@router.get("/records/{record_id}/import", response_model=ImportRecord)
async def get_manager_record_as_import(
    record_id: str,
    current_user: AuthenticatedUser = Depends(require_manager_user),
) -> ImportRecord:
    _ = current_user
    _, _, _, record = await asyncio.to_thread(_find_manager_record, record_id)
    await asyncio.to_thread(_upsert_staff_import_document, record)
    stored_record = await asyncio.to_thread(_load_staff_import_record, record_id)
    if stored_record is not None:
        return stored_record
    return await asyncio.to_thread(_build_staff_import_record, record)


@router.get("/records/{record_id}/preview")
async def get_manager_record_preview(
    record_id: str,
    current_user: AuthenticatedUser = Depends(require_manager_user),
) -> FileResponse:
    _ = current_user
    preview_path = await asyncio.to_thread(_get_manager_record_preview_path, record_id)
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="Record preview not found")
    return FileResponse(path=preview_path, media_type="image/png", headers={"Content-Disposition": "inline"})


@router.post("/records/{record_id}/complete", response_model=StaffRecordDetailResponse)
async def complete_manager_self_assigned_record(
    record_id: str,
    payload: StaffRecordCompletePayload,
    current_user: AuthenticatedUser = Depends(require_manager_user),
) -> StaffRecordDetailResponse:
    return await asyncio.to_thread(
        _complete_staff_record,
        record_id,
        current_user,
        payload.corrected_result,
    )


@staff_router.get("/records", response_model=list[StaffAssignedRecordResponse])
async def list_staff_records(
    current_user: AuthenticatedUser = Depends(require_staff_user),
) -> list[StaffAssignedRecordResponse]:
    return await asyncio.to_thread(_list_staff_assigned_records, current_user)


@staff_router.get("/records/{record_id}", response_model=StaffRecordDetailResponse)
async def get_staff_record(
    record_id: str,
    current_user: AuthenticatedUser = Depends(require_staff_user),
) -> StaffRecordDetailResponse:
    _, _, _, record = await asyncio.to_thread(_find_staff_record, record_id, current_user)
    return _build_staff_record_detail(record)


@staff_router.get("/records/{record_id}/import", response_model=ImportRecord)
async def get_staff_record_as_import(
    record_id: str,
    current_user: AuthenticatedUser = Depends(require_staff_user),
) -> ImportRecord:
    _, _, _, record = await asyncio.to_thread(_find_staff_record, record_id, current_user)
    await asyncio.to_thread(_upsert_staff_import_document, record)
    stored_record = await asyncio.to_thread(_load_staff_import_record, record_id)
    if stored_record is not None:
        return stored_record
    return await asyncio.to_thread(_build_staff_import_record, record)


@staff_router.get("/records/{record_id}/preview")
async def get_staff_record_preview(
    record_id: str,
    current_user: AuthenticatedUser = Depends(require_staff_user),
) -> FileResponse:
    preview_path = await asyncio.to_thread(_get_staff_record_preview_path, record_id, current_user)
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="Record preview not found")
    return FileResponse(path=preview_path, media_type="image/png", headers={"Content-Disposition": "inline"})


@staff_router.patch("/records/{record_id}/progress", response_model=StaffRecordDetailResponse)
async def save_staff_record_progress(
    record_id: str,
    payload: StaffRecordProgressPayload,
    current_user: AuthenticatedUser = Depends(require_staff_user),
) -> StaffRecordDetailResponse:
    return await asyncio.to_thread(
        _save_staff_record_progress,
        record_id,
        current_user,
        payload.corrected_result,
    )


@staff_router.post("/records/{record_id}/complete", response_model=StaffRecordDetailResponse)
async def complete_staff_record(
    record_id: str,
    payload: StaffRecordCompletePayload,
    current_user: AuthenticatedUser = Depends(require_staff_user),
) -> StaffRecordDetailResponse:
    return await asyncio.to_thread(
        _complete_staff_record,
        record_id,
        current_user,
        payload.corrected_result,
    )
