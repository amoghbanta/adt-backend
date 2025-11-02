from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

SANITIZE_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")


def sanitize_label(label: str, fallback: str) -> str:
    """Generate a filesystem-safe label. Returns fallback if result is empty."""
    cleaned = SANITIZE_PATTERN.sub("-", label.strip()).strip("-_.").lower()
    return cleaned or fallback


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def split_extension(filename: str) -> tuple[str, str]:
    path = Path(filename)
    stem = path.stem
    suffix = path.suffix
    return stem, suffix


def allowed_pdf_extensions() -> Iterable[str]:
    return [".pdf"]
