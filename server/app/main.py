from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import BASE_DIR, PREVIEWS_DIR, UPLOADS_DIR, ensure_data_dirs, load_settings
from app.database import init_db
from app.repository import (
    complete_job,
    create_job,
    fail_job,
    get_job,
    get_job_file_path,
    list_jobs,
    mark_processing,
    reset_job_for_retry,
    save_page_result,
)
from app.schemas import AppConfigResponse, HealthResponse, JobRecord
from app.typhoon import (
    build_page_segments,
    count_pages,
    join_markdown_pages,
    render_page_preview,
    run_ocr_page,
    run_structured_extraction,
    save_page_preview,
)


load_dotenv(BASE_DIR / ".env")
settings = load_settings()

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def job_worker(queue: asyncio.Queue[str]) -> None:
    while True:
        job_id = await queue.get()
        try:
            await asyncio.to_thread(process_job_sync, job_id)
        finally:
            queue.task_done()


def process_job_sync(job_id: str) -> None:
    job = get_job(job_id)
    file_path = get_job_file_path(job_id)
    if job is None or file_path is None:
        return

    try:
        mark_processing(job_id)
        page_markdowns: list[str] = []
        for page_number in range(1, job.total_pages + 1):
            page_preview_image = render_page_preview(file_path, page_number)
            markdown = run_ocr_page(file_path, page_number, settings)
            segments = build_page_segments(page_preview_image, markdown, page_number)
            page_markdowns.append(markdown)
            preview_path = PREVIEWS_DIR / f"{job_id}-page-{page_number}.png"
            page_preview_image.save(preview_path, format="PNG")
            save_page_result(job_id, page_number, markdown, segments)

        full_markdown = join_markdown_pages(page_markdowns)

        structured_output = None
        if job.extraction_prompt and settings.text_api_key:
            structured_output = run_structured_extraction(
                markdown=full_markdown,
                extraction_prompt=job.extraction_prompt,
                settings=settings,
            )

        complete_job(job_id, full_markdown, structured_output)
    except Exception as exc:
        fail_job(job_id, str(exc))


def rebuild_segments_sync(job_id: str) -> None:
    job = get_job(job_id)
    file_path = get_job_file_path(job_id)
    if job is None or file_path is None:
        return

    for page in job.pages:
        page_preview_image = render_page_preview(file_path, page.page_number)
        preview_path = PREVIEWS_DIR / f"{job_id}-page-{page.page_number}.png"
        page_preview_image.save(preview_path, format="PNG")
        segments = build_page_segments(page_preview_image, page.markdown, page.page_number)
        save_page_result(job_id, page.page_number, page.markdown, segments)


@app.on_event("startup")
async def on_startup() -> None:
    ensure_data_dirs()
    init_db()
    app.state.queue = asyncio.Queue()
    app.state.worker = asyncio.create_task(job_worker(app.state.queue))


@app.on_event("shutdown")
async def on_shutdown() -> None:
    worker = getattr(app.state, "worker", None)
    if worker is not None:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/api/config", response_model=AppConfigResponse)
async def config() -> AppConfigResponse:
    return AppConfigResponse(
        ocr_ready=bool(settings.ocr_api_key),
        extraction_ready=bool(settings.text_api_key),
        max_upload_mb=settings.max_upload_mb,
        text_model=settings.typhoon_text_model,
    )


@app.get("/api/jobs", response_model=list[JobRecord])
async def jobs() -> list[JobRecord]:
    return list_jobs()


@app.get("/api/jobs/{job_id}", response_model=JobRecord)
async def job_detail(job_id: str) -> JobRecord:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/file")
async def job_file(job_id: str) -> FileResponse:
    job = get_job(job_id)
    file_path = get_job_file_path(job_id)
    if job is None or file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail="Job file not found")

    return FileResponse(
        path=file_path,
        media_type=job.mime_type or None,
        headers={"Content-Disposition": "inline"},
    )


@app.get("/api/jobs/{job_id}/pages/{page_number}/preview")
async def job_page_preview(job_id: str, page_number: int) -> FileResponse:
    job = get_job(job_id)
    file_path = get_job_file_path(job_id)
    if job is None or file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail="Job file not found")
    if page_number < 1 or page_number > job.total_pages:
        raise HTTPException(status_code=404, detail="Page not found")

    preview_path = PREVIEWS_DIR / f"{job_id}-page-{page_number}.png"
    if not preview_path.exists():
        save_page_preview(file_path, page_number, preview_path)

    return FileResponse(
        path=preview_path,
        media_type="image/png",
        headers={"Content-Disposition": "inline"},
    )


@app.post("/api/jobs/{job_id}/retry", response_model=JobRecord)
async def retry_job(job_id: str) -> JobRecord:
    job = get_job(job_id)
    file_path = get_job_file_path(job_id)
    if job is None or file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    reset_job = reset_job_for_retry(job_id)
    if reset_job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    await app.state.queue.put(job_id)
    return reset_job


@app.post("/api/jobs/{job_id}/rebuild-segments", response_model=JobRecord)
async def rebuild_segments(job_id: str) -> JobRecord:
    job = get_job(job_id)
    file_path = get_job_file_path(job_id)
    if job is None or file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.pages:
        raise HTTPException(status_code=400, detail="Job has no OCR pages to rebuild")

    await asyncio.to_thread(rebuild_segments_sync, job_id)
    rebuilt_job = get_job(job_id)
    if rebuilt_job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return rebuilt_job


def _safe_filename(filename: str) -> str:
    return Path(filename).name or "upload"


@app.post("/api/jobs", response_model=JobRecord, status_code=201)
async def create_ocr_job(
    file: UploadFile = File(...),
    extraction_prompt: str = Form(default=""),
) -> JobRecord:
    if not settings.ocr_api_key:
        raise HTTPException(
            status_code=503,
            detail="Set TYPHOON_OCR_API_KEY or TYPHOON_API_KEY before uploading files.",
        )

    filename = _safe_filename(file.filename or "upload")
    extension = Path(filename).suffix.lower()
    if extension not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(status_code=400, detail="รองรับเฉพาะ PDF, PNG, JPG และ JPEG")

    job_id = uuid4().hex
    stored_path = UPLOADS_DIR / f"{job_id}{extension}"

    with stored_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_size = stored_path.stat().st_size
    if file_size > settings.max_upload_bytes:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"ไฟล์ใหญ่เกินกำหนด ({settings.max_upload_mb} MB)",
        )

    try:
        total_pages = count_pages(stored_path)
    except Exception as exc:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = create_job(
        job_id=job_id,
        filename=filename,
        stored_path=stored_path,
        mime_type=file.content_type,
        total_pages=total_pages,
        extraction_prompt=extraction_prompt.strip() or None,
    )
    await app.state.queue.put(job_id)
    return job
