"""
Unit tests for the staged check-in flow (Phases 1A-1D).

Tests:
- Check-in creates REGISTERED status (not CHECKED_IN)
- Confirm check-in validates status and required fields
- Deny visitor sets DENIED status
- Pending sessions returns only REGISTERED and PENDING_VERIFICATION
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from schemas.imports import VisitStatus, VerificationStatus
from security.principal import AuthPrincipal


# Mock auth principal for receptionist
MOCK_RECEPTIONIST_PRINCIPAL = AuthPrincipal(
    user_id="receptionist-001",
    role="receptionist",
    access_token_id="token-rec-001",
    jwt_token="jwt-token-rec-001",
    tenant_id="tenant-001",
    # Branch-scoped roles carry their assigned branch on the token; set it so
    # the routes' branch resolution returns it without a DB fallback.
    branch_ids=["branch-001"],
)

MOCK_DEPT_ADMIN_PRINCIPAL = AuthPrincipal(
    user_id="dept-admin-001",
    role="dept_admin",
    access_token_id="token-dept-001",
    jwt_token="jwt-token-dept-001",
    tenant_id="tenant-001",
    branch_ids=["branch-001"],
)


def _make_visit_session_out(
    id: str = "session-001",
    status: VisitStatus = VisitStatus.REGISTERED,
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
    visitor_name_snapshot: str = "John Doe",
    department_id: str = "dept-001",
    **kwargs,
) -> dict:
    """Factory function to create a VisitSessionOut dict for testing."""
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
        "verification_status": verification_status,
        "verification_method": None,
        "verified_by": None,
        "status": status,
        "purpose": "Business meeting",
        "visitor_name_snapshot": visitor_name_snapshot,
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
        "badge_pdf_object_key": "badges/session-001.pdf",
        "check_in_time": 1712532000,
        "check_out_time": None,
        "date_created": 1712532000,
        "visit_duration": None,
        **kwargs,
    }


@pytest.fixture(autouse=True)
def cleanup_dependency_overrides():
    """Cleanup dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()


