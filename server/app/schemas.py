from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class TextSegment(BaseModel):
    id: str
    text: str
    page_number: int
    bbox: tuple[float, float, float, float]


class PageResult(BaseModel):
    page_number: int
    markdown: str
    segments: list[TextSegment] = Field(default_factory=list)


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
    ocr_ready: bool
    extraction_ready: bool
    max_upload_mb: int
    text_model: str


class HealthResponse(BaseModel):
    status: str
