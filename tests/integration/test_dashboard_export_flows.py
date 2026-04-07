"""
Integration tests for dashboard endpoints:
  - Stats aggregation
  - Visitor log with filters
  - Active visitors listing
  - CSV / XLSX export

Requires: MongoDB on localhost:27017, Redis on localhost:6379
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient

from schemas.visitor_profile_schema import VisitorProfileCreate
from schemas.visit_session_schema import VisitSessionCreate
from schemas.department_schema import DepartmentCreate
from repositories.visitor_profile_repo import create_visitor_profile
from repositories.visit_session_repo import create_visit_session
from repositories.department_repo import create_department


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestDashboardStats:
    """Dashboard stats endpoint."""

    async def test_stats_empty_db(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/dashboard/stats", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    async def test_stats_unauthorized(self, integration_client: AsyncClient):
        resp = await integration_client.get("/v1/dashboard/stats")
        assert resp.status_code in (401, 403)


class TestDashboardVisitorLog:
    """Dashboard visitor log (historical sessions)."""

    @pytest_asyncio.fixture
    async def _seed_sessions(self, mongo_db, seeded_tenant, seeded_system_user):
        """Seed a department, visitor profile, and 3 visit sessions."""
        user, _ = seeded_system_user
        dept = await create_department(
            DepartmentCreate(
                tenant_id=seeded_tenant.id,
                name=f"Lobby_{int(time.time())}",
            )
        )
        profile = await create_visitor_profile(
            VisitorProfileCreate(
                tenant_id=seeded_tenant.id,
                full_name="Dashboard Test Visitor",
                email_address="dashboard@example.com",
                phone="+2348199999999",
            )
        )
        sessions = []
        for i in range(3):
            ts = int(time.time()) - (i * 3600)
            session = await create_visit_session(
                VisitSessionCreate(
                    tenant_id=seeded_tenant.id,
                    visitor_profile_id=profile.id,
                    department_id=dept.id,
                    host_name=user.full_name,
                    purpose="Dashboard integration test",
                    check_in_method="manual_entry",
                    checked_in_at=ts,
                    checked_in_by=user.id,
                )
            )
            sessions.append(session)
        return sessions

    async def test_visitor_log(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _seed_sessions,
    ):
        resp = await integration_client.get(
            "/v1/dashboard/visitors", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) >= 3

    async def test_active_visitors(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _seed_sessions,
    ):
        resp = await integration_client.get(
            "/v1/dashboard/visitors/active", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        # All 3 sessions have no checkout, so they should be active
        assert len(data) >= 3


class TestDashboardExport:
    """CSV / XLSX export endpoint."""

    async def test_export_csv(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/dashboard/export",
            params={"format": "csv"},
            headers=auth_headers,
        )
        # Export may return 200 with file content or a download URL
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        # Either JSON envelope with URL, or direct file download
        if "application/json" in content_type:
            body = resp.json()
            assert body["success"] is True
        else:
            # Direct file download (text/csv or application/octet-stream)
            assert len(resp.content) > 0

    async def test_export_xlsx(
        self, integration_client: AsyncClient, auth_headers: dict
    ):
        resp = await integration_client.get(
            "/v1/dashboard/export",
            params={"format": "xlsx"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            body = resp.json()
            assert body["success"] is True
        else:
            assert len(resp.content) > 0

    async def test_export_unauthorized(self, integration_client: AsyncClient):
        resp = await integration_client.get(
            "/v1/dashboard/export", params={"format": "csv"}
        )
        assert resp.status_code in (401, 403)
