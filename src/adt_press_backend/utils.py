"""
Utility functions for file system operations and string sanitization.

This module provides helper functions for:
- Sanitizing user-provided strings for safe filesystem usage
- Ensuring directory creation with proper error handling
- Validating and parsing file extensions
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

# Pattern to match characters that are not safe for filesystem paths
# Allows: alphanumeric characters, dots, underscores, and hyphens
SANITIZE_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")


def sanitize_label(label: str, fallback: str) -> str:
    """
    Generate a filesystem-safe label from user input.

    This function removes or replaces characters that might cause issues
    in filesystem paths, ensuring compatibility across different operating systems.

    Args:
        label: The original label string to sanitize
        fallback: Default value to return if sanitization results in an empty string

    Returns:
        A lowercase, filesystem-safe label or the fallback value

    Example:
        >>> sanitize_label("My Document!", "default-doc")
        "my-document"
        >>> sanitize_label("@#$", "default-doc")
        "default-doc"
    """
    # Replace non-safe characters with hyphens and normalize whitespace
    cleaned = SANITIZE_PATTERN.sub("-", label.strip())
    # Remove leading/trailing separators and convert to lowercase
    cleaned = cleaned.strip("-_.").lower()
    # Return fallback if result is empty
    return cleaned or fallback


def ensure_directory(path: Path) -> Path:
    """
    Create a directory if it doesn't exist, including parent directories.

    This is a safe idempotent operation that won't fail if the directory
    already exists.

    Args:
        path: The directory path to create

    Returns:
        The same path object for chaining

    Raises:
        OSError: If directory creation fails due to permissions or other I/O errors
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def split_extension(filename: str) -> tuple[str, str]:
    """
    Split a filename into stem and extension components.

    Args:
        filename: The filename to split (can include path)

    Returns:
        A tuple of (stem, extension) where extension includes the dot

    Example:
        >>> split_extension("document.pdf")
        ("document", ".pdf")
        >>> split_extension("/path/to/file.tar.gz")
        ("file.tar", ".gz")
    """
    path = Path(filename)
    return path.stem, path.suffix


def allowed_pdf_extensions() -> Iterable[str]:
    """
    Get the list of allowed PDF file extensions.

    Returns:
        An iterable of valid PDF extensions (currently only '.pdf')

    Note:
        This function exists for future extensibility if additional
        PDF-like formats need to be supported (e.g., '.PDF', compressed formats)
    """
    return [".pdf"]
