"""
Integration tests for audit log retrieval and incident lifecycle.

Requires: MongoDB on localhost:27017, Redis on localhost:6379
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------


class TestAuditLogFlow:
    """Verify audit-log listing endpoint."""

    async def test_list_audit_logs_empty(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        """Empty DB should return an empty list, not an error."""
        resp = await integration_client.get("/v1/audit-logs/", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)

    async def test_list_audit_logs_unauthorized(self, integration_client: AsyncClient):
        """Request without auth should be rejected."""
        resp = await integration_client.get("/v1/audit-logs/")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Incident Lifecycle
# ---------------------------------------------------------------------------


class TestIncidentLifecycleFlow:
    """Full CRUD cycle for security / data-breach incidents."""

    @pytest_asyncio.fixture
    async def _incident_payload(self, seeded_tenant, seeded_system_user):
        user, _ = seeded_system_user
        return {
            "tenant_id": seeded_tenant.id,
            "reported_by": user.id,
            "incident_type": "data_breach",
            "description": "Integration-test incident — unauthorized DB export",
            "risk_level": "high",
            "data_affected": "visitor_profiles",
            "detection_time": int(time.time()) - 3600,
        }

    async def test_create_incident(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _incident_payload: dict,
    ):
        resp = await integration_client.post(
            "/v1/incidents/", json=_incident_payload, headers=auth_headers
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()["data"]
        assert data["description"] == _incident_payload["description"]
        assert data["status"] == "open"

    async def test_list_incidents(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _incident_payload: dict,
    ):
        # seed one
        await integration_client.post(
            "/v1/incidents/", json=_incident_payload, headers=auth_headers
        )
        resp = await integration_client.get("/v1/incidents/", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert isinstance(items, list)
        assert len(items) >= 1

    async def test_get_single_incident(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _incident_payload: dict,
    ):
        create_resp = await integration_client.post(
            "/v1/incidents/", json=_incident_payload, headers=auth_headers
        )
        incident_id = create_resp.json()["data"]["id"]

        resp = await integration_client.get(
            f"/v1/incidents/{incident_id}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == incident_id

    async def test_update_incident_status(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _incident_payload: dict,
    ):
        create_resp = await integration_client.post(
            "/v1/incidents/", json=_incident_payload, headers=auth_headers
        )
        incident_id = create_resp.json()["data"]["id"]

        # transition: open → investigating → contained → closed
        for next_status in ("investigating", "contained", "closed"):
            patch_payload: dict = {"status": next_status}
            if next_status == "closed":
                patch_payload["resolved_at"] = int(time.time())
                patch_payload["mitigation_steps"] = "Revoked leaked credentials"

            resp = await integration_client.patch(
                f"/v1/incidents/{incident_id}",
                json=patch_payload,
                headers=auth_headers,
            )
            assert resp.status_code == 200, (
                f"Failed transitioning to {next_status}: {resp.text}"
            )
            assert resp.json()["data"]["status"] == next_status

    async def test_update_ndpc_notification(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _incident_payload: dict,
    ):
        create_resp = await integration_client.post(
            "/v1/incidents/", json=_incident_payload, headers=auth_headers
        )
        incident_id = create_resp.json()["data"]["id"]

        resp = await integration_client.patch(
            f"/v1/incidents/{incident_id}",
            json={
                "ndpc_notified": True,
                "ndpc_notified_at": int(time.time()),
                "status": "reported_to_ndpc",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ndpc_notified"] is True
        assert data["status"] == "reported_to_ndpc"

    async def test_get_nonexistent_incident(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/incidents/000000000000000000000000", headers=auth_headers
        )
        assert resp.status_code in (404, 200)  # may return null data or 404
