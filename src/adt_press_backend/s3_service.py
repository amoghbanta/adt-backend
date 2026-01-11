"""
S3 service module for uploading job outputs and generating presigned URLs.

This module provides functionality for:
- Creating zip archives from job output directories
- Uploading zip files to S3
- Generating presigned URLs for secure, time-limited downloads

The S3 bucket name is configured via the S3_BUCKET_NAME environment variable.
When running locally without AWS credentials, S3 operations are skipped gracefully.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# S3 bucket name from environment variable
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "")

# S3 client (lazy initialization)
_s3_client = None


def _get_s3_client():
    """
    Get or create the S3 client.

    Returns:
        boto3 S3 client or None if bucket is not configured
    
    Note:
        We no longer test credentials with list_buckets() because that requires
        s3:ListAllMyBuckets permission which follows least-privilege principle.
        Credential errors will surface during actual upload operations.
    """
    global _s3_client
    if _s3_client is None:
        if not S3_BUCKET_NAME:
            logger.warning("S3_BUCKET_NAME not configured")
            return None
        try:
            _s3_client = boto3.client("s3")
        except Exception as e:
            logger.warning(f"Failed to create S3 client: {e}")
            _s3_client = None
    return _s3_client


def zip_directory(source_dir: Path, zip_path: Path) -> Path:
    """
    Create a zip archive from a directory.

    Args:
        source_dir: Path to the directory to zip
        zip_path: Path where the zip file should be created (without .zip extension)

    Returns:
        Path to the created zip file (with .zip extension)

    Note:
        The source directory contents are placed at the root of the zip archive.
        Uses shutil.make_archive which handles large directories efficiently.
    """
    # Remove .zip extension if present for shutil.make_archive
    zip_base = str(zip_path).removesuffix(".zip")
    
    logger.info(f"Creating zip archive: {zip_base}.zip from {source_dir}")
    
    # Create the zip archive
    archive_path = shutil.make_archive(
        base_name=zip_base,
        format="zip",
        root_dir=source_dir.parent,
        base_dir=source_dir.name
    )
    
    logger.info(f"Zip archive created: {archive_path}")
    return Path(archive_path)


def upload_to_s3(zip_path: Path, s3_key: str) -> bool:
    """
    Upload a zip file to S3.

    Args:
        zip_path: Path to the local zip file
        s3_key: S3 object key (path within the bucket)

    Returns:
        True if upload was successful, False otherwise

    Note:
        If S3 credentials are not available or bucket is not configured,
        this function returns False without raising an exception.
    """
    if not S3_BUCKET_NAME:
        logger.warning("S3_BUCKET_NAME not configured, skipping upload")
        return False

    client = _get_s3_client()
    if client is None:
        logger.warning("S3 client not available, skipping upload")
        return False

    try:
        logger.info(f"Uploading {zip_path} to s3://{S3_BUCKET_NAME}/{s3_key}")
        client.upload_file(str(zip_path), S3_BUCKET_NAME, s3_key)
        logger.info(f"Upload successful: s3://{S3_BUCKET_NAME}/{s3_key}")
        return True
    except ClientError as e:
        logger.error(f"S3 upload failed: {e}")
        return False


def generate_presigned_url(s3_key: str, expiration: int = 3600) -> Optional[str]:
    """
    Generate a presigned URL for downloading a file from S3.

    Args:
        s3_key: S3 object key (path within the bucket)
        expiration: URL expiration time in seconds (default: 3600 = 1 hour)

    Returns:
        Presigned URL string, or None if generation fails

    Note:
        The presigned URL allows anyone with the URL to download the file
        until the expiration time is reached.
    """
    if not S3_BUCKET_NAME:
        logger.warning("S3_BUCKET_NAME not configured")
        return None

    client = _get_s3_client()
    if client is None:
        logger.warning("S3 client not available")
        return None

    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": s3_key},
            ExpiresIn=expiration
        )
        logger.info(f"Generated presigned URL for {s3_key} (expires in {expiration}s)")
        return url
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        return None


def is_s3_configured() -> bool:
    """
    Check if S3 is properly configured and accessible.

    Returns:
        True if S3 bucket is configured and credentials are available
    """
    return bool(S3_BUCKET_NAME) and _get_s3_client() is not None
