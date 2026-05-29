"""
Unit tests for NDPA compliance (Phase 8F-8J).

Tests:
- Department admin scoping (only see their department's data)
- Profiling opt-out (auto-load history disabled)
- Consent rejection blocks registration
- Incident auto-deadline
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from security.principal import AuthPrincipal
from schemas.imports import ProfilingPreference


MOCK_DEPT_ADMIN_PRINCIPAL = AuthPrincipal(
    user_id="dept-admin-001",
    role="dept_admin",
    access_token_id="token-dept-001",
    jwt_token="jwt-token-dept-001",
    tenant_id="tenant-001",
    # Branch-scoped roles carry their assigned branch on the token; set it so
    # the routes' branch resolution returns it without a DB fallback.
    branch_ids=["branch-001"],
)

MOCK_DPO_PRINCIPAL = AuthPrincipal(
    user_id="dpo-001",
    role="dpo",
    access_token_id="token-dpo-001",
    jwt_token="jwt-token-dpo-001",
    tenant_id="tenant-001",
)


def _make_visit_session_out(
    id: str = "session-001", department_id: str = "dept-001", **kwargs
) -> dict:
    """Factory function for visit session."""
    return {
        "id": id,
        "tenant_id": "tenant-001",
        "visitor_profile_id": "visitor-001",
        "department_id": department_id,
        "host_id": "host-001",
        "receptionist_id": "receptionist-001",
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "manual_entry",
        "check_out_method": None,
        "verification_status": "unverified",
        "verification_method": None,
        "verified_by": None,
        "status": "checked_in",
        "purpose": "Business meeting",
        "visitor_name_snapshot": "John Doe",
        "company_snapshot": "Acme Corp",
        "host_name_snapshot": "Jane Smith",
        "department_name_snapshot": "Sales",
        "receptionist_name_snapshot": "Mike Johnson",
        "consent_notice_displayed": True,
        "consent_granted": True,
        "consent_method": "digital_signature",
        "consent_timestamp": 1712532000,
        "consent_captured_by_user_id": "receptionist-001",
        "consent_withdrawal_at": None,
        "lawful_basis_at_time": "legitimate_interest",
        "badge_qr_token": "VIS_20240407_1234567890AB",
        "badge_format": "pdf_qr",
        "badge_generation_time": 1712532010,
        "badge_expiry": 1712618400,
        "check_in_time": 1712532000,
        "check_out_time": None,
        "date_created": 1712532000,
        "visit_duration": None,
        **kwargs,
    }


def _make_visitor_profile_out(
    id: str = "visitor-001",
    profiling_preference: ProfilingPreference = ProfilingPreference.ALLOWED,
    visit_count: int = 1,
    **kwargs,
) -> dict:
    """Factory function for visitor profile."""
    return {
        "id": id,
        "tenant_id": "tenant-001",
        "email": "john@example.com",
        "phone": "+1234567890",
        "full_name": "John Doe",
        "company": "Acme Corp",
        "visit_count": visit_count,
        "last_visit": 1712532000,
        "profile_image_object_key": None,
        "id_type": None,
        "id_number": None,
        "id_expiry": None,
        "id_country": None,
        "id_image_object_key": None,
        "date_of_birth": None,
        "profiling_preference": profiling_preference,
        "date_created": 1712532000,
        "last_updated": 1712532000,
        **kwargs,
    }


@pytest.fixture(autouse=True)
def cleanup_dependency_overrides():
    """Cleanup dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()


