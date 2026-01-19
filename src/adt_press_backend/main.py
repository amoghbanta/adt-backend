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

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .job_manager import JobManager
from .models import ConfigMetadata, JobDetail, JobSummary, RegenerateRequest, SectionEditRequest, SectionEditResponse
from .s3_service import generate_presigned_url
from .utils import ensure_directory
from .key_manager import KeyManager, APIKeyRecord
from .middleware import RateLimiter
from .configuration import CONFIG_PATH, get_default_config_container

import instructor
from banks import Prompt
from litellm import acompletion
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import os
import secrets
import shutil
import uuid

# Initialize FastAPI application with metadata
app = FastAPI(title="ADT Press API", version="0.1.0")

# Configure CORS to allow requests from any origin
allowed_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
job_manager = JobManager(max_workers=2)
key_manager = KeyManager()
rate_limiter = RateLimiter(requests_per_minute=60)

ensure_directory(job_manager.output_root)
ensure_directory(job_manager.upload_root)

# Mount static file serving for job outputs
app.mount("/outputs", StaticFiles(directory=job_manager.output_root), name="outputs")


# Dependencies
def get_job_manager() -> JobManager:
    return job_manager

def get_key_manager() -> KeyManager:
    return key_manager

async def check_rate_limit(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """
    Enforce rate limits based on API Key (if present) or Client IP.
    """
    identifier = x_api_key if x_api_key else (request.client.host if request.client else "unknown")
    
    if not rate_limiter.is_allowed(identifier):
        raise HTTPException(
            status_code=429, 
            detail="Rate limit exceeded. Please slow down."
        )
    return x_api_key

async def require_api_key(
    key: str | None = Depends(check_rate_limit),
    manager: KeyManager = Depends(get_key_manager)
) -> APIKeyRecord:
    """
    Strictly require a valid API Key.
    """
    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key required for this endpoint (Header: X-API-Key)",
        )
    
    record = manager.validate_key(key)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API Key",
        )
    return record

async def verify_quota(
    record: APIKeyRecord = Depends(require_api_key),
    manager: KeyManager = Depends(get_key_manager)
) -> APIKeyRecord:
    """
    Ensure the key has remaining generations.
    """
    if not manager.check_quota(record.id):
        raise HTTPException(
            status_code=429, 
            detail="Usage quota exceeded for this API Key. Please contact support."
        )
    return record


@app.get("/healthz")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


# --- Admin Endpoints ---

