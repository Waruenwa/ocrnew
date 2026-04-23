# Forced reload v2
from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import BASE_DIR, ensure_data_dirs, load_settings
from app.database import init_db
from app.import_pipeline import (
    create_import_from_uploaded_file,
    get_import,
    get_import_preview_path,
    import_record_needs_ocr,
    list_pending_ocr_imports,
    list_imports,
    mark_import_checked,
    process_import_ocr,
    save_import_page_markdown,
    scan_source_folder,
)
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
from app.schemas import (
    AppConfigResponse,
    HealthResponse,
    ImportCheckPayload,
    ImportPageSavePayload,
    ImportRecord,
    JobRecord,
)
from app.typhoon import (
    build_page_segments,
    count_pages,
    join_markdown_pages,
    render_page_preview,
    run_ocr_page,
    run_structured_extraction,
    save_page_preview,
)


load_dotenv(BASE_DIR / ".env", override=True)
settings = load_settings()

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def run_startup_import_scan() -> None:
    try:
        print(f"[imports] automatic startup scan from {settings.imports_source_dir}", flush=True)
        pending_records = await asyncio.to_thread(list_pending_ocr_imports)
        for record in pending_records:
            if import_record_needs_ocr(record):
                await app.state.import_queue.put(record.id)
        if pending_records:
            print(
                f"[imports] re-queued {len(pending_records)} pending OCR record(s)",
                flush=True,
            )

        records = await asyncio.to_thread(scan_source_folder, settings)
        for record in records:
            if import_record_needs_ocr(record):
                await app.state.import_queue.put(record.id)
        print(f"[imports] automatic startup scan completed: {len(records)} record(s)", flush=True)
    except Exception as exc:
        print(f"[imports] automatic startup scan failed: {exc}", flush=True)


async def job_worker(queue: asyncio.Queue[str]) -> None:
    while True:
        job_id = await queue.get()
        try:
            await asyncio.to_thread(process_job_sync, job_id)
        finally:
            queue.task_done()


async def import_worker(queue: asyncio.Queue[str]) -> None:
    while True:
        import_id = await queue.get()
        try:
            await asyncio.to_thread(process_import_ocr, import_id, settings)
        finally:
            queue.task_done()


def _get_job_original_path(job_id: str, filename: str) -> Path:
    extension = Path(filename).suffix.lower()
    return (settings.jobs_original_dir / job_id / f"source{extension}").resolve()


def _get_job_preview_path(job_id: str, page_number: int) -> Path:
    return (settings.jobs_derived_dir / job_id / f"page-{page_number:03d}-preview.png").resolve()


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
            preview_path = _get_job_preview_path(job_id, page_number)
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            page_preview_image.save(preview_path, format="PNG")
            save_page_result(job_id, page_number, markdown, segments)

        full_markdown = join_markdown_pages(page_markdowns)

        structured_output = None
        if job.extraction_prompt and settings.extraction_ready:
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
        preview_path = _get_job_preview_path(job_id, page.page_number)
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        page_preview_image.save(preview_path, format="PNG")
        segments = build_page_segments(page_preview_image, page.markdown, page.page_number)
        save_page_result(job_id, page.page_number, page.markdown, segments)


@app.on_event("startup")
async def on_startup() -> None:
    ensure_data_dirs()
    init_db()
    app.state.queue = asyncio.Queue()
    app.state.worker = asyncio.create_task(job_worker(app.state.queue))
    app.state.import_queue = asyncio.Queue()
    app.state.import_worker = asyncio.create_task(import_worker(app.state.import_queue))
    app.state.import_scan_task = asyncio.create_task(run_startup_import_scan())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    worker = getattr(app.state, "worker", None)
    if worker is not None:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    import_scan_task = getattr(app.state, "import_scan_task", None)
    if import_scan_task is not None:
        import_scan_task.cancel()
        with suppress(asyncio.CancelledError):
            await import_scan_task

    import_worker_task = getattr(app.state, "import_worker", None)
    if import_worker_task is not None:
        import_worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await import_worker_task


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/api/config", response_model=AppConfigResponse)
async def config() -> AppConfigResponse:
    return AppConfigResponse(
        imports_source_dir=str(settings.imports_source_dir),
        ocr_ready=settings.ocr_ready,
        extraction_ready=settings.extraction_ready,
        ocr_model=settings.ocr_model,
        max_upload_mb=settings.max_upload_mb,
        text_model=settings.text_model,
    )


@app.get("/api/jobs", response_model=list[JobRecord])
async def jobs() -> list[JobRecord]:
    return list_jobs()


