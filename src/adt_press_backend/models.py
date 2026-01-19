"""
Data models for the ADT Press Backend API.

This module defines the Pydantic models used for request/response validation
and data serialization throughout the API. All models follow the JSON API
conventions for consistent client integration.

Models:
    - JobStatus: Enumeration of possible job states
    - JobEvent: Individual timestamped event in a job's lifecycle
    - JobSummary: Lightweight job representation for list views
    - JobDetail: Complete job information including configuration and events
    - ConfigMetadata: Configuration schema and available options
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class JobStatus(str, Enum):
    """
    Job execution status enumeration.

    States represent the lifecycle of a document processing job:
    - PENDING: Job created and queued for execution
    - RUNNING: Job currently being processed by the pipeline
    - COMPLETED: Job finished successfully
    - FAILED: Job encountered an error and could not complete
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobEvent(BaseModel):
    """
    A timestamped event in a job's execution history.

    Events provide an audit trail of job progression and help with
    debugging and monitoring.

    Attributes:
        timestamp: When this event occurred (UTC)
        message: Human-readable description of the event
    """

    timestamp: datetime
    message: str


class JobSummary(BaseModel):
    """
    Lightweight job representation for list endpoints.

    This model provides essential job information without the full
    configuration details, optimized for listing and filtering operations.

    Attributes:
        id: Unique job identifier (hex UUID)
        label: Filesystem-safe job label with unique suffix
        display_label: Original user-provided label
        status: Current job execution status
        created_at: When the job was created (UTC)
        updated_at: Last modification timestamp (UTC)
        pdf_filename: Original uploaded PDF filename
        output_dir: Path to job output directory (if processing started)
        plate_available: Whether a plate.json file exists for editing
    """

    id: str
    label: str
    display_label: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    pdf_filename: str
    output_dir: Optional[str] = None
    plate_available: bool = False
    zip_available: bool = False


class JobDetail(JobSummary):
    """
    Complete job information including configuration and event history.

    Extends JobSummary with full configuration details and execution events,
    used for single-job detail endpoints.

    Additional Attributes:
        submitted_overrides: Configuration values provided by the user
        effective_overrides: Actual overrides applied (includes auto-injected values)
        resolved_config: Final configuration after merging defaults and overrides
        events: Chronological list of job lifecycle events
        error: Error message if job failed (None otherwise)
    """

    submitted_overrides: Dict[str, Any]
    effective_overrides: Dict[str, Any]
    resolved_config: Dict[str, Any]
    events: List[JobEvent]
    error: Optional[str] = None
    s3_key: Optional[str] = None


class ConfigMetadata(BaseModel):
    """
    Configuration schema and available options for the pipeline.

    Provides clients with information about configurable parameters,
    valid values, and default settings for job creation.

    Attributes:
        defaults: Default configuration values from config.yaml
        strategies: Available strategies for each processing step
                   (e.g., crop_strategy: ["llm", "none"])
        render_strategies: Valid rendering approach options
        layout_types: Available page layout type definitions
        boolean_flags: Configuration keys that accept boolean values
        notes: Additional documentation for specific configuration keys

    Note:
        This metadata is derived from the adt-press pipeline configuration
        and allows clients to build dynamic configuration UIs.
    """

    defaults: Dict[str, Any]
    strategies: Dict[str, List[str]]
    render_strategies: List[str]
    layout_types: Dict[str, Any]
    boolean_flags: List[str]
    notes: Dict[str, str]


class RegenerateRequest(BaseModel):
    """
    Request model for regenerating or editing specific sections of a completed job.

    This allows users to selectively regenerate or edit pages without
    re-running the entire pipeline from scratch.

    Attributes:
        regenerate_sections: List of section IDs to regenerate from scratch.
                            Format: ["sec_page_5_s0", "sec_page_7_s1"]
        edit_sections: Dict mapping section IDs to natural language edit instructions.
                      Format: {"sec_page_5_s0": "make the title bigger"}

    Note:
        At least one of regenerate_sections or edit_sections must be provided.
        A section cannot appear in both lists simultaneously.
    """

    regenerate_sections: List[str] = []
    edit_sections: Dict[str, str] = {}


class SectionEditRequest(BaseModel):
    """
    Request model for stateless section editing.

    This allows clients to send HTML content and receive edited HTML back
    without requiring job context or server-side state.

    Attributes:
        html: The current HTML content to edit
        edit_instruction: Natural language instruction for how to modify the content
        section_id: The section ID (for context in LLM prompt)
        section_type: Type of section (e.g., "content", "activity"). Defaults to "content"
        page_number: Page number for context. Defaults to 1
        language: Language of the content. Defaults to "English"
    """

    html: str
    edit_instruction: str
    section_id: str
    section_type: str = "content"
    page_number: int = 1
    language: str = "English"


class SectionEditResponse(BaseModel):
    """
    Response model for stateless section editing.

    Attributes:
        html: The updated HTML content after applying the edit
        reasoning: Explanation of what changes were made and why
    """

    html: str
    reasoning: str
