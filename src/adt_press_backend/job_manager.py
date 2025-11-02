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

from adt_press.pipeline import run_pipeline

from .configuration import build_config_metadata, make_runtime_config
from .models import ConfigMetadata, JobDetail, JobEvent, JobStatus, JobSummary
from .utils import ensure_directory, sanitize_label


@dataclass
class JobRecord:
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
    def __init__(
        self,
        output_root: Path | None = None,
        upload_root: Path | None = None,
        max_workers: int = 1,
    ) -> None:
        self.output_root = ensure_directory(output_root or Path("output"))
        self.upload_root = ensure_directory(upload_root or Path("uploads"))
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._config_metadata: ConfigMetadata | None = None

    def list_jobs(self) -> list[JobSummary]:
        with self._lock:
            records = sorted(self._jobs.values(), key=lambda r: r.created_at, reverse=True)
            return [record.to_summary() for record in records]

    def get_job(self, job_id: str) -> Optional[JobDetail]:
        with self._lock:
            record = self._jobs.get(job_id)
            return record.to_detail() if record else None

    def _register_job(self, record: JobRecord) -> None:
        with self._lock:
            self._jobs[record.id] = record

    def _update_job(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            record = self._jobs[job_id]
            for key, value in kwargs.items():
                setattr(record, key, value)
            record.updated_at = datetime.utcnow()

    def _append_event(self, job_id: str, message: str) -> None:
        event = JobEvent(timestamp=datetime.utcnow(), message=message)
        with self._lock:
            record = self._jobs[job_id]
            record.events.append(event)
            record.updated_at = event.timestamp

    def _persist_config(self, record: JobRecord) -> None:
        config_path = record.output_dir / "config.yaml"
        ensure_directory(record.output_dir)
        OmegaConf.save(record.runtime_config, config_path)

        # also persist overrides for transparency
        submitted_path = record.output_dir / "submitted_overrides.json"
        submitted_path.write_text(json.dumps(record.submitted_overrides, indent=2), encoding="utf-8")

        effective_path = record.output_dir / "effective_overrides.json"
        effective_path.write_text(json.dumps(record.overrides, indent=2), encoding="utf-8")

    def create_job(
        self,
        display_label: str,
        pdf_filename: str,
        pdf_path: Path,
        overrides: Dict[str, Any],
    ) -> JobSummary:
        job_id = uuid4().hex
        safe_label = sanitize_label(display_label, fallback=f"job-{job_id[:8]}")
        effective_label = f"{safe_label}-{job_id[:8]}"

        submitted_overrides = dict(overrides)
        overrides_with_defaults = {
            **submitted_overrides,
            "label": effective_label,
            "pdf_path": str(pdf_path),
        }

        runtime_config = make_runtime_config(overrides_with_defaults)
        resolved_config = OmegaConf.to_container(runtime_config, resolve=True, enum_to_str=True)  # type: ignore[assignment]
        output_dir = Path(resolved_config["run_output_dir"]) if resolved_config.get("run_output_dir") else self.output_root / effective_label
        ensure_directory(output_dir)

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
        self._persist_config(record)

        record.events.append(JobEvent(timestamp=record.created_at, message="Job registered and awaiting execution."))
        self._register_job(record)
        self._executor.submit(self._run_pipeline, job_id)

        return record.to_summary()

    def _run_pipeline(self, job_id: str) -> None:
        self._update_job(job_id, status=JobStatus.RUNNING)
        self._append_event(job_id, "Pipeline execution started.")

        with self._lock:
            runtime_config = self._jobs[job_id].runtime_config
            resolved_config = self._jobs[job_id].resolved_config

        try:
            run_pipeline(runtime_config)
            plate_path = Path(resolved_config["run_output_dir"]) / "plate.json"
            if plate_path.exists():
                self._update_job(job_id, plate_path=plate_path, status=JobStatus.COMPLETED)
            else:
                self._update_job(job_id, status=JobStatus.COMPLETED)
            self._append_event(job_id, "Pipeline execution completed.")
        except Exception as exc:  # noqa: BLE001
            self._update_job(job_id, status=JobStatus.FAILED, error=str(exc))
            self._append_event(job_id, f"Pipeline failed: {exc}")
        finally:
            self._update_job(job_id)

    def save_plate(self, job_id: str, plate_data: Dict[str, Any]) -> Path:
        with self._lock:
            record = self._jobs[job_id]
        if record.status != JobStatus.COMPLETED:
            raise RuntimeError("Job must be completed before saving plate edits.")

        plate_path = record.plate_path or (record.output_dir / "plate.json")
        plate_path.write_text(json.dumps(plate_data, indent=2), encoding="utf-8")
        record.plate_path = plate_path
        self._append_event(job_id, "Plate updated via API.")
        return plate_path

    def load_plate(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            record = self._jobs[job_id]
            plate_path = record.plate_path or (record.output_dir / "plate.json")
        if not plate_path.exists():
            raise FileNotFoundError("Plate file not found for this job.")
        return json.loads(plate_path.read_text(encoding="utf-8"))

    def get_config_metadata(self) -> ConfigMetadata:
        if self._config_metadata is None:
            self._config_metadata = build_config_metadata()
        return self._config_metadata
