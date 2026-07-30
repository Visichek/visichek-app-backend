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

from tests.integration.conftest import complete_write, expect_write_failure


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _find(items: list, resource_id: str) -> dict:
    match = next((x for x in items if x.get("id") == resource_id), None)
    assert match is not None, f"resource {resource_id} not found in {items}"
    return match


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
        # Departments are branch-scoped: create resolves the HQ branch and
        # 409s when the tenant has none. Real tenants always have one
        # (bootstrap / boot-time backfill provisions it); the raw seeded
        # tenant fixture bypasses that, so provision HQ here.
        from services.branch_service import ensure_default_branch

        await ensure_default_branch(
            tenant_id=seeded_tenant.id or "",
            company_name=seeded_tenant.company_name,
        )
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
        job = await complete_write(integration_client, resp, auth_headers)
        listing = await integration_client.get(
            "/v1/super-admin/departments", headers=auth_headers
        )
        assert listing.status_code == 200, listing.text
        data = _find(listing.json()["data"], job["resource_id"])
        assert _dept_payload["name"] in data.get("name", "")

    async def test_create_then_list_departments(
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
        await complete_write(integration_client, resp, auth_headers)
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
        job = await complete_write(integration_client, resp, auth_headers)
        listing = await integration_client.get(
            "/v1/super-admin/admins", headers=auth_headers
        )
        assert listing.status_code == 200, listing.text
        data = _find(listing.json()["data"], job["resource_id"])
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
        await complete_write(integration_client, resp1, auth_headers)

        # duplicate invite — worker rejects on email-uniqueness
        payload["full_name"] = "Second Admin"
        resp2 = await integration_client.post(
            "/v1/super-admin/admins/invite", json=payload, headers=auth_headers
        )
        await expect_write_failure(integration_client, resp2, auth_headers)
