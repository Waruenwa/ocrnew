from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.database import get_connection
from app.schemas import JobRecord, JobStatus, PageResult, TextSegment


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_job_record(row, page_rows) -> JobRecord:
    return JobRecord(
        id=row["id"],
        filename=row["filename"],
        mime_type=row["mime_type"],
        status=JobStatus(row["status"]),
        total_pages=row["total_pages"],
        processed_pages=row["processed_pages"],
        extraction_prompt=row["extraction_prompt"],
        ocr_markdown=row["ocr_markdown"],
        structured_output=row["structured_output"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        pages=[
            PageResult(
                page_number=page_row["page_number"],
                markdown=page_row["markdown"],
                segments=[
                    TextSegment(**segment)
                    for segment in json.loads(page_row["segments_json"] or "[]")
                ],
            )
            for page_row in page_rows
        ],
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
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO jobs (
                id,
                filename,
                stored_path,
                mime_type,
                status,
                total_pages,
                processed_pages,
                extraction_prompt,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                filename,
                str(stored_path),
                mime_type,
                JobStatus.queued.value,
                total_pages,
                0,
                extraction_prompt,
                now,
                now,
            ),
        )
        connection.commit()
    job = get_job(job_id)
    if job is None:
        raise RuntimeError("Failed to reload job after creation.")
    return job


def get_job(job_id: str) -> JobRecord | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        page_rows = connection.execute(
            "SELECT page_number, markdown, segments_json FROM job_pages WHERE job_id = ? ORDER BY page_number ASC",
            (job_id,),
        ).fetchall()
    return _build_job_record(row, page_rows)


def list_jobs(limit: int = 12) -> list[JobRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        jobs: list[JobRecord] = []
        for row in rows:
            page_rows = connection.execute(
                "SELECT page_number, markdown, segments_json FROM job_pages WHERE job_id = ? ORDER BY page_number ASC",
                (row["id"],),
            ).fetchall()
            jobs.append(_build_job_record(row, page_rows))
    return jobs


def get_job_file_path(job_id: str) -> Path | None:
    with get_connection() as connection:
        row = connection.execute("SELECT stored_path FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return Path(row["stored_path"])


def mark_processing(job_id: str) -> None:
    now = _utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = ?, updated_at = ?, error_message = NULL
            WHERE id = ?
            """,
            (JobStatus.processing.value, now, job_id),
        )
        connection.commit()


def save_page_result(
    job_id: str,
    page_number: int,
    markdown: str,
    segments: list[dict[str, object]],
) -> None:
    now = _utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO job_pages (job_id, page_number, markdown, segments_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id, page_number)
            DO UPDATE SET
                markdown = excluded.markdown,
                segments_json = excluded.segments_json
            """,
            (job_id, page_number, markdown, json.dumps(segments, ensure_ascii=False)),
        )
        connection.execute(
            """
            UPDATE jobs
            SET processed_pages = (
                SELECT COUNT(*) FROM job_pages WHERE job_id = ?
            ),
            updated_at = ?
            WHERE id = ?
            """,
            (job_id, now, job_id),
        )
        connection.commit()


def complete_job(job_id: str, markdown: str, structured_output: str | None) -> None:
    now = _utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = ?,
                processed_pages = total_pages,
                ocr_markdown = ?,
                structured_output = ?,
                updated_at = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                JobStatus.completed.value,
                markdown,
                structured_output,
                now,
                now,
                job_id,
            ),
        )
        connection.commit()


def fail_job(job_id: str, error_message: str) -> None:
    now = _utc_now()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET status = ?,
                error_message = ?,
                updated_at = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (JobStatus.failed.value, error_message, now, now, job_id),
        )
        connection.commit()


def reset_job_for_retry(job_id: str) -> JobRecord | None:
    now = _utc_now()
    with get_connection() as connection:
        connection.execute("DELETE FROM job_pages WHERE job_id = ?", (job_id,))
        connection.execute(
            """
            UPDATE jobs
            SET status = ?,
                processed_pages = 0,
                ocr_markdown = NULL,
                structured_output = NULL,
                error_message = NULL,
                updated_at = ?,
                completed_at = NULL
            WHERE id = ?
            """,
            (JobStatus.queued.value, now, job_id),
        )
        connection.commit()
    return get_job(job_id)
