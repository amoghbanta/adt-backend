from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .job_manager import JobManager
from .models import ConfigMetadata, JobDetail, JobSummary
from .utils import ensure_directory

app = FastAPI(title="ADT Press API", version="0.1.0")

allowed_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

job_manager = JobManager(max_workers=2)
ensure_directory(job_manager.output_root)
ensure_directory(job_manager.upload_root)

app.mount("/outputs", StaticFiles(directory=job_manager.output_root), name="outputs")


def get_job_manager() -> JobManager:
    return job_manager


@app.get("/healthz")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/config/defaults", response_model=ConfigMetadata)
def get_config_defaults(manager: JobManager = Depends(get_job_manager)) -> ConfigMetadata:
    return manager.get_config_metadata()


@app.get("/jobs", response_model=list[JobSummary])
def list_jobs(manager: JobManager = Depends(get_job_manager)) -> list[JobSummary]:
    return manager.list_jobs()


@app.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, manager: JobManager = Depends(get_job_manager)) -> JobDetail:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _sanitize_filename(filename: str) -> str:
    stem = Path(filename).stem or "document"
    suffix = Path(filename).suffix.lower()
    safe_stem = "".join(char if char.isalnum() or char in "-_" else "-" for char in stem)
    safe_stem = safe_stem.strip("-_") or "document"
    if suffix not in {".pdf"}:
        suffix = ".pdf"
    return f"{safe_stem}{suffix}"


async def _store_upload(file: UploadFile) -> Path:
    upload_id = uuid4().hex
    upload_dir = ensure_directory(job_manager.upload_root / upload_id)
    filename = _sanitize_filename(file.filename or "document.pdf")
    destination = upload_dir / filename

    with destination.open("wb") as buffer:
        while chunk := await file.read(8 * 1024 * 1024):
            buffer.write(chunk)
    await file.close()
    return destination


@app.post("/jobs", response_model=JobSummary)
async def create_job(
    pdf: UploadFile = File(...),
    label: str = Form(""),
    config: str = Form("{}"),
    manager: JobManager = Depends(get_job_manager),
) -> JobSummary:
    if not pdf.filename:
        raise HTTPException(status_code=400, detail="PDF file must have a filename")

    if not pdf.filename.lower().endswith(".pdf") and (pdf.content_type or "") != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")

    try:
        parsed_config: Dict[str, Any] = json.loads(config) if config else {}
    except json.JSONDecodeError as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid config JSON: {exc}") from exc

    for key in list(parsed_config.keys()):
        if parsed_config[key] is None:
            parsed_config.pop(key)

    stored_pdf_path = await _store_upload(pdf)
    display_label = label or Path(pdf.filename).stem

    summary = manager.create_job(
        display_label=display_label,
        pdf_filename=pdf.filename,
        pdf_path=stored_pdf_path,
        overrides=parsed_config,
    )
    return summary


@app.get("/jobs/{job_id}/plate")
def get_plate(job_id: str, manager: JobManager = Depends(get_job_manager)) -> JSONResponse:
    try:
        data = manager.load_plate(job_id)
    except FileNotFoundError as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(content=data)


@app.put("/jobs/{job_id}/plate")
async def update_plate(job_id: str, request: Request, manager: JobManager = Depends(get_job_manager)) -> Dict[str, str]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    try:
        manager.save_plate(job_id, payload)
    except RuntimeError as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"status": "saved"}


@app.get("/jobs/{job_id}/status")
def job_status(job_id: str, manager: JobManager = Depends(get_job_manager)) -> Dict[str, Any]:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "status": job.status,
        "error": job.error,
        "plate_available": job.plate_available,
    }


@app.get("/jobs/{job_id}/outputs/{path:path}")
def job_output(job_id: str, path: str, manager: JobManager = Depends(get_job_manager)):
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    base_path = Path(job.output_dir).resolve()
    file_path = (base_path / path).resolve()
    if not str(file_path).startswith(str(base_path)):
        raise HTTPException(status_code=400, detail="Invalid path request")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(file_path)