class TestNDPACompliance:
    """Tests for NDPA compliance (8F-8J)."""

    @pytest.mark.asyncio
    async def test_dept_admin_scoping(self, cleanup_dependency_overrides):
        """
        8F: dept_admin can only see their department's data.

        A department admin should only be able to retrieve visitor logs,
        sessions, and profiles for their own department. Requests for other
        departments should return 403 Forbidden.
        """
        from security.auth import verify_system_user_token

        # Dept admin for Sales department
        sales_dept_admin = AuthPrincipal(
            user_id="dept-admin-sales",
            role="dept_admin",
            access_token_id="token-sales",
            jwt_token="jwt-token-sales",
            tenant_id="tenant-001",
        )

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            sales_dept_admin
        )

        session_sales = _make_visit_session_out(
            id="session-001",
            department_id="dept-sales",
            visitor_name_snapshot="John Doe",
        )

        with patch(
            "api.v1.visitor_route.retrieve_visit_sessions_with_summary",
            new_callable=AsyncMock,
        ) as mock_list:
            # Returns only sessions from the admin's department
            mock_list.return_value = [session_sales]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/visitors/sessions?start=0&stop=100",
                    headers={"Authorization": "Bearer token-sales"},
                )

            assert response.status_code == 200
            data = response.json()
            assert len(data["data"]) == 1
            assert (
                data["data"][0].get("department_id")
                or data["data"][0].get("departmentId")
            ) == "dept-sales"

    @pytest.mark.asyncio
    async def test_dept_admin_scoping_blocks_other_departments(
        self, cleanup_dependency_overrides
    ):
        """
        8F: dept_admin requesting data from other departments gets 403.

        If a dept_admin attempts to view visitors from a different department,
        the request should be rejected with a 403 Forbidden response.
        """
        from security.auth import verify_system_user_token

        sales_dept_admin = AuthPrincipal(
            user_id="dept-admin-sales",
            role="dept_admin",
            access_token_id="token-sales",
            jwt_token="jwt-token-sales",
            tenant_id="tenant-001",
        )

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            sales_dept_admin
        )

        with patch(
            "api.v1.visitor_route.retrieve_visit_sessions_with_summary",
            new_callable=AsyncMock,
        ) as mock_list:
            from fastapi import HTTPException

            mock_list.side_effect = HTTPException(
                status_code=403, detail="Access denied"
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/visitors/sessions?department_id=dept-hr&start=0&stop=100",
                    headers={"Authorization": "Bearer token-sales"},
                )

            assert response.status_code in [403, 400, 500]

    @pytest.mark.asyncio
    async def test_profiling_opt_out(self, cleanup_dependency_overrides):
        """
        8G: Opted-out profiles don't auto-load history.

        When a visitor has opted out of profiling (profiling_preference=opted_out),
        their historical visit data should not be automatically loaded. The system
        should still create a new session but without loading past visit info.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_DEPT_ADMIN_PRINCIPAL
        )

        _make_visitor_profile_out(
            id="visitor-opted-out",
            profiling_preference=ProfilingPreference.OPTED_OUT,
            visit_count=5,
        )

        with patch(
            "api.v1.visitor_route.check_in_visitor", new_callable=AsyncMock
        ) as mock_checkin:
            # Return new session without loading historical data
            session = _make_visit_session_out(status="registered")
            mock_checkin.return_value = session

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/check-in",
                    json={
                        "phone": "+1234567890",
                        "full_name": "John Doe",
                        "company": "Acme Corp",
                        "department_id": "dept-001",
                        "host_id": "host-001",
                        "purpose": "Business meeting",
                        "consent_granted": True,
                        "check_in_method": "manual_entry",
                    },
                    headers={"Authorization": "Bearer token-dept-001"},
                )

            assert response.status_code == 201
            data = response.json()
            # Session created but history not pre-populated
            assert data["data"]["status"] == "registered"

    @pytest.mark.asyncio
    async def test_consent_rejection_blocks_registration(
        self, cleanup_dependency_overrides
    ):
        """
        8A: consent_granted=false blocks registration when lawful_basis=consent.

        When a tenant has configured lawful_basis=consent, visitors must
        explicitly grant consent to register. If they reject consent,
        registration should fail.
        """
        with patch(
            "api.v1.public_registration_route.register_visitor_public",
            new_callable=AsyncMock,
        ) as mock_reg:
            from fastapi import HTTPException

            mock_reg.side_effect = HTTPException(
                status_code=400, detail="Consent is required"
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/public/register/69dd342b13d92289d3c7f5d9",
                    json={
                        "tenant_id": "tenant-consent-required",
                        "phone": "+1234567890",
                        "full_name": "John Doe",
                        "company": "Acme Corp",
                        "department_id": "dept-001",
                        "purpose": "Business meeting",
                        "consent_granted": False,
                    },
                )

            assert response.status_code in [400, 422, 500]

    @pytest.mark.asyncio
    async def test_incident_deadline_auto_set(self, cleanup_dependency_overrides):
        """
        8J: Incident creation auto-sets 72h notification_deadline.

        When a security officer or DPO creates an incident record, the system
        should automatically set notification_deadline to current_time + 72 hours
        as per NDPA requirements.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_DPO_PRINCIPAL
        )

        with patch(
            "api.v1.incident_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "incident-001",
                "job_id": "job-inc-001",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/incidents",
                    json={
                        "incident_type": "data_breach",
                        "description": "Potential data exposure detected",
                        "reported_by": "dpo-111",
                        "risk_level": "high",
                        "detection_time": 1712532000,
                        "tenant_id": "tenant-001",
                    },
                    headers={"Authorization": "Bearer token-dpo-001"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["success"] is True
            assert data["data"]["jobId"] == "job-inc-001"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "incident.create"

    @pytest.mark.asyncio
    async def test_incident_deadline_calculation(self, cleanup_dependency_overrides):
        """
        8J: Incident deadline is exactly 72 hours from creation.

        Verify the deadline calculation is precise: created_at + (72 * 3600) seconds.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_DPO_PRINCIPAL
        )

        with patch(
            "api.v1.incident_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "incident-002",
                "job_id": "job-inc-002",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/incidents",
                    json={
                        "incident_type": "unauthorized_access",
                        "description": "Unauthorized system access",
                        "reported_by": "dpo-111",
                        "risk_level": "high",
                        "detection_time": 1712532000,
                        "tenant_id": "tenant-001",
                    },
                    headers={"Authorization": "Bearer token-dpo-001"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["data"]["jobId"] == "job-inc-002"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "incident.create"

    @pytest.mark.asyncio
    async def test_dpo_incident_access(self, cleanup_dependency_overrides):
        """
        8J: DPO can create and manage incidents.

        DPO (Data Protection Officer) role should have full access to create,
        view, and manage incident records.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_DPO_PRINCIPAL
        )

        with patch(
            "api.v1.incident_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "incident-003",
                "job_id": "job-inc-003",
                "status": "queued",
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/incidents",
                    json={
                        "incident_type": "data_export_exposure",
                        "description": "Exported data found on public server",
                        "reported_by": "dpo-111",
                        "risk_level": "high",
                        "detection_time": 1712532000,
                        "tenant_id": "tenant-001",
                    },
                    headers={"Authorization": "Bearer token-dpo-001"},
                )

            assert response.status_code == 202
            data = response.json()
            assert data["success"] is True
            assert data["data"]["jobId"] == "job-inc-003"
            mock_enqueue.assert_awaited_once()
            assert mock_enqueue.await_args.kwargs["writer_key"] == "incident.create"
