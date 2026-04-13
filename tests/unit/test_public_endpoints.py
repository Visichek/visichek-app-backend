"""
Unit tests for public endpoints (Phases 4B-4C, 8A, 8D-8E).

Tests:
- Public registration doesn't require auth token
- Public registration creates REGISTERED session
- Public checkout via badge QR
- Public registration consent enforcement
- DSR submission creates record
- Consent withdrawal
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from schemas.imports import VisitStatus, DSRType, DSRStatus


def _make_visit_session_out(
    id: str = "session-001",
    status: VisitStatus = VisitStatus.REGISTERED,
    consent_granted: bool | None = None,
    **kwargs,
) -> dict:
    """Factory function for visit session."""
    return {
        "id": id,
        "tenant_id": "tenant-001",
        "visitor_profile_id": "visitor-001",
        "department_id": "dept-001",
        "host_id": "host-001",
        "receptionist_id": None,
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "qr_registration",
        "check_out_method": None,
        "verification_status": "unverified",
        "verification_method": None,
        "verified_by": None,
        "status": status,
        "purpose": "Business meeting",
        "visitor_name_snapshot": "John Doe",
        "company_snapshot": "Acme Corp",
        "host_name_snapshot": "Jane Smith",
        "department_name_snapshot": "Sales",
        "receptionist_name_snapshot": None,
        "consent_notice_displayed": True,
        "consent_granted": consent_granted,
        "consent_method": "qr_registration" if consent_granted is not None else None,
        "consent_timestamp": 1712532000 if consent_granted is not None else None,
        "consent_captured_by_user_id": None,
        "consent_withdrawal_at": None,
        "lawful_basis_at_time": "legitimate_interest",
        "badge_qr_token": "VIS_20240407_1234567890AB",
        "badge_format": "pdf_qr",
        "badge_generation_time": 1712532010,
        "badge_expiry": 1712618400,
        "badge_pdf_object_key": "badges/session-001.pdf",
        "check_in_time": None,
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


class TestPublicEndpoints:
    """Tests for public endpoints (4B-4C, 8A, 8D-8E)."""

    @pytest.mark.asyncio
    async def test_public_registration_no_auth_required(
        self, cleanup_dependency_overrides
    ):
        """
        4B: Public registration doesn't require auth token.

        The public registration endpoint should accept unauthenticated requests
        without requiring a Bearer token. This allows visitors to self-register
        without pre-authentication.
        """
        session_out = _make_visit_session_out(
            status=VisitStatus.REGISTERED,
            consent_granted=True,
        )

        with patch(
            "services.visit_session_service.resume_draft_registration",
            new_callable=AsyncMock,
        ) as mock_reg:
            mock_reg.return_value = session_out

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # No Authorization header
                response = await client.post(
                    "/v1/visitors/public/register",
                    json={
                        "tenant_id": "tenant-001",
                        "phone": "+1234567890",
                        "full_name": "John Doe",
                        "company": "Acme Corp",
                        "department_id": "dept-001",
                        "purpose": "Business meeting",
                        "consent_granted": True,
                    },
                )

            assert response.status_code == 201
            data = response.json()
            assert data["success"] is True
            assert data["data"]["status"] == "registered"

    @pytest.mark.asyncio
    async def test_public_registration_creates_registered_session(
        self, cleanup_dependency_overrides
    ):
        """
        4B: Public registration creates REGISTERED session.

        When a visitor self-registers via the public endpoint, they should get
        back a session in REGISTERED status (not CHECKED_IN), which they can
        then proceed to check in at the reception desk.
        """
        session_out = _make_visit_session_out(
            status=VisitStatus.REGISTERED,
            consent_granted=True,
        )

        with patch(
            "services.visit_session_service.resume_draft_registration",
            new_callable=AsyncMock,
        ) as mock_reg:
            mock_reg.return_value = session_out

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/public/register",
                    json={
                        "tenant_id": "tenant-001",
                        "phone": "+1234567890",
                        "full_name": "John Doe",
                        "company": "Acme Corp",
                        "department_id": "dept-001",
                        "purpose": "Business meeting",
                        "consent_granted": True,
                    },
                )

            assert response.status_code == 201
            data = response.json()
            assert data["data"]["status"] == "registered"
            assert data["data"]["verification_status"] == "unverified"

    @pytest.mark.asyncio
    async def test_public_checkout_via_badge_qr(self, cleanup_dependency_overrides):
        """
        4C: Public checkout validates QR token and checks out visitor.

        When a visitor scans their badge QR code at the exit, the public endpoint
        should validate the QR token, find the corresponding session, and mark
        it as CHECKED_OUT.
        """
        checked_out_session = _make_visit_session_out(
            id="session-001",
            status=VisitStatus.CHECKED_OUT,
            check_in_time=1712532000,
            check_out_time=1712535600,
        )

        with patch(
            "services.visit_session_service.check_out_visitor", new_callable=AsyncMock
        ) as mock_checkout:
            mock_checkout.return_value = checked_out_session

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # No Authorization header for public checkout
                response = await client.post(
                    "/v1/visitors/public/checkout",
                    json={
                        "badge_qr_token": "VIS_20240407_1234567890AB",
                    },
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["data"]["status"] == "checked_out"
            assert data["data"]["check_out_time"] == 1712535600

    @pytest.mark.asyncio
    async def test_public_checkout_invalid_qr_token(self, cleanup_dependency_overrides):
        """
        4C: Public checkout rejects invalid QR token.

        If the QR token is invalid or expired, the checkout should fail
        with an appropriate error message.
        """
        with patch(
            "services.visit_session_service.check_out_visitor", new_callable=AsyncMock
        ) as mock_checkout:
            mock_checkout.side_effect = ValueError("Invalid or expired QR token")

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/public/checkout",
                    json={
                        "badge_qr_token": "INVALID_TOKEN",
                    },
                )

            assert response.status_code in [400, 404]

    @pytest.mark.asyncio
    async def test_public_registration_consent_enforcement_required(
        self, cleanup_dependency_overrides
    ):
        """
        8A: Registration requires consent when tenant lawful_basis=consent.

        When a tenant has consent as their lawful basis, the public registration
        endpoint must enforce that consent_granted is True. Registration without
        consent should be rejected.
        """
        with patch(
            "services.visit_session_service.resume_draft_registration",
            new_callable=AsyncMock,
        ) as mock_reg:
            mock_reg.side_effect = ValueError(
                "Consent is required for registration at this location"
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/public/register",
                    json={
                        "tenant_id": "tenant-consent-required",
                        "phone": "+1234567890",
                        "full_name": "John Doe",
                        "company": "Acme Corp",
                        "department_id": "dept-001",
                        "purpose": "Business meeting",
                        "consent_granted": False,  # Not granted
                    },
                )

            assert response.status_code in [400, 422]

    @pytest.mark.asyncio
    async def test_public_dsr_submission(self, cleanup_dependency_overrides):
        """
        8D: DSR submission creates record with verification token.

        When a visitor submits a Data Subject Request (DSR) via public endpoint,
        it should create a record with:
        - A unique ID
        - DSR type (access, correction, deletion, consent_withdrawal)
        - Status (pending)
        - A verification token for validating the requester
        """
        dsr_record = {
            "id": "dsr-001",
            "tenant_id": "tenant-001",
            "visitor_id": "visitor-001",
            "dsr_type": DSRType.ACCESS,
            "email": "john@example.com",
            "status": DSRStatus.PENDING,
            "verification_token": "TOKEN_ABC123XYZ",
            "description": "Request copy of my data",
            "date_created": 1712532000,
            "date_updated": 1712532000,
        }

        with patch(
            "services.data_subject_request_service.create_dsr", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = dsr_record

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/data-subject-requests/public/submit",
                    json={
                        "tenant_id": "tenant-001",
                        "email": "john@example.com",
                        "dsr_type": "access",
                        "description": "Request copy of my data",
                    },
                )

            assert response.status_code == 201
            data = response.json()
            assert data["success"] is True
            assert data["data"]["status"] == "pending"
            assert "verification_token" in data["data"]

    @pytest.mark.asyncio
    async def test_consent_withdrawal(self, cleanup_dependency_overrides):
        """
        8E: Consent withdrawal updates all active sessions.

        When a visitor withdraws consent, all of their active visit sessions
        should be updated to reflect the withdrawal with consent_withdrawal_at
        timestamp set.
        """
        timestamp = 1712532000

        with patch(
            "services.visitor_profile_service.withdraw_consent", new_callable=AsyncMock
        ) as mock_withdraw:
            mock_withdraw.return_value = {
                "visitor_id": "visitor-001",
                "consent_withdrawn_at": timestamp,
                "sessions_updated": 3,
            }

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/public/withdraw-consent",
                    json={
                        "email": "john@example.com",
                        "tenant_id": "tenant-001",
                    },
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["data"]["consent_withdrawn_at"] == timestamp
            assert data["data"]["sessions_updated"] == 3

    @pytest.mark.asyncio
    async def test_public_registration_returns_badge_qr_token(
        self, cleanup_dependency_overrides
    ):
        """
        4B: Public registration returns badge QR token for checkout.

        The public registration endpoint should return a badge_qr_token
        that the visitor can use for later checkout without additional auth.
        """
        session_out = _make_visit_session_out(
            status=VisitStatus.REGISTERED,
            consent_granted=True,
            badge_qr_token="VIS_20240407_1234567890AB",
        )

        with patch(
            "services.visit_session_service.resume_draft_registration",
            new_callable=AsyncMock,
        ) as mock_reg:
            mock_reg.return_value = session_out

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/public/register",
                    json={
                        "tenant_id": "tenant-001",
                        "phone": "+1234567890",
                        "full_name": "John Doe",
                        "company": "Acme Corp",
                        "department_id": "dept-001",
                        "purpose": "Business meeting",
                        "consent_granted": True,
                    },
                )

            assert response.status_code == 201
            data = response.json()
            assert "badge_qr_token" in data["data"]
            assert data["data"]["badge_qr_token"] == "VIS_20240407_1234567890AB"
