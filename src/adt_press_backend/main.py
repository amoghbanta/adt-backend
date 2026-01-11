"""
FastAPI application for ADT Press Backend.

This module defines the HTTP API endpoints for:
- Job creation and management
- Configuration discovery
- File uploads and output retrieval
- Plate (intermediate result) editing

The API follows RESTful conventions and provides async endpoints for
efficient handling of concurrent requests.

Architecture:
    - FastAPI handles HTTP routing and request validation
    - JobManager coordinates business logic and pipeline execution
    - CORS middleware enables cross-origin requests from frontend
    - Static file serving provides direct access to generated outputs
"""

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
from .s3_service import generate_presigned_url
from .utils import ensure_directory

# Initialize FastAPI application with metadata
app = FastAPI(title="ADT Press API", version="0.1.0")

# Configure CORS to allow requests from any origin
# Note: In production, restrict allowed_origins to specific domains for security
allowed_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize job manager with 2 concurrent workers
# This allows processing 2 documents simultaneously
job_manager = JobManager(max_workers=2)
ensure_directory(job_manager.output_root)
ensure_directory(job_manager.upload_root)

# Mount static file serving for job outputs
# Outputs are served at /outputs/{job-label}/... for direct file access
app.mount("/outputs", StaticFiles(directory=job_manager.output_root), name="outputs")


def get_job_manager() -> JobManager:
    """
    Dependency injection function for JobManager.

    Returns:
        The singleton JobManager instance

    Note:
        Using dependency injection enables easier testing by allowing
        the JobManager to be mocked or replaced in test scenarios.
    """
    return job_manager


@app.get("/healthz")
def healthcheck() -> Dict[str, str]:
    """
    Health check endpoint for monitoring and load balancers.

    Returns:
        Simple status object indicating service is running
    """
    return {"status": "ok"}


@app.get("/config/defaults", response_model=ConfigMetadata)
def get_config_defaults(manager: JobManager = Depends(get_job_manager)) -> ConfigMetadata:
    """
    Get default configuration metadata for job creation.

    This endpoint provides clients with:
    - Default configuration values
    - Available processing strategies
    - Valid options for each configuration key
    - Documentation for configuration parameters

    Returns:
        ConfigMetadata with complete configuration schema

    Example:
        GET /config/defaults
        Response: {
            "defaults": {"crop_strategy": "llm", ...},
            "strategies": {"crop_strategy": ["llm", "none"], ...},
            ...
        }
    """
    return manager.get_config_metadata()


@app.get("/jobs", response_model=list[JobSummary])
def list_jobs(manager: JobManager = Depends(get_job_manager)) -> list[JobSummary]:
    """
    List all jobs sorted by creation time (newest first).

    Returns:
        List of job summaries with essential information

    Example:
        GET /jobs
        Response: [
            {"id": "abc123...", "label": "doc-abc12345", ...},
            ...
        ]
    """
    return manager.list_jobs()


@app.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, manager: JobManager = Depends(get_job_manager)) -> JobDetail:
    """
    Get detailed information about a specific job.

    Args:
        job_id: The unique job identifier

    Returns:
        JobDetail with complete job information including configuration and events

    Raises:
        HTTPException: 404 if job not found

    Example:
        GET /jobs/abc123
        Response: {
            "id": "abc123",
            "status": "completed",
            "submitted_overrides": {...},
            "events": [...]
        }
    """
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _sanitize_filename(filename: str) -> str:
    """
    Sanitize an uploaded filename for safe filesystem storage.

    This function:
    - Removes potentially dangerous characters
    - Ensures a valid PDF extension
    - Provides a fallback for empty filenames

    Args:
        filename: Original uploaded filename

    Returns:
        Sanitized filename safe for filesystem storage

    Example:
        >>> _sanitize_filename("My Doc!.pdf")
        "My-Doc-.pdf"
        >>> _sanitize_filename("test.txt")
        "test.pdf"
    """
    stem = Path(filename).stem or "document"
    suffix = Path(filename).suffix.lower()

    # Replace non-alphanumeric characters (except - and _) with hyphens
    safe_stem = "".join(char if char.isalnum() or char in "-_" else "-" for char in stem)
    safe_stem = safe_stem.strip("-_") or "document"

    # Ensure PDF extension for security
    if suffix not in {".pdf"}:
        suffix = ".pdf"

    return f"{safe_stem}{suffix}"