@app.get("/api/imports", response_model=list[ImportRecord])
async def imports(
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[ImportRecord]:
    return list_imports(limit=limit, category=category)


@app.post("/api/imports/scan-folder", response_model=list[ImportRecord])
async def scan_import_folder() -> list[ImportRecord]:
    records = await asyncio.to_thread(scan_source_folder, settings)
    for record in records:
        if import_record_needs_ocr(record):
            await app.state.import_queue.put(record.id)
    return records


@app.post("/api/imports/upload", response_model=ImportRecord, status_code=201)
async def upload_import_file(
    file: UploadFile = File(...),
    document_category: str = Form(default="uncategorized"),
) -> ImportRecord:
    if not settings.ocr_ready:
        raise HTTPException(
            status_code=503,
            detail=(
                "Configure OCR_BASE_URL and OCR_MODEL before uploading files. "
                "Add OCR_API_KEY as well when the OCR endpoint is not localhost/private."
            ),
        )

    filename = _safe_filename(file.filename or "upload")
    extension = Path(filename).suffix.lower()
    if extension not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(status_code=400, detail="รองรับเฉพาะ PDF, PNG, JPG และ JPEG")

    staged_path = settings.imports_source_dir / f"{uuid4().hex}{extension}"
    with staged_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_size = staged_path.stat().st_size
    if file_size > settings.max_upload_bytes:
        staged_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"ไฟล์ใหญ่เกินกำหนด ({settings.max_upload_mb} MB)",
        )

    try:
        record = await asyncio.to_thread(
            create_import_from_uploaded_file,
            staged_path,
            source_filename=filename,
            document_category=document_category,
            settings=settings,
        )
        if import_record_needs_ocr(record):
            await app.state.import_queue.put(record.id)
        return record
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise


@app.get("/api/imports/{import_id}")
async def import_detail(import_id: str) -> ImportRecord:
    record = get_import(import_id, settings, refresh_ocr=False)
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return record


@app.post("/api/imports/{import_id}/retry-ocr", response_model=ImportRecord)
async def retry_import_ocr(import_id: str) -> ImportRecord:
    record = get_import(import_id, settings, refresh_ocr=False)
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    await app.state.import_queue.put(import_id)
    return record


def _build_import_preview_response(import_id: str, page_number: int, *, cleaned: bool) -> FileResponse:
    preview_path = get_import_preview_path(import_id, page_number, cleaned=cleaned)
    if preview_path is None:
        raise HTTPException(status_code=404, detail="Import preview not found")
    return FileResponse(path=preview_path, media_type="image/png", headers={"Content-Disposition": "inline"})


@app.get("/api/imports/{import_id}/pages/{page_number}/original")
async def import_original_preview(import_id: str, page_number: int) -> FileResponse:
    return _build_import_preview_response(import_id, page_number, cleaned=False)


@app.get("/api/imports/{import_id}/pages/{page_number}/cleaned")
async def import_cleaned_preview(import_id: str, page_number: int) -> FileResponse:
    return _build_import_preview_response(import_id, page_number, cleaned=True)


@app.post("/api/imports/{import_id}/check", response_model=ImportRecord)
async def check_import(import_id: str, payload: ImportCheckPayload) -> ImportRecord:
    record = await asyncio.to_thread(
        mark_import_checked,
        import_id,
        checked_by=payload.checked_by,
        note=payload.note,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return record


@app.post("/api/imports/{import_id}/pages/{page_number}/save", response_model=ImportRecord)
async def save_import_page(
    import_id: str,
    page_number: int,
    payload: ImportPageSavePayload,
) -> ImportRecord:
    record = await asyncio.to_thread(
        save_import_page_markdown,
        import_id,
        page_number=page_number,
        markdown=payload.markdown,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Import page not found")
    return record


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

    preview_path = _get_job_preview_path(job_id, page_number)
    if not preview_path.exists():
        preview_path.parent.mkdir(parents=True, exist_ok=True)
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
    if not settings.ocr_ready:
        raise HTTPException(
            status_code=503,
            detail=(
                "Configure OCR_BASE_URL and OCR_MODEL before uploading files. "
                "Add OCR_API_KEY as well when the OCR endpoint is not localhost/private."
            ),
        )

    filename = _safe_filename(file.filename or "upload")
    extension = Path(filename).suffix.lower()
    if extension not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(status_code=400, detail="รองรับเฉพาะ PDF, PNG, JPG และ JPEG")

    job_id = uuid4().hex
    stored_path = _get_job_original_path(job_id, filename)
    stored_path.parent.mkdir(parents=True, exist_ok=True)

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