async def verify_master_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Verify the Master API Key for admin actions.
    """
    master_key = os.getenv("ADT_API_KEY")
    if not master_key:
        # If no master key configured, deny all admin access for safety
        raise HTTPException(status_code=500, detail="Server misconfiguration: ADT_API_KEY not set")
    
    # constant time comparison to prevent timing attacks
    if not secrets.compare_digest(x_api_key, master_key):
        raise HTTPException(status_code=401, detail="Invalid Master API Key")
    return x_api_key

class CreateKeyRequest(BaseModel):
    owner: str
    max_generations: int = 100

@app.post("/admin/keys", status_code=201)
def create_api_key(
    request: CreateKeyRequest,
    manager: KeyManager = Depends(get_key_manager),
    _: str = Depends(verify_master_key),
    __: str | None = Depends(check_rate_limit)
):
    """
    Create a new API Key with specified quota.
    Requires Master Key.
    """
    raw_key, record = manager.create_key(request.owner, request.max_generations)
    return {"api_key": raw_key, "record": record}

@app.get("/admin/keys")
def list_api_keys(
    manager: KeyManager = Depends(get_key_manager),
    _: str = Depends(verify_master_key),
    __: str | None = Depends(check_rate_limit)
):
    """List all API keys (Requires Master Key)."""
    return manager.list_keys()

@app.delete("/admin/keys/{key_id}")
def revoke_api_key(
    key_id: str, 
    manager: KeyManager = Depends(get_key_manager),
    _: str = Depends(verify_master_key),
    __: str | None = Depends(check_rate_limit)
):
    """Revoke an API key (Requires Master Key)."""
    if manager.revoke_key(key_id):
        return {"status": "revoked"}
    raise HTTPException(status_code=404, detail="Key not found")


# --- Public Endpoints (Rate Limited) ---

@app.get("/config/defaults", response_model=ConfigMetadata)
def get_config_defaults(
    manager: JobManager = Depends(get_job_manager),
    _: str | None = Depends(check_rate_limit)
) -> ConfigMetadata:
    return manager.get_config_metadata()


@app.get("/jobs", response_model=list[JobSummary])
def list_jobs(
    manager: JobManager = Depends(get_job_manager),
    _: str | None = Depends(check_rate_limit)
) -> list[JobSummary]:
    return manager.list_jobs()


@app.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(
    job_id: str, 
    manager: JobManager = Depends(get_job_manager),
    _: str | None = Depends(check_rate_limit)
) -> JobDetail:
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job



@app.post("/jobs", response_model=JobSummary)
async def create_job(
    pdf: UploadFile = File(...),
    label: str = Form(""),
    config: str = Form("{}"),
    manager: JobManager = Depends(get_job_manager),
    key_mgr: KeyManager = Depends(get_key_manager),
    key_record: APIKeyRecord = Depends(verify_quota)  # Enforce Quota
) -> JobSummary:
    # ... (Validation Logic) ...
    if not pdf.filename:
        raise HTTPException(status_code=400, detail="PDF file must have a filename")

    if not pdf.filename.lower().endswith(".pdf") and (pdf.content_type or "") != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")

    try:
        parsed_config: Dict[str, Any] = json.loads(config) if config else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config JSON: {exc}") from exc

    for key in list(parsed_config.keys()):
        if parsed_config[key] is None:
            parsed_config.pop(key)

    # Increment Usage (Atomically)
    # We do this BEFORE starting the job. If job fails immediately, we might want to refund?
    # For now, simplistic approach: "Attempting a generation costs 1 credit".
    if not key_mgr.increment_usage(key_record.id):
        # Should be caught by verify_quota, but double check race condition
        raise HTTPException(status_code=429, detail="Quota exceeded")

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
def get_plate(
    job_id: str, 
    manager: JobManager = Depends(get_job_manager),
    _: str | None = Depends(check_rate_limit)
) -> JSONResponse:
    try:
        data = manager.load_plate(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(content=data)


@app.put("/jobs/{job_id}/plate")
async def update_plate(
    job_id: str, 
    request: Request, 
    manager: JobManager = Depends(get_job_manager),
    _: str | None = Depends(check_rate_limit)
) -> Dict[str, str]:
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


@app.post("/jobs/{job_id}/regenerate", response_model=JobSummary)
async def regenerate_job(
    job_id: str,
    request: RegenerateRequest,
    manager: JobManager = Depends(get_job_manager),
    key_mgr: KeyManager = Depends(get_key_manager),
    key_record: APIKeyRecord = Depends(verify_quota),
) -> JobSummary:
    """
    Regenerate or edit specific sections of a completed job.

    This endpoint creates a new job that reuses the source job's PDF and
    configuration, but regenerates or edits only the specified sections.

    Args:
        job_id: The ID of the completed job to regenerate from
        request: RegenerateRequest containing sections to regenerate/edit

    Returns:
        JobSummary of the newly created regeneration job

    Raises:
        400: If neither regenerate_sections nor edit_sections provided
        404: If source job not found
        409: If source job is not in COMPLETED status
    """
    # Validate that at least one operation is specified
    if not request.regenerate_sections and not request.edit_sections:
        raise HTTPException(
            status_code=400,
            detail="At least one of regenerate_sections or edit_sections must be provided",
        )

    # Check for overlapping section IDs
    if request.regenerate_sections and request.edit_sections:
        overlap = set(request.regenerate_sections) & set(request.edit_sections.keys())
        if overlap:
            raise HTTPException(
                status_code=400,
                detail=f"Sections cannot be in both regenerate and edit lists: {overlap}",
            )

    # Increment usage quota
    if not key_mgr.increment_usage(key_record.id):
        raise HTTPException(status_code=429, detail="Quota exceeded")

    try:
        summary = manager.regenerate_job(
            source_job_id=job_id,
            regenerate_sections=request.regenerate_sections,
            edit_sections=request.edit_sections,
        )
        return summary
    except ValueError as exc:
        error_msg = str(exc)
        if "not found" in error_msg:
            raise HTTPException(status_code=404, detail=error_msg) from exc
        if "must be completed" in error_msg:
            raise HTTPException(status_code=409, detail=error_msg) from exc
        raise HTTPException(status_code=400, detail=error_msg) from exc


@app.get("/jobs/{job_id}/status")
def job_status(
    job_id: str, 
    manager: JobManager = Depends(get_job_manager),
    _: str | None = Depends(check_rate_limit)
) -> Dict[str, Any]:
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
def job_output(
    job_id: str, 
    path: str, 
    manager: JobManager = Depends(get_job_manager),
    # Note: Static assets might need to be public if loaded by browser directly without headers?
    # Usually "outputs" are protected. If frontend uses img tags, it needs to proxy or allow via token in URL.
    # For now, let's PROTECT it. Frontend needs to send header.
    # If the frontend uses standard <img src="..."> tags, those WON'T send custom headers.
    # This is a common issue.
    # User said: "In the backend can we create one more API for managing API keys and usage?"
    # Did not explicitly say "protect existing outputs".
    # BUT "Other APIs should be allowed to any use but rate limited."
    # If I protect this, the editor might break if it tries to load images directly.
    # Let's verify_api_key here too. If editor breaks, we might need a query param token or cookie.
    _: str | None = Depends(check_rate_limit)
):
    job = manager.get_job(job_id)
    # ... (rest of implementation)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    base_path = Path(job.output_dir).resolve()
    file_path = (base_path / path).resolve()

    if not str(file_path).startswith(str(base_path)):
        raise HTTPException(status_code=400, detail="Invalid path request")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(file_path)

@app.get("/jobs/{job_id}/download")
def get_download_url(
    job_id: str, 
    manager: JobManager = Depends(get_job_manager),
    _: str | None = Depends(check_rate_limit)
) -> Dict[str, Any]:
    # ...
    job = manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.s3_key:
        raise HTTPException(status_code=404, detail="Zip not available for this job")
    
    url = generate_presigned_url(job.s3_key, expiration=3600)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to generate download URL")
    
    return {"download_url": url, "expires_in_seconds": 3600}

# --- Section Edit Endpoint ---

def _get_instructor_client():
    """
    Return an Instructor-wrapped LiteLLM client that prefers JSON-schema modes.
    """
    mode_candidates = [
        "JSON_SCHEMA",
        "JSON",
        "OPENAI_RESPONSE_FORMAT",
    ]

    for attr in mode_candidates:
        mode = getattr(instructor.Mode, attr, None)
        if mode is not None:
            return instructor.from_litellm(acompletion, mode=mode)

    return instructor.from_litellm(acompletion)


def _load_web_edit_config() -> dict:
    """
    Load the web_edit configuration including model and template.
    Returns dict with 'model', 'template', 'max_retries', 'timeout'.
    """
    config = get_default_config_container(resolve=True)
    web_edit_config = config.get("prompts", {}).get("web_edit", {})
    default_model = config.get("default_model", "gpt-4o")

    # Resolve model - if "default", use the default_model setting
    model = web_edit_config.get("model", "default")
    if model == "default":
        model = default_model

    # Load template
    template_path_str = web_edit_config.get("template_path", "prompts/web_edit.jinja2")
    # CONFIG_PATH points to config/config.yaml, so parent.parent is the adt-press root
    adt_press_root = CONFIG_PATH.parent.parent
    template_path = adt_press_root / template_path_str

    if not template_path.exists():
        raise FileNotFoundError(f"Web edit prompt template not found at {template_path}")

    template_content = template_path.read_text(encoding="utf-8")

    return {
        "model": model,
        "template": template_content,
        "max_retries": web_edit_config.get("max_retries", 3),
        "timeout": web_edit_config.get("timeout", 120),
    }


class _WebEditLLMResponse(BaseModel):
    """Internal response model for LLM output with validation."""
    html: str
    reasoning: str


@app.post("/sections/edit", response_model=SectionEditResponse)
async def edit_section(
    request: SectionEditRequest,
    _: APIKeyRecord = Depends(require_api_key)
) -> SectionEditResponse:
    """
    Stateless section editing endpoint.

    Takes HTML content and an edit instruction, returns updated HTML.
    No job context or server-side state required.

    Args:
        request: SectionEditRequest with html, edit_instruction, and optional context

    Returns:
        SectionEditResponse with updated html and reasoning

    Raises:
        400: If the request is invalid
        500: If the LLM call fails
    """
    try:
        # Load config and prompt template
        web_edit_config = _load_web_edit_config()
        prompt = Prompt(web_edit_config["template"])

        # Build context for the prompt
        context = {
            "section_id": request.section_id,
            "existing_html": request.html,
            "edit_instruction": request.edit_instruction,
            "section_type": request.section_type,
            "page_number": request.page_number,
            "language": request.language,
        }

        # Get instructor client
        client = _get_instructor_client()

        # Call LLM
        response: _WebEditLLMResponse = await client.chat.completions.create(
            model=web_edit_config["model"],
            response_model=_WebEditLLMResponse,
            messages=[m.model_dump(exclude_none=True) for m in prompt.chat_messages(context)],
            max_retries=web_edit_config["max_retries"],
            timeout=web_edit_config["timeout"],
        )

        return SectionEditResponse(
            html=response.html,
            reasoning=response.reasoning,
        )

    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logging.exception("Failed to edit section")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to edit section: {str(exc)}"
        ) from exc


async def _store_upload(upload_file: UploadFile) -> Path:
    """
    Store uploaded file to a temporary location and return the path.
    """
    upload_dir = Path("data/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Use UUID to prevent filename collisions
    filename = f"{uuid.uuid4()}_{upload_file.filename}"
    file_path = upload_dir / filename
    
    try:
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer)
    finally:
        await upload_file.close()
        
    return file_path
