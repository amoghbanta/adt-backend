"""
SQLite database for persistent job storage.

This module provides a simple SQLite-based persistence layer for job records,
ensuring jobs survive server restarts.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import JobStatus


# Default database path
DEFAULT_DB_PATH = Path("data/jobs.db")


def _ensure_db_dir(db_path: Path) -> None:
    """Ensure the database directory exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)


def _serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format string."""
    return dt.isoformat() if dt else None


def _deserialize_datetime(s: str) -> Optional[datetime]:
    """Deserialize ISO format string to datetime."""
    if not s:
        return None
    return datetime.fromisoformat(s)


class JobDatabase:
    """
    SQLite database for job persistence.

    Thread-safe: SQLite handles concurrent access with WAL mode.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        _ensure_db_dir(db_path)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    display_label TEXT NOT NULL,
                    effective_label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    pdf_filename TEXT NOT NULL,
                    pdf_path TEXT NOT NULL,
                    submitted_overrides TEXT,
                    overrides TEXT,
                    resolved_config TEXT,
                    output_dir TEXT NOT NULL,
                    plate_path TEXT,
                    zip_path TEXT,
                    s3_key TEXT,
                    error TEXT,
                    events TEXT
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_created_at
                ON jobs(created_at DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON jobs(status)
            """)

    def save_job(self, job_data: Dict[str, Any]) -> None:
        """
        Save or update a job record.

        Args:
            job_data: Dictionary with job fields
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO jobs (
                    id, display_label, effective_label, status,
                    created_at, updated_at, pdf_filename, pdf_path,
                    submitted_overrides, overrides, resolved_config,
                    output_dir, plate_path, zip_path, s3_key, error, events
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_data["id"],
                job_data["display_label"],
                job_data["effective_label"],
                job_data["status"],
                _serialize_datetime(job_data["created_at"]),
                _serialize_datetime(job_data["updated_at"]),
                job_data["pdf_filename"],
                str(job_data["pdf_path"]),
                json.dumps(job_data.get("submitted_overrides", {})),
                json.dumps(job_data.get("overrides", {})),
                json.dumps(job_data.get("resolved_config", {})),
                str(job_data["output_dir"]),
                str(job_data["plate_path"]) if job_data.get("plate_path") else None,
                str(job_data["zip_path"]) if job_data.get("zip_path") else None,
                job_data.get("s3_key"),
                job_data.get("error"),
                json.dumps([
                    {"timestamp": _serialize_datetime(e["timestamp"]), "message": e["message"]}
                    for e in job_data.get("events", [])
                ]),
            ))

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a job by ID.

        Args:
            job_id: The job ID

        Returns:
            Job data dictionary or None if not found
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()

            if not row:
                return None

            return self._row_to_dict(row)

    def list_jobs(self) -> List[Dict[str, Any]]:
        """
        List all jobs ordered by creation time (newest first).

        Returns:
            List of job data dictionaries
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()

            return [self._row_to_dict(row) for row in rows]

    def update_job_status(
        self,
        job_id: str,
        status: str,
        error: Optional[str] = None,
        s3_key: Optional[str] = None,
        plate_path: Optional[str] = None,
    ) -> None:
        """
        Update job status and related fields.

        Args:
            job_id: The job ID
            status: New status value
            error: Optional error message
            s3_key: Optional S3 key for completed jobs
            plate_path: Optional plate file path
        """
        with self._get_connection() as conn:
            updates = ["status = ?", "updated_at = ?"]
            values = [status, _serialize_datetime(datetime.utcnow())]

            if error is not None:
                updates.append("error = ?")
                values.append(error)

            if s3_key is not None:
                updates.append("s3_key = ?")
                values.append(s3_key)

            if plate_path is not None:
                updates.append("plate_path = ?")
                values.append(str(plate_path))

            values.append(job_id)

            conn.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?",
                values
            )

    def add_job_event(self, job_id: str, message: str) -> None:
        """
        Add an event to a job's event log.

        Args:
            job_id: The job ID
            message: Event message
        """
        with self._get_connection() as conn:
            # Get current events
            row = conn.execute(
                "SELECT events FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()

            if not row:
                return

            events = json.loads(row["events"] or "[]")
            events.append({
                "timestamp": _serialize_datetime(datetime.utcnow()),
                "message": message,
            })

            conn.execute(
                "UPDATE jobs SET events = ?, updated_at = ? WHERE id = ?",
                (json.dumps(events), _serialize_datetime(datetime.utcnow()), job_id)
            )

    def delete_job(self, job_id: str) -> bool:
        """
        Delete a job record.

        Args:
            job_id: The job ID

        Returns:
            True if deleted, False if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return cursor.rowcount > 0

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a database row to a job data dictionary."""
        events_raw = json.loads(row["events"] or "[]")
        events = [
            {
                "timestamp": _deserialize_datetime(e["timestamp"]),
                "message": e["message"],
            }
            for e in events_raw
        ]

        return {
            "id": row["id"],
            "display_label": row["display_label"],
            "effective_label": row["effective_label"],
            "status": row["status"],
            "created_at": _deserialize_datetime(row["created_at"]),
            "updated_at": _deserialize_datetime(row["updated_at"]),
            "pdf_filename": row["pdf_filename"],
            "pdf_path": Path(row["pdf_path"]),
            "submitted_overrides": json.loads(row["submitted_overrides"] or "{}"),
            "overrides": json.loads(row["overrides"] or "{}"),
            "resolved_config": json.loads(row["resolved_config"] or "{}"),
            "output_dir": Path(row["output_dir"]),
            "plate_path": Path(row["plate_path"]) if row["plate_path"] else None,
            "zip_path": Path(row["zip_path"]) if row["zip_path"] else None,
            "s3_key": row["s3_key"],
            "error": row["error"],
            "events": events,
        }
