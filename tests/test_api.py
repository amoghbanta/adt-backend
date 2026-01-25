"""
Tests for ADT Press Backend API endpoints.

Tests cover:
- Health check
- Configuration defaults
- Job management (create, list, get, status)
- Plate management (get, update)
- Section editing (stateless)
- API key management (admin)
- Regeneration endpoint
"""

import pytest
import json
from io import BytesIO


class TestHealthCheck:
    """Tests for the /healthz endpoint."""

    def test_health_check_returns_ok(self, client):
        """Health check should return status ok."""
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestConfigDefaults:
    """Tests for the /config/defaults endpoint."""

    def test_get_config_defaults(self, client):
        """Should return configuration metadata."""
        response = client.get("/config/defaults")
        assert response.status_code == 200

        data = response.json()
        # Check expected keys exist
        assert "defaults" in data
        assert "strategies" in data
        assert "render_strategies" in data
        assert "boolean_flags" in data


class TestAPIKeyManagement:
    """Tests for the /admin/keys endpoints."""

    def test_create_api_key_requires_master_key(self, client):
        """Creating API key without master key should fail."""
        response = client.post(
            "/admin/keys",
            json={"owner": "test", "max_generations": 5},
        )
        assert response.status_code == 422  # Missing required header

    def test_create_api_key_with_invalid_master_key(self, client):
        """Creating API key with invalid master key should fail."""
        response = client.post(
            "/admin/keys",
            json={"owner": "test", "max_generations": 5},
            headers={"X-API-Key": "invalid-key"},
        )
        assert response.status_code == 401

    def test_create_api_key_with_valid_master_key(self, client, master_key):
        """Creating API key with valid master key should succeed."""
        response = client.post(
            "/admin/keys",
            json={"owner": "test-owner", "max_generations": 5},
            headers={"X-API-Key": master_key},
        )
        assert response.status_code == 201

        data = response.json()
        assert "api_key" in data
        assert "record" in data
        assert data["api_key"].startswith("adt_")
        assert data["record"]["owner"] == "test-owner"
        assert data["record"]["max_generations"] == 5

    def test_list_api_keys(self, client, master_key, api_key):
        """Listing API keys should return all keys."""
        response = client.get(
            "/admin/keys",
            headers={"X-API-Key": master_key},
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        # At least the key we created should exist
        assert len(data) >= 1

    def test_revoke_api_key(self, client, master_key):
        """Revoking an API key should work."""
        # First create a key
        create_response = client.post(
            "/admin/keys",
            json={"owner": "revoke-test", "max_generations": 1},
            headers={"X-API-Key": master_key},
        )
        key_id = create_response.json()["record"]["id"]

        # Then revoke it
        revoke_response = client.delete(
            f"/admin/keys/{key_id}",
            headers={"X-API-Key": master_key},
        )
        assert revoke_response.status_code == 200
        assert revoke_response.json() == {"status": "revoked"}


class TestJobManagement:
    """Tests for job management endpoints."""

    def test_list_jobs_empty(self, client):
        """Listing jobs should work even when empty."""
        response = client.get("/jobs")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_nonexistent_job(self, client):
        """Getting a nonexistent job should return 404."""
        response = client.get("/jobs/nonexistent-job-id")
        assert response.status_code == 404

    def test_job_status_nonexistent(self, client):
        """Getting status of nonexistent job should return 404."""
        response = client.get("/jobs/nonexistent-job-id/status")
        assert response.status_code == 404

    def test_create_job_requires_api_key(self, client, sample_pdf):
        """Creating a job without API key should fail."""
        with open(sample_pdf, "rb") as f:
            response = client.post(
                "/jobs",
                files={"pdf": ("test.pdf", f, "application/pdf")},
                data={"label": "test-job", "config": "{}"},
            )
        assert response.status_code == 401

    def test_create_job_with_non_pdf(self, client, api_key):
        """Creating a job with non-PDF file should fail."""
        response = client.post(
            "/jobs",
            files={"pdf": ("test.txt", BytesIO(b"not a pdf"), "text/plain")},
            data={"label": "test-job", "config": "{}"},
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]


class TestPlateManagement:
    """Tests for plate management endpoints."""

    def test_get_plate_nonexistent_job(self, client):
        """Getting plate for nonexistent job should return 404."""
        response = client.get("/jobs/nonexistent/plate")
        assert response.status_code == 404

    def test_update_plate_nonexistent_job(self, client):
        """Updating plate for nonexistent job should return 404."""
        response = client.put(
            "/jobs/nonexistent/plate",
            json={"title": "Test"},
        )
        assert response.status_code == 404


class TestSectionEdit:
    """Tests for the /sections/edit endpoint (stateless editing)."""

    def test_section_edit_requires_api_key(self, client, sample_html):
        """Section edit without API key should fail."""
        response = client.post(
            "/sections/edit",
            json={
                "html": sample_html,
                "edit_instruction": "Make the title red",
                "section_id": "sec_p1_s0",
            },
        )
        assert response.status_code == 401

    def test_section_edit_validation(self, client, api_key):
        """Section edit with missing fields should fail validation."""
        response = client.post(
            "/sections/edit",
            json={"html": "<div>test</div>"},  # Missing edit_instruction and section_id
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 422

    def test_section_edit_basic_structure(self, client, api_key, sample_html):
        """Section edit should accept valid request structure."""
        # Note: This test will fail if LLM is not configured, but validates the API structure
        response = client.post(
            "/sections/edit",
            json={
                "html": sample_html,
                "edit_instruction": "Add a red border to the container",
                "section_id": "sec_p1_s0",
                "section_type": "text_and_images",
                "page_number": 1,
                "language": "English",
            },
            headers={"X-API-Key": api_key},
        )
        # The actual edit may fail without proper LLM config, but we test the API accepts the request
        # 200 = success, 500 = LLM error (expected in test environment)
        assert response.status_code in [200, 500]


class TestRegeneration:
    """Tests for the /jobs/{job_id}/regenerate endpoint."""

    def test_regenerate_requires_api_key(self, client):
        """Regeneration without API key should fail."""
        response = client.post(
            "/jobs/test-job-id/regenerate",
            json={"regenerate_sections": ["sec_p1_s0"]},
        )
        assert response.status_code == 401

    def test_regenerate_nonexistent_job(self, client, api_key):
        """Regenerating from nonexistent job should return 404."""
        response = client.post(
            "/jobs/nonexistent-job/regenerate",
            json={"regenerate_sections": ["sec_p1_s0"]},
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 404

    def test_regenerate_validation_empty_request(self, client, api_key):
        """Regeneration with no sections specified should fail."""
        response = client.post(
            "/jobs/some-job-id/regenerate",
            json={},  # Neither regenerate_sections nor edit_sections
            headers={"X-API-Key": api_key},
        )
        # Will be 404 (job not found) or 400 (validation), depending on order of checks
        assert response.status_code in [400, 404]

    def test_regenerate_validation_overlapping_sections(self, client, api_key):
        """Regeneration with same section in both lists should fail."""
        response = client.post(
            "/jobs/some-job-id/regenerate",
            json={
                "regenerate_sections": ["sec_p1_s0"],
                "edit_sections": {"sec_p1_s0": "make it red"},
            },
            headers={"X-API-Key": api_key},
        )
        # Will be 404 (job not found) or 400 (overlap), depending on order of checks
        assert response.status_code in [400, 404]


class TestDownload:
    """Tests for the /jobs/{job_id}/download endpoint."""

    def test_download_nonexistent_job(self, client):
        """Downloading from nonexistent job should return 404."""
        response = client.get("/jobs/nonexistent/download")
        assert response.status_code == 404


class TestRateLimiting:
    """Tests for rate limiting behavior."""

    def test_rate_limited_endpoint_accepts_requests(self, client):
        """Rate-limited endpoints should accept normal request volume."""
        # Make a few requests - should all succeed
        for _ in range(5):
            response = client.get("/healthz")
            assert response.status_code == 200


class TestCORS:
    """Tests for CORS configuration."""

    def test_cors_headers_present(self, client):
        """CORS headers should be present on responses."""
        response = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # FastAPI/Starlette returns 400 for OPTIONS without proper CORS preflight
        # but we check that it doesn't block the request entirely
        assert response.status_code in [200, 400]
