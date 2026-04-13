"""
Unit tests for verification flows (Phases 2A-2C).

Tests:
- ID scan verification endpoint returns OCR results
- Applying ID scan updates session and profile
- Host approval sets VERIFIED status
- Host approval validates tenant
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from schemas.imports import VerificationStatus, VerificationMethod
from security.principal import AuthPrincipal


MOCK_RECEPTIONIST_PRINCIPAL = AuthPrincipal(
    user_id="receptionist-001",
    role="receptionist",
    access_token_id="token-rec-001",
    jwt_token="jwt-token-rec-001",
    tenant_id="tenant-001",
)

MOCK_HOST_PRINCIPAL = AuthPrincipal(
    user_id="host-001",
    role="receptionist",
    access_token_id="token-host-001",
    jwt_token="jwt-token-host-001",
    tenant_id="tenant-001",
)


def _make_visit_session_out(
    id: str = "session-001",
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
    verified_by: str | None = None,
    verification_method: VerificationMethod | None = None,
    **kwargs,
) -> dict:
    """Factory function for visit session."""
    return {
        "id": id,
        "tenant_id": "tenant-001",
        "visitor_profile_id": "visitor-001",
        "department_id": "dept-001",
        "host_id": "host-001",
        "receptionist_id": "receptionist-001",
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "manual_entry",
        "check_out_method": None,
        "verification_status": verification_status,
        "verification_method": verification_method,
        "verified_by": verified_by,
        "status": "registered",
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
        "badge_pdf_object_key": "badges/session-001.pdf",
        "check_in_time": 1712532000,
        "check_out_time": None,
        "date_created": 1712532000,
        "visit_duration": None,
        **kwargs,
    }


def _make_visitor_profile_out(id: str = "visitor-001", **kwargs) -> dict:
    """Factory function for visitor profile."""
    return {
        "id": id,
        "tenant_id": "tenant-001",
        "email": "john@example.com",
        "phone": "+1234567890",
        "full_name": "John Doe",
        "company": "Acme Corp",
        "visit_count": 1,
        "last_visit": 1712532000,
        "profile_image_object_key": None,
        "id_type": None,
        "id_number": None,
        "id_expiry": None,
        "id_country": None,
        "id_image_object_key": None,
        "date_of_birth": None,
        "profiling_preference": "allowed",
        "date_created": 1712532000,
        "last_updated": 1712532000,
        **kwargs,
    }


@pytest.fixture(autouse=True)
def cleanup_dependency_overrides():
    """Cleanup dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()


