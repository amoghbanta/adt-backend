"""
Pytest configuration and fixtures for ADT Press Backend tests.
"""

import os
import pytest
import tempfile
import shutil
from pathlib import Path
from fastapi.testclient import TestClient

# Set test environment variables before importing the app
os.environ["ADT_API_KEY"] = "test-master-key-12345"
os.environ["OUTPUT_DIR"] = tempfile.mkdtemp(prefix="adt_test_output_")
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="adt_test_uploads_")

from adt_press_backend.main import app, job_manager, key_manager


@pytest.fixture(scope="session")
def test_dirs():
    """Create and cleanup test directories."""
    output_dir = os.environ["OUTPUT_DIR"]
    upload_dir = os.environ["UPLOAD_DIR"]

    yield {
        "output": output_dir,
        "upload": upload_dir,
    }

    # Cleanup after all tests
    shutil.rmtree(output_dir, ignore_errors=True)
    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def master_key():
    """Return the master API key for admin operations."""
    return "test-master-key-12345"


@pytest.fixture
def api_key(client, master_key):
    """Create a test API key with default quota."""
    response = client.post(
        "/admin/keys",
        json={"owner": "test-user", "max_generations": 10},
        headers={"X-API-Key": master_key},
    )
    assert response.status_code == 201
    data = response.json()
    return data["api_key"]


@pytest.fixture
def sample_pdf():
    """Create a minimal valid PDF file for testing."""
    # Minimal PDF that is technically valid
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>
endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer
<< /Size 4 /Root 1 0 R >>
startxref
196
%%EOF"""

    # Create a temp file
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        os.write(fd, pdf_content)
        os.close(fd)
        yield path
    finally:
        os.unlink(path)


@pytest.fixture
def sample_html():
    """Sample HTML content for section editing tests."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Test Section</title>
</head>
<body>
  <div class="container">
    <h1>Test Title</h1>
    <p>This is a test paragraph with some content.</p>
    <img src="./images/test.jpg" alt="Test image"/>
  </div>
</body>
</html>"""
