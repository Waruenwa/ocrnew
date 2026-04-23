from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.database import get_jobs_collection
from app.schemas import JobRecord, JobStatus, PageResult, TextSegment


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_job_record_from_document(document: dict[str, object]) -> JobRecord:
    pages_data = document.get("pages") or []
    assert isinstance(pages_data, list)
    pages: list[PageResult] = []
    for page_data in pages_data:
        if not isinstance(page_data, dict):
            continue
        raw_segments = page_data.get("segments") or []
        segments = [
            TextSegment(**segment)
            for segment in raw_segments
            if isinstance(segment, dict)
        ]
        pages.append(
            PageResult(
                page_number=int(page_data.get("page_number", 0)),
                markdown=str(page_data.get("markdown", "")),
                segments=segments,
            )
        )

    pages.sort(key=lambda page: page.page_number)
    return JobRecord(
        id=str(document.get("id") or document.get("_id")),
        filename=str(document.get("filename", "")),
        mime_type=document.get("mime_type"),
        status=JobStatus(str(document.get("status", JobStatus.queued.value))),
        total_pages=int(document.get("total_pages", 1)),
        processed_pages=int(document.get("processed_pages", 0)),
        extraction_prompt=document.get("extraction_prompt"),
        ocr_markdown=document.get("ocr_markdown"),
        structured_output=document.get("structured_output"),
        error_message=document.get("error_message"),
        created_at=str(document.get("created_at", "")),
        updated_at=str(document.get("updated_at", "")),
        completed_at=document.get("completed_at"),
        pages=pages,
    )


def create_job(
    *,
    job_id: str,
    filename: str,
    stored_path: Path,
    mime_type: str | None,
    total_pages: int,
    extraction_prompt: str | None,
) -> JobRecord:
    now = _utc_now()
    document = {
        "_id": job_id,
        "id": job_id,
        "filename": filename,
        "stored_path": str(stored_path),
        "mime_type": mime_type,
        "status": JobStatus.queued.value,
        "total_pages": total_pages,
        "processed_pages": 0,
        "extraction_prompt": extraction_prompt,
        "ocr_markdown": None,
        "structured_output": None,
        "error_message": None,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "pages": [],
    }
    get_jobs_collection().replace_one({"_id": job_id}, document, upsert=True)
    return _build_job_record_from_document(document)


def get_job(job_id: str) -> JobRecord | None:
    document = get_jobs_collection().find_one({"_id": job_id})
    if document is None:
        return None
    return _build_job_record_from_document(document)


def list_jobs(limit: int = 12) -> list[JobRecord]:
    cursor = (
        get_jobs_collection()
        .find()
        .sort("created_at", -1)
        .limit(limit)
    )
    return [_build_job_record_from_document(document) for document in cursor]


def get_job_file_path(job_id: str) -> Path | None:
    document = get_jobs_collection().find_one({"_id": job_id}, {"stored_path": 1})
    if document is None:
        return None
    stored_path = document.get("stored_path")
    if not isinstance(stored_path, str):
        return None
    return Path(stored_path)


def mark_processing(job_id: str) -> None:
    now = _utc_now()
    get_jobs_collection().update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": JobStatus.processing.value,
                "updated_at": now,
                "error_message": None,
            }
        },
    )


def save_page_result(
    job_id: str,
    page_number: int,
    markdown: str,
    segments: list[dict[str, object]],
) -> None:
    now = _utc_now()
    collection = get_jobs_collection()
    document = collection.find_one({"_id": job_id}, {"pages": 1})
    if document is None:
        return

    pages = document.get("pages") or []
    assert isinstance(pages, list)
    normalized_pages = [
        page
        for page in pages
        if isinstance(page, dict) and int(page.get("page_number", 0)) != page_number
    ]
    normalized_pages.append(
        {
            "page_number": page_number,
            "markdown": markdown,
            "segments": segments,
        }
    )
    normalized_pages.sort(key=lambda page: int(page.get("page_number", 0)))

    collection.update_one(
        {"_id": job_id},
        {
            "$set": {
                "pages": normalized_pages,
                "processed_pages": len(normalized_pages),
                "updated_at": now,
            }
        },
    )


def complete_job(job_id: str, markdown: str, structured_output: str | None) -> None:
    now = _utc_now()
    job = get_job(job_id)
    if job is None:
        return
    get_jobs_collection().update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": JobStatus.completed.value,
                "processed_pages": job.total_pages,
                "ocr_markdown": markdown,
                "structured_output": structured_output,
                "updated_at": now,
                "completed_at": now,
            }
        },
    )


def fail_job(job_id: str, error_message: str) -> None:
    now = _utc_now()
    get_jobs_collection().update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": JobStatus.failed.value,
                "error_message": error_message,
                "updated_at": now,
                "completed_at": now,
            }
        },
    )


def reset_job_for_retry(job_id: str) -> JobRecord | None:
    now = _utc_now()
    get_jobs_collection().update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": JobStatus.queued.value,
                "processed_pages": 0,
                "ocr_markdown": None,
                "structured_output": None,
                "error_message": None,
                "updated_at": now,
                "completed_at": None,
                "pages": [],
            }
        },
    )
    return get_job(job_id)