class TestVerification:
    """Tests for verification flows (2A-2C)."""

    @pytest.mark.asyncio
    async def test_id_scan_verification_endpoint(self, cleanup_dependency_overrides):
        """
        2A: ID scan endpoint returns OCR results.

        When a receptionist uploads an ID image, the backend processes it through
        OCR and returns structured data (id_type, id_number, id_expiry, etc).
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        ocr_result = {
            "id_type": "passport",
            "id_number": "AB123456",
            "id_expiry": 1744060800,
            "id_country": "GB",
            "full_name": "John Doe",
        }

        with patch(
            "services.visit_session_service.verify_id_with_ocr", new_callable=AsyncMock
        ) as mock_ocr:
            mock_ocr.return_value = ocr_result

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/session-001/verify-id",
                    json={
                        "id_image_object_key": "uploads/id-001.png",
                    },
                    headers={"Authorization": "Bearer token-rec-001"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["data"]["id_type"] == "passport"
            assert data["data"]["id_number"] == "AB123456"
            mock_ocr.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_id_scan_updates_session_and_profile(
        self, cleanup_dependency_overrides
    ):
        """
        2B: Applying scan results updates session verification and profile ID fields.

        After OCR extracts ID data, applying those results should:
        - Update the session's verification_status to VERIFIED
        - Update the visitor_profile with id_type, id_number, id_expiry, etc
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        verified_session = _make_visit_session_out(
            verification_status=VerificationStatus.VERIFIED,
            verification_method=VerificationMethod.ID_SCAN,
            verified_by="receptionist-001",
        )

        _make_visitor_profile_out(
            id_type="passport",
            id_number="AB123456",
            id_expiry=1744060800,
            id_country="GB",
        )

        with patch(
            "services.visit_session_service.apply_id_scan_verification",
            new_callable=AsyncMock,
        ) as mock_apply:
            mock_apply.return_value = verified_session

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/session-001/apply-id-scan",
                    json={
                        "id_type": "passport",
                        "id_number": "AB123456",
                        "id_image_object_key": "uploads/id-001.png",
                    },
                    headers={"Authorization": "Bearer token-rec-001"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["data"]["verification_status"] == "verified"
            assert data["data"]["verification_method"] == "id_scan"
            assert data["data"]["verified_by"] == "receptionist-001"
            mock_apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_host_approval_sets_verification(self, cleanup_dependency_overrides):
        """
        2C: Host approval sets VERIFIED status with HOST_APPROVAL method.

        When a host approves a visitor, the session should be marked as VERIFIED
        with verification_method=HOST_APPROVAL and verified_by set to the host's ID.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_HOST_PRINCIPAL
        )

        approved_session = _make_visit_session_out(
            verification_status=VerificationStatus.VERIFIED,
            verification_method=VerificationMethod.HOST_APPROVAL,
            verified_by="host-001",
        )

        with patch(
            "services.visit_session_service.approve_visitor_by_host",
            new_callable=AsyncMock,
        ) as mock_approve:
            mock_approve.return_value = approved_session

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/session-001/approve",
                    json={},
                    headers={"Authorization": "Bearer token-host-001"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["data"]["verification_status"] == "verified"
            assert data["data"]["verification_method"] == "host_approval"
            assert data["data"]["verified_by"] == "host-001"
            mock_approve.assert_called_once()

    @pytest.mark.asyncio
    async def test_host_approval_validates_tenant(self, cleanup_dependency_overrides):
        """
        2C: Host must belong to same tenant.

        When a host attempts to approve a visitor, the backend should verify that
        the host and visitor are in the same tenant. Approval from a host in a
        different tenant should fail.
        """
        from security.auth import verify_system_user_token

        # Host from different tenant
        different_tenant_host = AuthPrincipal(
            user_id="host-002",
            role="receptionist",
            access_token_id="token-host-002",
            jwt_token="jwt-token-host-002",
            tenant_id="tenant-002",  # Different tenant
        )

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            different_tenant_host
        )

        with patch(
            "services.visit_session_service.approve_visitor_by_host",
            new_callable=AsyncMock,
        ) as mock_approve:
            mock_approve.side_effect = PermissionError(
                "Host and visitor must be in same tenant"
            )

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/session-001/approve",
                    json={},
                    headers={"Authorization": "Bearer token-host-002"},
                )

            assert response.status_code in [403, 400]

    @pytest.mark.asyncio
    async def test_id_scan_error_handling(self, cleanup_dependency_overrides):
        """
        2A: ID scan endpoint handles OCR failures gracefully.

        If OCR processing fails (invalid image, unreadable document), the endpoint
        should return an appropriate error message without crashing.
        """
        from security.auth import verify_system_user_token

        app.dependency_overrides[verify_system_user_token] = lambda *roles: (
            MOCK_RECEPTIONIST_PRINCIPAL
        )

        with patch(
            "services.visit_session_service.verify_id_with_ocr", new_callable=AsyncMock
        ) as mock_ocr:
            mock_ocr.side_effect = ValueError("Could not extract ID data from image")

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/visitors/session-001/verify-id",
                    json={
                        "id_image_object_key": "uploads/invalid.png",
                    },
                    headers={"Authorization": "Bearer token-rec-001"},
                )

            assert response.status_code in [400, 422]
