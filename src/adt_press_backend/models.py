from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobEvent(BaseModel):
    timestamp: datetime
    message: str


class JobSummary(BaseModel):
    id: str
    label: str
    display_label: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    pdf_filename: str
    output_dir: Optional[str] = None
    plate_available: bool = False


class JobDetail(JobSummary):
    submitted_overrides: Dict[str, Any]
    effective_overrides: Dict[str, Any]
    resolved_config: Dict[str, Any]
    events: List[JobEvent]
    error: Optional[str] = None


class ConfigMetadata(BaseModel):
    defaults: Dict[str, Any]
    strategies: Dict[str, List[str]]
    render_strategies: List[str]
    layout_types: Dict[str, Any]
    boolean_flags: List[str]
    notes: Dict[str, str]
