"""
Integration tests for super-admin endpoints:
  - Company-wide analytics
  - Cross-tenant department management
  - System-user (admin) listing and invitation

Requires: MongoDB on localhost:27017, Redis on localhost:6379
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient



pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestSuperAdminAnalytics:
    """Super-admin analytics / dashboard aggregation."""

    async def test_get_analytics(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/super-admin/analytics", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        # data may be a dict with aggregation keys
        assert body["data"] is not None

    async def test_analytics_unauthorized(self, integration_client: AsyncClient):
        resp = await integration_client.get("/v1/super-admin/analytics")
        assert resp.status_code in (401, 403)


class TestSuperAdminDepartments:
    """Department management via super-admin endpoints."""

    @pytest_asyncio.fixture
    async def _dept_payload(self, seeded_tenant, seeded_system_user):
        user, _ = seeded_system_user
        return {
            "tenant_id": seeded_tenant.id,
            "name": f"Engineering_{int(time.time())}",
            "description": "Software engineering department",
            "created_by": user.id,
        }

    async def test_list_all_departments_empty(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/super-admin/departments", headers=auth_headers
        )
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    async def test_create_department_via_super_admin(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _dept_payload: dict,
    ):
        resp = await integration_client.post(
            "/v1/super-admin/departments",
            json=_dept_payload,
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()["data"]
        assert _dept_payload["name"] in data.get("name", "")

    async def test_create_then_list_departments(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _dept_payload: dict,
    ):
        await integration_client.post(
            "/v1/super-admin/departments",
            json=_dept_payload,
            headers=auth_headers,
        )
        resp = await integration_client.get(
            "/v1/super-admin/departments", headers=auth_headers
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 1


class TestSuperAdminUserManagement:
    """Admin listing and invitation."""

    async def test_list_all_admins(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/super-admin/admins", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        # At least the seeded super_admin should appear
        assert len(data) >= 1

    async def test_invite_admin(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        seeded_tenant,
    ):
        invite_payload = {
            "tenant_id": seeded_tenant.id,
            "full_name": "Invited Admin",
            "email": f"invited_{int(time.time())}@test.example.com",
            "role": "dept_admin",
            "account_status": "ACTIVE",
            "password_hash": "InvitedPass123!",
        }
        resp = await integration_client.post(
            "/v1/super-admin/admins/invite",
            json=invite_payload,
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        data = resp.json()["data"]
        assert data["full_name"] == "Invited Admin"
        assert data["role"] == "dept_admin"

    async def test_invite_duplicate_email(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        seeded_tenant,
    ):
        email = f"dup_{int(time.time())}@test.example.com"
        payload = {
            "tenant_id": seeded_tenant.id,
            "full_name": "First Admin",
            "email": email,
            "role": "dept_admin",
            "account_status": "ACTIVE",
            "password_hash": "Pass123!",
        }
        # first invite
        resp1 = await integration_client.post(
            "/v1/super-admin/admins/invite", json=payload, headers=auth_headers
        )
        assert resp1.status_code in (200, 201)

        # duplicate invite should fail
        payload["full_name"] = "Second Admin"
        resp2 = await integration_client.post(
            "/v1/super-admin/admins/invite", json=payload, headers=auth_headers
        )
        assert resp2.status_code in (409, 400, 422), (
            f"Expected duplicate rejection, got {resp2.status_code}: {resp2.text}"
        )
