"""
Integration tests for the presigned upload surface (/v1/uploads/*):
  - Upload intent creation (step 1)
  - Direct PUT to the upload_url + confirm (step 2)
  - Fresh download-URL retrieval
  - Validation + auth

All client uploads are presigned-only. On the local storage backend the
``upload_url`` points at the in-process PUT shim, so these tests can drive the
full intent -> PUT -> confirm flow end to end.

Requires: MongoDB on localhost:27017, Redis on localhost:6379
"""

from __future__ import annotations


import pytest
import pytest_asyncio
from httpx import AsyncClient


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestPresignedUploadFlow:
    """End-to-end presigned upload lifecycle."""

    @pytest_asyncio.fixture
    def _intent_payload(self):
        return {
            "file_name": "visitor_id_scan.pdf",
            "mime_type": "application/pdf",
            "size": 102400,
            "purpose": "id_document",
        }

    async def test_create_upload_intent(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _intent_payload: dict,
    ):
        resp = await integration_client.post(
            "/v1/uploads/intent",
            json=_intent_payload,
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()["data"]
        assert "object_key" in data
        assert "upload_url" in data
        assert data["method"] == "PUT"

    async def test_full_upload_then_confirm(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _intent_payload: dict,
    ):
        # Step 1: intent
        intent_resp = await integration_client.post(
            "/v1/uploads/intent", json=_intent_payload, headers=auth_headers
        )
        assert intent_resp.status_code in (200, 201), intent_resp.text
        intent = intent_resp.json()["data"]
        object_key = intent["object_key"]

        # Step 2a: PUT the bytes straight to the presigned URL. On the local
        # backend this is the in-process shim; on S3 it would be the bucket.
        payload = b"%PDF-1.4 fake pdf bytes" * 64
        put_resp = await integration_client.put(
            intent["upload_url"],
            content=payload,
            headers={"Content-Type": _intent_payload["mime_type"]},
        )
        assert put_resp.status_code in (200, 204), put_resp.text

        # Step 2b: confirm
        confirm_resp = await integration_client.post(
            "/v1/uploads/confirm",
            json={"object_key": object_key},
            headers=auth_headers,
        )
        assert confirm_resp.status_code in (200, 201), confirm_resp.text
        data = confirm_resp.json()["data"]
        assert data["object_key"] == object_key
        assert data["file_name"] == "visitor_id_scan.pdf"
        assert data["size"] == len(payload)
        assert "download_url" in data

    async def test_confirm_without_upload_conflicts(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _intent_payload: dict,
    ):
        """Confirming before any bytes were PUT must 409 (object absent)."""
        intent_resp = await integration_client.post(
            "/v1/uploads/intent", json=_intent_payload, headers=auth_headers
        )
        object_key = intent_resp.json()["data"]["object_key"]

        resp = await integration_client.post(
            "/v1/uploads/confirm",
            json={"object_key": object_key},
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text

    async def test_get_download_url(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _intent_payload: dict,
    ):
        intent = (
            await integration_client.post(
                "/v1/uploads/intent", json=_intent_payload, headers=auth_headers
            )
        ).json()["data"]
        object_key = intent["object_key"]
        await integration_client.put(
            intent["upload_url"],
            content=b"data" * 32,
            headers={"Content-Type": _intent_payload["mime_type"]},
        )
        await integration_client.post(
            "/v1/uploads/confirm",
            json={"object_key": object_key},
            headers=auth_headers,
        )

        resp = await integration_client.get(
            "/v1/uploads/url",
            params={"object_key": object_key},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["object_key"] == object_key
        assert "download_url" in data

    async def test_intent_validation(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        """Missing required fields should fail validation."""
        resp = await integration_client.post(
            "/v1/uploads/intent",
            json={"file_name": ""},  # empty name, missing fields
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_intent_unauthorized(self, integration_client: AsyncClient):
        resp = await integration_client.post(
            "/v1/uploads/intent",
            json={"file_name": "test.pdf", "mime_type": "application/pdf", "size": 100},
        )
        assert resp.status_code in (401, 403)
