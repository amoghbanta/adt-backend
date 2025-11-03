"""
Job orchestration and lifecycle management for ADT Press pipeline.

This module manages the end-to-end lifecycle of document processing jobs:
- Job creation and registration
- Asynchronous pipeline execution
- Status tracking and event logging
- Plate file (intermediate result) management
- Thread-safe access to job state

The JobManager class provides the core business logic for the API, coordinating
between user requests, configuration management, and pipeline execution.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional
from uuid import uuid4

from omegaconf import DictConfig, OmegaConf

# Import the ADT Press pipeline runner
# Try package import first, fall back to local development path
try:
    from adt_press.pipeline import run_pipeline
except ModuleNotFoundError:
    # Development fallback: search parent directories for local adt_press
    import sys
    from pathlib import Path

    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "adt_press"
        if candidate.exists():
            sys.path.insert(0, str(parent))
            break

    from adt_press.pipeline import run_pipeline  # type: ignore

from .configuration import build_config_metadata, make_runtime_config
from .models import ConfigMetadata, JobDetail, JobEvent, JobStatus, JobSummary
from .utils import ensure_directory, sanitize_label


@dataclass
class JobRecord:
    """
    Internal representation of a processing job with full state.

    This dataclass stores all job-related data and is used internally by
    JobManager for thread-safe state management. It maintains both the
    original user inputs and the derived/computed values.

    Attributes:
        id: Unique job identifier (hex UUID)
        display_label: User-provided label for UI display
        effective_label: Filesystem-safe label with unique suffix (e.g., "doc-a1b2c3d4")
        status: Current execution status
        created_at: Job creation timestamp (UTC)
        updated_at: Last modification timestamp (UTC)
        pdf_filename: Original uploaded PDF filename
        pdf_path: Path to stored PDF file
        submitted_overrides: Configuration values submitted by user
        overrides: Effective overrides including auto-injected values (label, pdf_path)
        runtime_config: OmegaConf configuration object for pipeline
        resolved_config: Final resolved configuration as dictionary
        output_dir: Directory where job outputs are stored
        plate_path: Path to plate.json if available
        error: Error message if job failed
        events: Chronological list of job lifecycle events
    """

    id: str
    display_label: str
    effective_label: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    pdf_filename: str
    pdf_path: Path
    submitted_overrides: Dict[str, Any]
    overrides: Dict[str, Any]
    runtime_config: DictConfig
    resolved_config: Dict[str, Any]
    output_dir: Path
    plate_path: Optional[Path] = None
    error: Optional[str] = None
    events: list[JobEvent] = field(default_factory=list)

    def to_summary(self) -> JobSummary:
        """
        Convert to a lightweight summary representation.

        Returns:
            JobSummary with essential fields for list views
        """
        return JobSummary(
            id=self.id,
            label=self.effective_label,
            display_label=self.display_label,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pdf_filename=self.pdf_filename,
            output_dir=str(self.output_dir),
            plate_available=bool(self.plate_path and self.plate_path.exists()),
        )

    def to_detail(self) -> JobDetail:
        """
        Convert to a detailed representation with full information.

        Returns:
            JobDetail with all fields including configuration and events
        """
        summary = self.to_summary()
        return JobDetail(
            **summary.model_dump(),
            submitted_overrides=self.submitted_overrides,
            effective_overrides=self.overrides,
            resolved_config=self.resolved_config,
            events=self.events,
            error=self.error,
        )


class JobManager:
    """
    Central coordinator for job lifecycle management.

    This class orchestrates all aspects of job processing:
    - Creating jobs and validating configuration
    - Executing the ADT Press pipeline asynchronously
    - Managing job state in a thread-safe manner
    - Persisting configuration and intermediate results

    Thread Safety:
        All job state modifications are protected by a lock to ensure
        consistency when accessed from multiple HTTP request threads.

    Attributes:
        output_root: Base directory for job outputs
        upload_root: Base directory for uploaded PDFs
    """

    def __init__(
        self,
        output_root: Path | None = None,
        upload_root: Path | None = None,
        max_workers: int = 1,
    ) -> None:
        """
        Initialize the job manager.

        Args:
            output_root: Base directory for job outputs (default: ./output)
            upload_root: Base directory for uploads (default: ./uploads)
            max_workers: Number of concurrent pipeline executions (default: 1)

        Note:
            Setting max_workers > 1 enables parallel job processing but may
            increase resource usage. Consider available CPU and memory when
            configuring this value.
        """
        self.output_root = ensure_directory(output_root or Path("output"))
        self.upload_root = ensure_directory(upload_root or Path("uploads"))
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._config_metadata: ConfigMetadata | None = None

    def list_jobs(self) -> list[JobSummary]:
        """
        Get all jobs sorted by creation time (newest first).

        Returns:
            List of job summaries for all registered jobs

        Thread Safety:
            Acquires lock for consistent snapshot of job state
        """
        with self._lock:
            records = sorted(self._jobs.values(), key=lambda r: r.created_at, reverse=True)
            return [record.to_summary() for record in records]

    def get_job(self, job_id: str) -> Optional[JobDetail]:
        """
        Get detailed information about a specific job.

        Args:
            job_id: The unique job identifier

        Returns:
            JobDetail if found, None otherwise

        Thread Safety:
            Acquires lock for consistent snapshot of job state
        """
        with self._lock:
            record = self._jobs.get(job_id)
            return record.to_detail() if record else None

    def _register_job(self, record: JobRecord) -> None:
        """
        Register a new job in the internal registry.

        Args:
            record: The job record to register

        Thread Safety:
            Acquires lock before modifying job registry
        """
        with self._lock:
            self._jobs[record.id] = record

    def _update_job(self, job_id: str, **kwargs: Any) -> None:
        """
        Update job attributes and refresh the updated_at timestamp.

        Args:
            job_id: The job to update
            **kwargs: Attributes to update on the job record

        Thread Safety:
            Acquires lock before modifying job state

        Note:
            The updated_at timestamp is automatically refreshed to the current UTC time.
        """
        with self._lock:
            record = self._jobs[job_id]
            for key, value in kwargs.items():
                setattr(record, key, value)
            record.updated_at = datetime.utcnow()

    def _append_event(self, job_id: str, message: str) -> None:
        """
        Add a timestamped event to a job's event log.

        Args:
            job_id: The job to add the event to
            message: Human-readable event description

        Thread Safety:
            Acquires lock before modifying job events
        """
        event = JobEvent(timestamp=datetime.utcnow(), message=message)
        with self._lock:
            record = self._jobs[job_id]
            record.events.append(event)
            record.updated_at = event.timestamp

    def _persist_config(self, record: JobRecord) -> None:
        """
        Save job configuration to disk for reproducibility and debugging.

        Persists three files in the job's output directory:
        - config.yaml: Full resolved configuration
        - submitted_overrides.json: User-provided overrides
        - effective_overrides.json: All overrides including auto-injected values

        Args:
            record: The job whose configuration to persist

        Note:
            This enables full transparency and reproducibility - users can see
            exactly what configuration was used for each job.
        """
        config_path = record.output_dir / "config.yaml"
        ensure_directory(record.output_dir)
        OmegaConf.save(record.runtime_config, config_path)

        # Persist user-submitted overrides for transparency
        submitted_path = record.output_dir / "submitted_overrides.json"
        submitted_path.write_text(json.dumps(record.submitted_overrides, indent=2), encoding="utf-8")

        # Persist effective overrides (includes auto-injected label and pdf_path)
        effective_path = record.output_dir / "effective_overrides.json"
        effective_path.write_text(json.dumps(record.overrides, indent=2), encoding="utf-8")

    def create_job(
        self,
        display_label: str,
        pdf_filename: str,
        pdf_path: Path,
        overrides: Dict[str, Any],
    ) -> JobSummary:
        """
        Create and register a new processing job.

        This method:
        1. Generates a unique job ID
        2. Creates a filesystem-safe label with unique suffix
        3. Merges user overrides with defaults
        4. Persists configuration to disk
        5. Submits job for asynchronous execution

        Args:
            display_label: User-provided label for UI display
            pdf_filename: Original uploaded PDF filename
            pdf_path: Path to the stored PDF file
            overrides: User-provided configuration overrides

        Returns:
            JobSummary of the created job

        Note:
            The job is immediately submitted to the executor and will begin
            processing as soon as a worker is available.
        """
        # Generate unique identifiers
        job_id = uuid4().hex
        safe_label = sanitize_label(display_label, fallback=f"job-{job_id[:8]}")
        # Append short job ID to ensure uniqueness even for duplicate labels
        effective_label = f"{safe_label}-{job_id[:8]}"

        # Preserve user input and add required fields
        submitted_overrides = dict(overrides)
        overrides_with_defaults = {
            **submitted_overrides,
            "label": effective_label,
            "pdf_path": str(pdf_path),
        }

        # Build runtime configuration by merging overrides with defaults
        runtime_config = make_runtime_config(overrides_with_defaults)
        resolved_config = OmegaConf.to_container(runtime_config, resolve=True, enum_to_str=True)  # type: ignore[assignment]

        # Determine output directory (from config or default location)
        output_dir = Path(resolved_config["run_output_dir"]) if resolved_config.get("run_output_dir") else self.output_root / effective_label
        ensure_directory(output_dir)

        # Create job record with all metadata
        record = JobRecord(
            id=job_id,
            display_label=display_label,
            effective_label=effective_label,
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            pdf_filename=pdf_filename,
            pdf_path=pdf_path,
            submitted_overrides=submitted_overrides,
            overrides=overrides_with_defaults,
            runtime_config=runtime_config,
            resolved_config=resolved_config,  # type: ignore[arg-type]
            output_dir=output_dir,
        )

        # Persist configuration for reproducibility
        self._persist_config(record)

        # Log initial event and register job
        record.events.append(JobEvent(timestamp=record.created_at, message="Job registered and awaiting execution."))
        self._register_job(record)

        # Submit for asynchronous execution
        self._executor.submit(self._run_pipeline, job_id)

        return record.to_summary()

    def _run_pipeline(self, job_id: str) -> None:
        """
        Execute the ADT Press pipeline for a job (runs in background thread).

        This method is invoked by the thread pool executor and handles
        the complete pipeline execution lifecycle, including error handling
        and status updates.

        Args:
            job_id: The job to process

        Note:
            This method runs in a background thread. All job state modifications
            must use the lock-protected update methods to ensure thread safety.
        """
        self._update_job(job_id, status=JobStatus.RUNNING)
        self._append_event(job_id, "Pipeline execution started.")

        # Get configuration snapshot under lock
        with self._lock:
            runtime_config = self._jobs[job_id].runtime_config
            resolved_config = self._jobs[job_id].resolved_config

        try:
            # Execute the pipeline (this may take several minutes for large documents)
            run_pipeline(runtime_config)

            # Check if pipeline generated a plate.json file
            plate_path = Path(resolved_config["run_output_dir"]) / "plate.json"
            if plate_path.exists():
                self._update_job(job_id, plate_path=plate_path, status=JobStatus.COMPLETED)
            else:
                self._update_job(job_id, status=JobStatus.COMPLETED)

            self._append_event(job_id, "Pipeline execution completed.")
        except Exception as exc:
            # Capture error and mark job as failed
            self._update_job(job_id, status=JobStatus.FAILED, error=str(exc))
            self._append_event(job_id, f"Pipeline failed: {exc}")
        finally:
            # Ensure updated_at is refreshed
            self._update_job(job_id)

    def save_plate(self, job_id: str, plate_data: Dict[str, Any]) -> Path:
        """
        Save edited plate data for a completed job.

        The plate.json file contains intermediate layout information that
        users can edit before regenerating final outputs.

        Args:
            job_id: The job whose plate to update
            plate_data: New plate data (typically from user edits)

        Returns:
            Path to the saved plate.json file

        Raises:
            RuntimeError: If job is not in COMPLETED status
            KeyError: If job_id doesn't exist

        Note:
            Plate edits are only allowed for completed jobs to prevent
            conflicts with ongoing pipeline execution.
        """
        with self._lock:
            record = self._jobs[job_id]
        if record.status != JobStatus.COMPLETED:
            raise RuntimeError("Job must be completed before saving plate edits.")

        # Determine plate path (use existing or default location)
        plate_path = record.plate_path or (record.output_dir / "plate.json")
        plate_path.write_text(json.dumps(plate_data, indent=2), encoding="utf-8")
        record.plate_path = plate_path
        self._append_event(job_id, "Plate updated via API.")
        return plate_path

    def load_plate(self, job_id: str) -> Dict[str, Any]:
        """
        Load plate data for a job.

        Args:
            job_id: The job whose plate to load

        Returns:
            Parsed plate.json data

        Raises:
            FileNotFoundError: If plate.json doesn't exist for this job
            KeyError: If job_id doesn't exist
            json.JSONDecodeError: If plate file is not valid JSON
        """
        with self._lock:
            record = self._jobs[job_id]
            plate_path = record.plate_path or (record.output_dir / "plate.json")

        if not plate_path.exists():
            raise FileNotFoundError("Plate file not found for this job.")

        return json.loads(plate_path.read_text(encoding="utf-8"))

    def get_config_metadata(self) -> ConfigMetadata:
        """
        Get configuration metadata (cached after first call).

        Returns:
            ConfigMetadata with defaults, strategies, and documentation

        Note:
            Metadata is cached to avoid redundant configuration processing.
        """
        if self._config_metadata is None:
            self._config_metadata = build_config_metadata()
        return self._config_metadata
