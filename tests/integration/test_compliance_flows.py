"""
Integration tests for compliance endpoints:
  - Data Processing Register (DPR)
  - Sub-Processor management
  - Retention policies
  - Deletion logs

Requires: MongoDB on localhost:27017, Redis on localhost:6379
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Data Processing Register (DPR)
# ---------------------------------------------------------------------------

class TestDPRFlow:
    """DPR register CRUD lifecycle."""

    @pytest_asyncio.fixture
    async def _dpr_payload(self, seeded_tenant):
        return {
            "tenant_id": seeded_tenant.id,
            "field_name": "visitor_phone",
            "purpose": "Emergency contact during visit",
            "lawful_basis": "legitimate_interest",
            "retention_period": 365,
            "crosses_borders": False,
        }

    async def test_get_register_empty(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/compliance/register", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)

    async def test_add_register_entry(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _dpr_payload: dict,
    ):
        resp = await integration_client.post(
            "/v1/compliance/register", json=_dpr_payload, headers=auth_headers
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()["data"]
        assert data["field_name"] == "visitor_phone"
        assert data["purpose"] == "Emergency contact during visit"

    async def test_add_multiple_entries_then_list(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        seeded_tenant,
    ):
        entries = [
            {
                "tenant_id": seeded_tenant.id,
                "field_name": f"field_{i}",
                "purpose": f"Purpose {i}",
                "lawful_basis": "consent",
                "retention_period": 180 * (i + 1),
                "crosses_borders": i % 2 == 0,
            }
            for i in range(3)
        ]
        for entry in entries:
            resp = await integration_client.post(
                "/v1/compliance/register", json=entry, headers=auth_headers
            )
            assert resp.status_code in (200, 201)

        resp = await integration_client.get(
            "/v1/compliance/register", headers=auth_headers
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 3


# ---------------------------------------------------------------------------
# Sub-Processors
# ---------------------------------------------------------------------------

class TestSubProcessorFlow:
    """Full CRUD lifecycle for sub-processor records."""

    @pytest_asyncio.fixture
    async def _sp_payload(self, seeded_tenant):
        return {
            "tenant_id": seeded_tenant.id,
            "provider": "AWS S3",
            "purpose": "Document storage for visitor ID scans",
            "jurisdiction": "EU (Ireland)",
            "dpa_signed": True,
            "uses_data_for_training": False,
        }

    async def test_create_sub_processor(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _sp_payload: dict,
    ):
        resp = await integration_client.post(
            "/v1/sub-processors/", json=_sp_payload, headers=auth_headers
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()["data"]
        assert data["provider"] == "AWS S3"
        assert data["dpa_signed"] is True

    async def test_list_sub_processors(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _sp_payload: dict,
    ):
        # seed one
        await integration_client.post(
            "/v1/sub-processors/", json=_sp_payload, headers=auth_headers
        )
        resp = await integration_client.get(
            "/v1/sub-processors/", headers=auth_headers
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 1

    async def test_update_sub_processor(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _sp_payload: dict,
    ):
        create_resp = await integration_client.post(
            "/v1/sub-processors/", json=_sp_payload, headers=auth_headers
        )
        sp_id = create_resp.json()["data"]["id"]

        resp = await integration_client.patch(
            f"/v1/sub-processors/{sp_id}",
            json={"jurisdiction": "US (Virginia)", "uses_data_for_training": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["jurisdiction"] == "US (Virginia)"
        assert data["uses_data_for_training"] is True

    async def test_delete_sub_processor(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _sp_payload: dict,
    ):
        create_resp = await integration_client.post(
            "/v1/sub-processors/", json=_sp_payload, headers=auth_headers
        )
        sp_id = create_resp.json()["data"]["id"]

        resp = await integration_client.delete(
            f"/v1/sub-processors/{sp_id}", headers=auth_headers
        )
        assert resp.status_code == 200

        # verify it's gone
        list_resp = await integration_client.get(
            "/v1/sub-processors/", headers=auth_headers
        )
        ids = [item["id"] for item in list_resp.json()["data"]]
        assert sp_id not in ids


# ---------------------------------------------------------------------------
# Retention Policies
# ---------------------------------------------------------------------------

class TestRetentionPolicyFlow:
    """CRUD lifecycle for data retention policies."""

    @pytest_asyncio.fixture
    async def _policy_payload(self, seeded_tenant):
        return {
            "tenant_id": seeded_tenant.id,
            "scope": "visit_sessions",
            "retention_days": 365,
            "action": "anonymise",
        }

    async def test_create_retention_policy(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _policy_payload: dict,
    ):
        resp = await integration_client.post(
            "/v1/retention-policies/", json=_policy_payload, headers=auth_headers
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()["data"]
        assert data["scope"] == "visit_sessions"
        assert data["retention_days"] == 365

    async def test_list_retention_policies(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _policy_payload: dict,
    ):
        await integration_client.post(
            "/v1/retention-policies/", json=_policy_payload, headers=auth_headers
        )
        resp = await integration_client.get(
            "/v1/retention-policies/", headers=auth_headers
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 1

    async def test_update_retention_policy(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _policy_payload: dict,
    ):
        create_resp = await integration_client.post(
            "/v1/retention-policies/", json=_policy_payload, headers=auth_headers
        )
        policy_id = create_resp.json()["data"]["id"]

        resp = await integration_client.patch(
            f"/v1/retention-policies/{policy_id}",
            json={"retention_days": 730, "action": "delete"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["retention_days"] == 730
        assert data["action"] == "delete"

    async def test_create_multiple_scope_policies(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        seeded_tenant,
    ):
        scopes = ["visit_sessions", "id_images", "visitor_profiles"]
        for scope in scopes:
            resp = await integration_client.post(
                "/v1/retention-policies/",
                json={
                    "tenant_id": seeded_tenant.id,
                    "scope": scope,
                    "retention_days": 180,
                    "action": "anonymise",
                },
                headers=auth_headers,
            )
            assert resp.status_code in (200, 201)

        resp = await integration_client.get(
            "/v1/retention-policies/", headers=auth_headers
        )
        assert len(resp.json()["data"]) >= 3


# ---------------------------------------------------------------------------
# Deletion Logs
# ---------------------------------------------------------------------------

class TestDeletionLogFlow:
    """Verify deletion log listing endpoint."""

    async def test_list_deletion_logs_empty(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/compliance/deletion-logs", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