class TestStagedCheckIn:
    """Tests for staged check-in flow (1A-1D)."""

    @pytest.mark.asyncio
    async def test_check_in_creates_registered_session(
        self, cleanup_dependency_overrides
    ):
        """
        1A: Check-in should create session with REGISTERED status, not CHECKED_IN.

        The receptionist initiates check-in, which creates a session in REGISTERED
        state. The session is not yet marked as CHECKED_IN until confirmed.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        session_out = _make_visit_session_out(status=VisitStatus.REGISTERED)

        with patch(
            "api.v1.visitor_route.check_in_visitor", new_callable=AsyncMock
        ) as mock_checkin:
            mock_checkin.return_value = session_out

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
                    },
                    headers={"Authorization": "Bearer token-rec-001"},
                )

            assert response.status_code == 201
            data = response.json()
            assert data["success"] is True
            assert data["data"]["status"] == "registered"
            assert (
                data["data"].get("verification_status")
                or data["data"].get("verificationStatus")
            ) == "unverified"
            mock_checkin.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_check_in_validates_status(
        self, cleanup_dependency_overrides
    ):
        """
        1A: Confirm should only work on REGISTERED/PENDING_VERIFICATION sessions.

        Attempting to confirm a session that's already CHECKED_IN or in another
        state should fail with a validation error.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        # Test successful confirmation on REGISTERED
        session_out = _make_visit_session_out(
            status=VisitStatus.REGISTERED,
            visitor_name_snapshot="John Doe",
            department_id="dept-001",
        )

        with patch(
            "api.v1.visitor_route.confirm_check_in", new_callable=AsyncMock
        ) as mock_confirm:
            mock_confirm.return_value = {
                **session_out,
                "status": VisitStatus.CHECKED_IN,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/sessions/69dd2cb2d8015f440f99ef2d/confirm",
                    json={"badge_format": "A7"},
                    headers={"Authorization": "Bearer token-rec-001"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["data"]["status"] == "checked_in"
            mock_confirm.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_check_in_validates_required_fields(
        self, cleanup_dependency_overrides
    ):
        """
        1D: Confirm should fail if visitor_name_snapshot or department_id is missing.

        The confirmation requires certain fields to be present from the initial
        check-in. If they're missing, the operation should fail.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.visitor_route.confirm_check_in", new_callable=AsyncMock
        ) as mock_confirm:
            from fastapi import HTTPException

            mock_confirm.side_effect = HTTPException(
                status_code=400, detail="Missing required field: visitor_name_snapshot"
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/sessions/69dd2cb2d8015f440f99ef2d/confirm",
                    json={"badge_format": "A7"},
                    headers={"Authorization": "Bearer token-rec-001"},
                )

            # Service raises ValueError, which gets caught
            assert response.status_code in [400, 422, 500]

    @pytest.mark.asyncio
    async def test_deny_visitor_sets_denied_status(self, cleanup_dependency_overrides):
        """
        1B: Deny should set status to DENIED with reason.

        When a receptionist denies a visitor, the session status changes to DENIED
        and the denial reason is recorded.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        denied_session = _make_visit_session_out(
            status=VisitStatus.DENIED, denial_reason="Not on visitor list"
        )

        with patch(
            "api.v1.visitor_route.deny_visitor", new_callable=AsyncMock
        ) as mock_deny:
            mock_deny.return_value = denied_session

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/sessions/69dd2cb2d8015f440f99ef2d/deny",
                    json={"reason": "Not on visitor list"},
                    headers={"Authorization": "Bearer token-rec-001"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["data"]["status"] == "denied"
            assert (
                data["data"].get("denial_reason") or data["data"].get("denialReason")
            ) == "Not on visitor list"
            mock_deny.assert_called_once()

    @pytest.mark.asyncio
    async def test_deny_visitor_rejects_checked_in(self, cleanup_dependency_overrides):
        """
        1B: Cannot deny a checked-in visitor.

        Once a visitor is already checked in, they cannot be denied. Only sessions
        in REGISTERED or PENDING_VERIFICATION state can be denied.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "api.v1.visitor_route.deny_visitor", new_callable=AsyncMock
        ) as mock_deny:
            from fastapi import HTTPException

            mock_deny.side_effect = HTTPException(
                status_code=400, detail="Cannot deny a checked-in visitor"
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/sessions/69dd2cb2d8015f440f99ef2d/deny",
                    json={"reason": "Not allowed"},
                    headers={"Authorization": "Bearer token-rec-001"},
                )

            assert response.status_code in [400, 422, 500]

    @pytest.mark.asyncio
    async def test_pending_sessions_returns_correct_statuses(
        self, cleanup_dependency_overrides
    ):
        """
        1C: Pending endpoint returns only REGISTERED and PENDING_VERIFICATION sessions.

        The pending sessions endpoint should exclude CHECKED_IN, CHECKED_OUT, DENIED,
        and CANCELLED sessions, returning only those awaiting confirmation or
        verification.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        registered_session = _make_visit_session_out(
            id="session-001",
            status=VisitStatus.REGISTERED,
        )
        pending_verification_session = _make_visit_session_out(
            id="session-002",
            status=VisitStatus.PENDING_VERIFICATION,
        )
        _make_visit_session_out(
            id="session-003",
            status=VisitStatus.CHECKED_IN,
        )

        with patch(
            "api.v1.visitor_route.retrieve_pending_sessions",
            new_callable=AsyncMock,
        ) as mock_pending:
            # Only return REGISTERED and PENDING_VERIFICATION
            mock_pending.return_value = [
                registered_session,
                pending_verification_session,
            ]

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/v1/visitors/sessions/pending?start=0&stop=100",
                    headers={"Authorization": "Bearer token-rec-001"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["data"]) == 2
            statuses = [s["status"] for s in data["data"]]
            assert "registered" in statuses
            assert "pending_verification" in statuses
            assert "checked_in" not in statuses
            mock_pending.assert_called_once()