async def _store_upload(file: UploadFile) -> Path:
    """
    Store an uploaded file in a unique directory.

    Each upload gets its own directory identified by a UUID to prevent
    filename conflicts and organize uploads by job.

    Args:
        file: The uploaded file from FastAPI

    Returns:
        Path to the stored file

    Note:
        Files are read in 8MB chunks to handle large PDFs efficiently
        without loading the entire file into memory.
    """
    upload_id = uuid4().hex
    upload_dir = ensure_directory(job_manager.upload_root / upload_id)
    filename = _sanitize_filename(file.filename or "document.pdf")
    destination = upload_dir / filename

    # Stream file in chunks to handle large uploads efficiently
    with destination.open("wb") as buffer:
        while chunk := await file.read(8 * 1024 * 1024):  # 8MB chunks
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
    """
    Create a new document processing job.

    This endpoint accepts a PDF file upload along with optional configuration
    and creates a background processing job.

    Args:
        pdf: The PDF file to process (multipart/form-data)
        label: Optional display label for the job (defaults to filename)
        config: JSON string of configuration overrides (defaults to {})
        manager: Injected JobManager dependency

    Returns:
        JobSummary of the created job

    Raises:
        HTTPException: 400 if validation fails (invalid PDF, malformed config)

    Example:
        POST /jobs
        Content-Type: multipart/form-data

        pdf: <file>
        label: "My Document"
        config: '{"crop_strategy": "llm", "page_range": [1, 5]}'

        Response: {"id": "abc123", "status": "pending", ...}

    Note:
        The job is created immediately and begins processing asynchronously.
        Clients should poll GET /jobs/{job_id}/status to track progress.
    """
    # Validate that a filename was provided
    if not pdf.filename:
        raise HTTPException(status_code=400, detail="PDF file must have a filename")

    # Validate file type (extension or content-type)
    if not pdf.filename.lower().endswith(".pdf") and (pdf.content_type or "") != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")

    # Parse and validate configuration JSON
    try:
        parsed_config: Dict[str, Any] = json.loads(config) if config else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config JSON: {exc}") from exc

    # Remove null values to use defaults instead
    for key in list(parsed_config.keys()):
        if parsed_config[key] is None:
            parsed_config.pop(key)

    # Store the uploaded file
    stored_pdf_path = await _store_upload(pdf)

    # Use provided label or derive from filename
    display_label = label or Path(pdf.filename).stem

    # Create and start the job
    summary = manager.create_job(
        display_label=display_label,
        pdf_filename=pdf.filename,
        pdf_path=stored_pdf_path,
        overrides=parsed_config,
    )
    return summary


@app.get("/jobs/{job_id}/plate")
def get_plate(job_id: str, manager: JobManager = Depends(get_job_manager)) -> JSONResponse:
    """
    Retrieve the plate.json file for a completed job.

    The plate file contains intermediate layout information that can be
    edited before regenerating final outputs.

    Args:
        job_id: The job whose plate to retrieve

    Returns:
        JSON response with plate data

    Raises:
        HTTPException: 404 if job or plate not found

    Example:
        GET /jobs/abc123/plate
        Response: {"pages": [...], "metadata": {...}}
    """
    try:
        data = manager.load_plate(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(content=data)


@app.put("/jobs/{job_id}/plate")
async def update_plate(job_id: str, request: Request, manager: JobManager = Depends(get_job_manager)) -> Dict[str, str]:
    """
    Update the plate.json file for a completed job.

    This allows users to edit intermediate layout information and
    regenerate outputs with modifications.

    Args:
        job_id: The job whose plate to update
        request: HTTP request containing JSON payload

    Returns:
        Status confirmation

    Raises:
        HTTPException:
            - 400 if payload is not valid JSON
            - 404 if job not found
            - 409 if job is not in COMPLETED status

    Example:
        PUT /jobs/abc123/plate
        Body: {"pages": [...], "metadata": {...}}
        Response: {"status": "saved"}

    Note:
        Plate updates are only allowed for completed jobs to prevent
        conflicts with ongoing pipeline execution.
    """
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    try:
        manager.save_plate(job_id, payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"status": "saved"}


@app.get("/jobs/{job_id}/status")
def job_status(job_id: str, manager: JobManager = Depends(get_job_manager)) -> Dict[str, Any]:
    """
    Get lightweight status information for a job.

    This endpoint provides a quick way to check job progress without
    fetching the full job details.

    Args:
        job_id: The job to check

    Returns:
        Object with status, error (if any), and plate_available flag

    Raises:
        HTTPException: 404 if job not found

    Example:
        GET /jobs/abc123/status
        Response: {
            "status": "running",
            "error": null,
            "plate_available": false
        }
    """
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "status": job.status,
        "error": job.error,
        "plate_available": job.plate_available,
        "zip_available": job.zip_available,
    }


@app.get("/jobs/{job_id}/outputs/{path:path}")
def job_output(job_id: str, path: str, manager: JobManager = Depends(get_job_manager)):
    """
    Retrieve a specific output file from a job.

    This endpoint serves individual output files (HTML, images, etc.)
    from the job's output directory.

    Args:
        job_id: The job whose output to retrieve
        path: Relative path to the file within the job's output directory

    Returns:
        FileResponse with the requested file

    Raises:
        HTTPException:
            - 400 if path attempts directory traversal
            - 404 if job or file not found

    Security:
        Path traversal protection ensures clients cannot access files
        outside the job's output directory.

    Example:
        GET /jobs/abc123/outputs/index.html
        Returns: HTML file content
    """
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Resolve paths and validate no directory traversal
    base_path = Path(job.output_dir).resolve()
    file_path = (base_path / path).resolve()

    # Security check: ensure requested path is within job output directory
    if not str(file_path).startswith(str(base_path)):
        raise HTTPException(status_code=400, detail="Invalid path request")

    # Verify file exists and is a regular file
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(file_path)


@app.get("/jobs/{job_id}/download")
def get_download_url(job_id: str, manager: JobManager = Depends(get_job_manager)) -> Dict[str, Any]:
    """
    Get a presigned S3 URL for downloading the job output zip.

    The URL is valid for 60 minutes (3600 seconds).

    Args:
        job_id: The job whose zip to download

    Returns:
        Object with download_url and expires_in_seconds

    Raises:
        HTTPException:
            - 404 if job not found or zip not available

    Example:
        GET /jobs/abc123/download
        Response: {
            "download_url": "https://s3.amazonaws.com/...",
            "expires_in_seconds": 3600
        }
    """
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.s3_key:
        raise HTTPException(status_code=404, detail="Zip not available for this job")
    
    url = generate_presigned_url(job.s3_key, expiration=3600)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to generate download URL")
    
    return {"download_url": url, "expires_in_seconds": 3600}
