
import sqlite3
import hashlib
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from uuid import uuid4

@dataclass
class APIKeyRecord:
    id: str
    owner: str
    prefix: str
    max_generations: int
    current_generations: int
    is_active: bool
    created_at: str

class KeyManager:
    """
    Manages API keys and usage tracking using a local SQLite database.
    """

    def __init__(self, db_path: str = "data/adt_press.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initialize the database schema if it doesn't exist."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    key_hash TEXT UNIQUE NOT NULL,
                    prefix TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    max_generations INTEGER NOT NULL,
                    current_generations INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def _hash_key(self, key: str) -> str:
        """SHA-256 hash of the API key."""
        return hashlib.sha256(key.encode()).hexdigest()

    def create_key(self, owner: str, max_generations: int = 100) -> Tuple[str, dict]:
        """
        Generate a new API key for a user.
        
        Returns:
            Tuple[str, dict]: (raw_api_key, key_record_dict)
            WARNING: raw_api_key is shown ONLY ONCE here.
        """
        # Generate a secure random key
        raw_key = f"adt_{secrets.token_urlsafe(32)}"
        key_hash = self._hash_key(raw_key)
        prefix = raw_key[:8]
        key_id = str(uuid4())
        created_at = datetime.utcnow().isoformat()

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO api_keys (id, key_hash, prefix, owner, max_generations, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (key_id, key_hash, prefix, owner, max_generations, created_at))
            conn.commit()

        record = {
            "id": key_id,
            "prefix": prefix,
            "owner": owner,
            "max_generations": max_generations,
            "current_generations": 0,
            "is_active": True,
            "created_at": created_at
        }
        return raw_key, record

    def validate_key(self, key: str) -> Optional[APIKeyRecord]:
        """
        Validate an API key and return its record if valid.
        """
        if not key:
            return None
            
        key_hash = self._hash_key(key)
        
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ? AND is_active = 1",
                (key_hash,)
            ).fetchone()
            
            if row:
                return APIKeyRecord(
                    id=row["id"],
                    owner=row["owner"],
                    prefix=row["prefix"],
                    max_generations=row["max_generations"],
                    current_generations=row["current_generations"],
                    is_active=bool(row["is_active"]),
                    created_at=row["created_at"]
                )
        return None

    def check_quota(self, key_id: str) -> bool:
        """
        Check if the key has remaining generations.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT max_generations, current_generations FROM api_keys WHERE id = ?",
                (key_id,)
            ).fetchone()
            
            if row:
                return row["current_generations"] < row["max_generations"]
        return False

    def increment_usage(self, key_id: str) -> bool:
        """
        Increment the usage count for a key. Returns True if successful (within quota).
        """
        with self._get_conn() as conn:
            # Check quota first with a lock (transaction)
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                row = cursor.execute(
                    "SELECT max_generations, current_generations FROM api_keys WHERE id = ?",
                    (key_id,)
                ).fetchone()
                
                if not row or row["current_generations"] >= row["max_generations"]:
                    return False
                
                cursor.execute(
                    "UPDATE api_keys SET current_generations = current_generations + 1 WHERE id = ?",
                    (key_id,)
                )
                conn.commit()
                return True
            except:
                conn.rollback()
                raise

    def list_keys(self) -> list[dict]:
        """List all API keys (admin only)."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
            return [dict(row) for row in rows]

    def revoke_key(self, key_id: str) -> bool:
        """Revoke a key by ID."""
        with self._get_conn() as conn:
            cursor = conn.execute("UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,))
            conn.commit()
            return cursor.rowcount > 0
