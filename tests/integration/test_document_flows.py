"""
Integration tests for document management endpoints:
  - Upload intent creation
  - Upload completion
  - Document retrieval
  - Document deletion

Requires: MongoDB on localhost:27017, Redis on localhost:6379
"""
from __future__ import annotations


import pytest
import pytest_asyncio
from httpx import AsyncClient


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestDocumentUploadFlow:
    """End-to-end document upload lifecycle."""

    @pytest_asyncio.fixture
    def _upload_intent_payload(self):
        return {
            "file_name": "visitor_id_scan.pdf",
            "mime_type": "application/pdf",
            "size": 102400,
        }

    async def test_create_upload_intent(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _upload_intent_payload: dict,
    ):
        resp = await integration_client.post(
            "/v1/documents/upload-intents",
            json=_upload_intent_payload,
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()["data"]
        assert "object_key" in data
        assert "upload_url" in data
        assert data["method"] in ("PUT", "POST")

    async def test_complete_upload(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _upload_intent_payload: dict,
    ):
        # Step 1: Create intent
        intent_resp = await integration_client.post(
            "/v1/documents/upload-intents",
            json=_upload_intent_payload,
            headers=auth_headers,
        )
        assert intent_resp.status_code in (200, 201)
        intent_data = intent_resp.json()["data"]
        object_key = intent_data["object_key"]

        # Step 2: Complete upload (simulate: the real file upload goes to S3/local)
        complete_payload = {
            "object_key": object_key,
            "file_name": _upload_intent_payload["file_name"],
            "mime_type": _upload_intent_payload["mime_type"],
            "size": _upload_intent_payload["size"],
            "checksum": "sha256:abc123def456",
        }
        resp = await integration_client.post(
            "/v1/documents/complete",
            json=complete_payload,
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()["data"]
        assert data["file_name"] == "visitor_id_scan.pdf"
        assert data["status"] == "ready"
        return data

    async def test_get_document(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _upload_intent_payload: dict,
    ):
        # Create + complete a document first
        intent_resp = await integration_client.post(
            "/v1/documents/upload-intents",
            json=_upload_intent_payload,
            headers=auth_headers,
        )
        object_key = intent_resp.json()["data"]["object_key"]

        complete_resp = await integration_client.post(
            "/v1/documents/complete",
            json={
                "object_key": object_key,
                "file_name": _upload_intent_payload["file_name"],
                "mime_type": _upload_intent_payload["mime_type"],
                "size": _upload_intent_payload["size"],
            },
            headers=auth_headers,
        )
        doc_id = complete_resp.json()["data"]["id"]

        # Fetch the document
        resp = await integration_client.get(
            f"/v1/documents/{doc_id}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == doc_id

    async def test_delete_document(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _upload_intent_payload: dict,
    ):
        # Create + complete
        intent_resp = await integration_client.post(
            "/v1/documents/upload-intents",
            json=_upload_intent_payload,
            headers=auth_headers,
        )
        object_key = intent_resp.json()["data"]["object_key"]

        complete_resp = await integration_client.post(
            "/v1/documents/complete",
            json={
                "object_key": object_key,
                "file_name": _upload_intent_payload["file_name"],
                "mime_type": _upload_intent_payload["mime_type"],
                "size": _upload_intent_payload["size"],
            },
            headers=auth_headers,
        )
        doc_id = complete_resp.json()["data"]["id"]

        # Delete
        resp = await integration_client.delete(
            f"/v1/documents/{doc_id}", headers=auth_headers
        )
        assert resp.status_code == 200

        # Verify gone
        get_resp = await integration_client.get(
            f"/v1/documents/{doc_id}", headers=auth_headers
        )
        assert get_resp.status_code in (200, 404)
        if get_resp.status_code == 200:
            assert get_resp.json()["data"] is None

    async def test_get_nonexistent_document(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/documents/000000000000000000000000", headers=auth_headers
        )
        assert resp.status_code in (200, 404)

    async def test_upload_intent_validation(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        """Missing required fields should fail validation."""
        resp = await integration_client.post(
            "/v1/documents/upload-intents",
            json={"file_name": ""},  # empty name, missing fields
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_upload_intent_unauthorized(
        self, integration_client: AsyncClient
    ):
        resp = await integration_client.post(
            "/v1/documents/upload-intents",
            json={"file_name": "test.pdf", "mime_type": "application/pdf", "size": 100},
        )
        assert resp.status_code in (401, 403)
