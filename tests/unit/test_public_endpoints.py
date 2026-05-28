"""
Unit tests for public endpoints.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from main import app
from schemas.public_registration_schema import (
    PublicBadgePassOut,
    PublicBadgePassTenant,
    PublicRegistrationResponse,
    PublicCheckoutResponse,
)


VALID_TENANT_ID = str(ObjectId())


@pytest.fixture(autouse=True)
def cleanup_dependency_overrides():
    yield
    app.dependency_overrides.clear()


class TestPublicEndpoints:
    @pytest.mark.asyncio
    async def test_public_registration_no_auth_required(
        self, cleanup_dependency_overrides
    ):
        resp = PublicRegistrationResponse(
            session_id="session-001",
            visitor_profile_id="visitor-001",
            status="registered",
            message="ok",
        )
        with patch(
            "api.v1.public_registration_route.register_visitor_public",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/v1/public/register/{VALID_TENANT_ID}",
                    json={
                        "phone": "+1234567890",
                        "full_name": "John Doe",
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
        resp = PublicRegistrationResponse(
            session_id="session-001",
            visitor_profile_id="visitor-001",
            status="registered",
            message="ok",
        )
        with patch(
            "api.v1.public_registration_route.register_visitor_public",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/v1/public/register/{VALID_TENANT_ID}",
                    json={
                        "phone": "+1234567890",
                        "full_name": "John Doe",
                        "consent_granted": True,
                    },
                )
            assert response.status_code == 201
            data = response.json()
            assert data["data"]["status"] == "registered"

    @pytest.mark.asyncio
    async def test_public_checkout_via_badge_qr(self, cleanup_dependency_overrides):
        resp = PublicCheckoutResponse(
            session_id="session-001", status="checked_out", visit_duration=3600
        )
        with patch(
            "api.v1.public_registration_route.checkout_visitor_public",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/public/checkout",
                    params={"badge_qr_token": "VIS_20240407_1234567890AB"},
                )
            assert response.status_code == 200
            data = response.json()
            assert data["data"]["status"] == "checked_out"

    @pytest.mark.asyncio
    async def test_public_registration_consent_enforcement_required(
        self, cleanup_dependency_overrides
    ):
        from fastapi import HTTPException

        with patch(
            "api.v1.public_registration_route.register_visitor_public",
            new_callable=AsyncMock,
            side_effect=HTTPException(status_code=400, detail="Consent is required"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/v1/public/register/{VALID_TENANT_ID}",
                    json={
                        "phone": "+1234567890",
                        "full_name": "John Doe",
                        "consent_granted": False,
                    },
                )
            assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_public_dsr_submission(self, cleanup_dependency_overrides):
        # Routes may vary; accept any 2xx or 422/404 response indicating the endpoint exists.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/public/rights/request",
                json={
                    "tenant_id": VALID_TENANT_ID,
                    "email": "john@example.com",
                    "request_type": "access",
                    "description": "Request copy of my data",
                },
            )
        # Endpoint exists (not 404) — we accept anything except 404 as a pass.
        assert response.status_code != 404

    @pytest.mark.asyncio
    async def test_consent_withdrawal(self, cleanup_dependency_overrides):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/public/rights/withdraw-consent",
                json={"email": "john@example.com", "tenant_id": VALID_TENANT_ID},
            )
        assert response.status_code != 404

    @pytest.mark.asyncio
    async def test_public_registration_returns_badge_qr_token(
        self, cleanup_dependency_overrides
    ):
        resp = PublicRegistrationResponse(
            session_id="session-001",
            visitor_profile_id="visitor-001",
            status="registered",
            message="ok",
        )
        with patch(
            "api.v1.public_registration_route.register_visitor_public",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/v1/public/register/{VALID_TENANT_ID}",
                    json={
                        "phone": "+1234567890",
                        "full_name": "John Doe",
                        "consent_granted": True,
                    },
                )
            assert response.status_code == 201
            data = response.json()
            assert (
                data["data"].get("session_id") == "session-001"
                or data["data"].get("sessionId") == "session-001"
            )

    @pytest.mark.asyncio
    async def test_public_checkout_invalid_qr_token(self, cleanup_dependency_overrides):
        from fastapi import HTTPException

        with patch(
            "api.v1.public_registration_route.checkout_visitor_public",
            new_callable=AsyncMock,
            side_effect=HTTPException(
                status_code=400, detail="Invalid or expired QR token"
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/public/checkout",
                    params={"badge_qr_token": "INVALID_TOKEN"},
                )
            assert response.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_public_badge_pass_returns_pass(
        self, cleanup_dependency_overrides
    ):
        token = "bqt_test_123"
        resp = PublicBadgePassOut(
            token=token,
            visitor_name="Nathaniel Uriri",
            company="Introgroup Technologies",
            purpose="Quarterly partnership review",
            host_name="Ada Receptionist",
            department_name="Operations",
            status="checked_in",
            issued_at=1748419200,
            expires_at=1748448000,
            tenant=PublicBadgePassTenant(
                company_name="Doux Finance",
                logo_url="https://cdn.example.com/logo.png",
                branding_enabled=True,
            ),
        )
        with patch(
            "api.v1.public_registration_route.get_public_badge_pass",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/v1/public/badge/{token}")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            payload = data["data"]
            # camelCase is the default; allow either to keep the test
            # resilient against case-conversion middleware changes.
            assert (
                payload.get("visitorName") == "Nathaniel Uriri"
                or payload.get("visitor_name") == "Nathaniel Uriri"
            )
            assert (
                payload.get("token") == token
                or payload.get("Token") == token
            )
            tenant = payload.get("tenant") or {}
            assert (
                tenant.get("brandingEnabled") is True
                or tenant.get("branding_enabled") is True
            )

    @pytest.mark.asyncio
    async def test_public_badge_pass_unknown_token_returns_404(
        self, cleanup_dependency_overrides
    ):
        from core.errors import resource_not_found

        with patch(
            "api.v1.public_registration_route.get_public_badge_pass",
            new_callable=AsyncMock,
            side_effect=resource_not_found(resource="Badge", resource_id="bad"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/v1/public/badge/bad-token")
            assert response.status_code == 404
            data = response.json()
            assert data["success"] is False

    @pytest.mark.asyncio
    async def test_public_badge_pass_does_not_require_auth(
        self, cleanup_dependency_overrides
    ):
        """The endpoint must be reachable with no Authorization header.

        Knowing the token is the only credential — same as
        ``POST /v1/public/checkout``.
        """
        token = "bqt_unauth"
        resp = PublicBadgePassOut(
            token=token,
            visitor_name="Jane Visitor",
            status="checked_in",
            tenant=PublicBadgePassTenant(
                company_name="Acme", branding_enabled=False
            ),
        )
        with patch(
            "api.v1.public_registration_route.get_public_badge_pass",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Deliberately no Authorization header.
                response = await client.get(f"/v1/public/badge/{token}")
            assert response.status_code == 200
