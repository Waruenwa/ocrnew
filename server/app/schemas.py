from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ImportStatus(str, Enum):
    uploaded = "uploaded"
    cleaning = "cleaning"
    ocr_queued = "ocr_queued"
    ocr_running = "ocr_running"
    ready_for_review = "ready_for_review"
    ocr_failed = "ocr_failed"
    # Legacy value kept for records created before the background OCR workflow.
    review_ready = "review_ready"
    checked = "checked"


class TextSegment(BaseModel):
    id: str
    text: str
    page_number: int
    bbox: tuple[float, float, float, float]
    bboxes: list[tuple[float, float, float, float]] | None = None
    raw_text: str | None = None
    corrected_text: str | None = None
    source_line_index: int | None = None
    source_row_index: int | None = None


class PageResult(BaseModel):
    page_number: int
    markdown: str
    segments: list[TextSegment] = Field(default_factory=list)


class ImportPageAsset(BaseModel):
    page_number: int
    original_preview_path: str
    cleaned_preview_path: str
    watermark_detected: bool | None = None
    watermark_score: float | None = None
    cleaning_mode: str | None = None
    markdown: str | None = None
    raw_markdown: str | None = None
    corrected_markdown: str | None = None
    original_markdown: str | None = None
    cleaned_markdown: str | None = None
    selected_markdown_source: str | None = None
    selected_markdown_score: float | None = None
    selected_ocr_model: str | None = None
    selected_candidate_source: str | None = None
    ocr_candidate_scores: list[dict[str, Any]] = Field(default_factory=list)
    original_markdown_score: float | None = None
    cleaned_markdown_score: float | None = None
    correction_model: str | None = None
    correction_error: str | None = None
    correction_similarity: float | None = None
    original_ocr_error: str | None = None
    cleaned_ocr_error: str | None = None
    diff_similarity: float | None = None
    suspicious_reasons: list[str] = Field(default_factory=list)
    processing_timing: dict[str, Any] | None = None
    ocr_current_stage: dict[str, Any] | None = None
    segments: list[TextSegment] = Field(default_factory=list)


class ImportRecord(BaseModel):
    id: str
    source_filename: str
    document_category: str | None = None
    source_path: str
    cleaned_file_path: str
    source_fingerprint: str
    status: ImportStatus
    total_pages: int = 1
    created_at: str
    updated_at: str
    checked_at: str | None = None
    checked_by: str | None = None
    save_btn: str | None = None
    review_status: str | None = None
    assigned_to_user_id: str | None = None
    assigned_to_username: str | None = None
    assigned_at: str | None = None
    note: str | None = None
    ocr_markdown: str | None = None
    raw_ocr_markdown: str | None = None
    corrected_ocr_markdown: str | None = None
    original_ocr_markdown: str | None = None
    cleaned_ocr_markdown: str | None = None
    correction_model: str | None = None
    ocr_error_message: str | None = None
    ocr_completed_at: str | None = None
    ocr_quality: str | None = None
    field_validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    review_data: dict[str, Any] | None = None
    pages: list[ImportPageAsset] = Field(default_factory=list)


class ImportCheckPayload(BaseModel):
    checked_by: str | None = None
    note: str | None = None


class ImportPageSavePayload(BaseModel):
    markdown: str = ""


class JobRecord(BaseModel):
    id: str
    filename: str
    mime_type: str | None = None
    status: JobStatus
    total_pages: int = 1
    processed_pages: int = 0
    extraction_prompt: str | None = None
    ocr_markdown: str | None = None
    structured_output: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None
    pages: list[PageResult] = Field(default_factory=list)


class AppConfigResponse(BaseModel):
    imports_source_dir: str
    ocr_ready: bool
    extraction_ready: bool
    vision_ready: bool
    ocr_model: str
    ocr_compare_models: list[str] = Field(default_factory=list)
    vision_model: str
    max_upload_mb: int
    text_model: str


class HealthResponse(BaseModel):
    status: str
