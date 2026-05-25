"""Unit tests for the first-login tenant-info confirmation endpoints.

Covers ``GET /v1/onboarding/me/tenant-confirmation`` and
``POST /v1/onboarding/me/tenant-confirmation``. Both are synchronous
self-service endpoints (mirroring ``POST /v1/onboarding/me/complete``),
so they return 200 with the confirmation payload rather than a 202 queue
receipt. Service functions are mocked — no DB/cache is touched.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from main import app
from security.principal import AuthPrincipal
from security.auth import verify_super_admin_token


MOCK_SUPER_ADMIN_PRINCIPAL = AuthPrincipal(
    user_id="super-admin-123",
    role="super_admin",
    access_token_id="token-123",
    jwt_token="jwt-token-123",
    tenant_id="tenant-001",
)


@pytest.fixture
def cleanup_dependency_overrides():
    """Cleanup dependency overrides after each test."""
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_tenant_confirmation_success(cleanup_dependency_overrides):
    """GET returns the review payload with onboarding context attached."""
    app.dependency_overrides[verify_super_admin_token] = lambda: (
        MOCK_SUPER_ADMIN_PRINCIPAL
    )

    with patch(
        "api.v1.onboarding_route.get_tenant_info_confirmation",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {
            "tenant_id": "tenant-001",
            "company_name": "Acme Clinics",
            "dpo_contact_email": "dpo@acme.example.com",
            "privacy_policy_url": None,
            "country_of_hosting": "NG",
            "onboarding_info_confirmed": False,
            "onboarding_info_confirmed_at": None,
            "onboarding_submission_id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "onboarding_fields": {
                "company": "Acme Clinics",
                "email": "ops@acme.example.com",
            },
            "onboarding_field_labels": {
                "company": "Company name",
                "email": "Work email",
            },
            "onboarding_field_order": ["company", "email"],
        }

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/v1/onboarding/me/tenant-confirmation",
                headers={"Authorization": "Bearer token-123"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["companyName"] == "Acme Clinics"
    assert body["data"]["onboardingInfoConfirmed"] is False
    mock_get.assert_awaited_once_with(tenant_id="tenant-001")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_confirm_tenant_info_success(cleanup_dependency_overrides):
    """POST confirms (and edits) tenant info synchronously, returns 200."""
    app.dependency_overrides[verify_super_admin_token] = lambda: (
        MOCK_SUPER_ADMIN_PRINCIPAL
    )

    with patch(
        "api.v1.onboarding_route.confirm_tenant_info",
        new_callable=AsyncMock,
    ) as mock_confirm:
        mock_confirm.return_value = {
            "tenant_id": "tenant-001",
            "company_name": "Acme Health Ltd",
            "dpo_contact_email": "dpo@acme.example.com",
            "privacy_policy_url": "https://acme.example.com/privacy",
            "country_of_hosting": "NG",
            "onboarding_info_confirmed": True,
            "onboarding_info_confirmed_at": 1716200000,
            "onboarding_submission_id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "onboarding_fields": {},
            "onboarding_field_labels": {},
            "onboarding_field_order": [],
        }

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/onboarding/me/tenant-confirmation",
                json={
                    "company_name": "Acme Health Ltd",
                    "privacy_policy_url": "https://acme.example.com/privacy",
                },
                headers={"Authorization": "Bearer token-123"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["onboardingInfoConfirmed"] is True
    assert body["data"]["companyName"] == "Acme Health Ltd"
    # tenant_id comes from the token, not the body
    assert mock_confirm.await_args.kwargs["tenant_id"] == "tenant-001"
    assert mock_confirm.await_args.kwargs["actor_id"] == "super-admin-123"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_confirm_tenant_info_accepts_dpa_fields(cleanup_dependency_overrides):
    """The confirm endpoint whitelists dpaAccepted / dpaAcceptedAt (no 422) and
    forwards them to the service."""
    app.dependency_overrides[verify_super_admin_token] = lambda: (
        MOCK_SUPER_ADMIN_PRINCIPAL
    )

    with patch(
        "api.v1.onboarding_route.confirm_tenant_info",
        new_callable=AsyncMock,
    ) as mock_confirm:
        mock_confirm.return_value = {
            "tenant_id": "tenant-001",
            "company_name": "Acme Clinics",
            "dpo_contact_email": None,
            "privacy_policy_url": None,
            "country_of_hosting": None,
            "onboarding_info_confirmed": True,
            "onboarding_info_confirmed_at": 1716200000,
            "dpa_accepted": True,
            "dpa_accepted_at": 1716200000,
            "dpa_version": "1.0",
            "onboarding_submission_id": None,
            "onboarding_fields": {},
            "onboarding_field_labels": {},
            "onboarding_field_order": [],
        }

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/onboarding/me/tenant-confirmation",
                json={"dpaAccepted": True, "dpaAcceptedAt": 1716200000},
                headers={"Authorization": "Bearer token-123"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["dpaAccepted"] is True
    assert body["data"]["dpaVersion"] == "1.0"
    # The whitelisted fields reached the service (camel -> snake conversion).
    payload = mock_confirm.await_args.args[0]
    assert getattr(payload, "dpa_accepted") is True
    assert getattr(payload, "dpa_accepted_at") == 1716200000


@pytest.mark.asyncio
@pytest.mark.unit
async def test_confirm_tenant_info_empty_body_is_valid(cleanup_dependency_overrides):
    """A bare confirmation (no edits) is accepted — every field is optional."""
    app.dependency_overrides[verify_super_admin_token] = lambda: (
        MOCK_SUPER_ADMIN_PRINCIPAL
    )

    with patch(
        "api.v1.onboarding_route.confirm_tenant_info",
        new_callable=AsyncMock,
    ) as mock_confirm:
        mock_confirm.return_value = {
            "tenant_id": "tenant-001",
            "company_name": "Acme Clinics",
            "dpo_contact_email": None,
            "privacy_policy_url": None,
            "country_of_hosting": None,
            "onboarding_info_confirmed": True,
            "onboarding_info_confirmed_at": 1716200000,
            "onboarding_submission_id": None,
            "onboarding_fields": {},
            "onboarding_field_labels": {},
            "onboarding_field_order": [],
        }

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/onboarding/me/tenant-confirmation",
                json={},
                headers={"Authorization": "Bearer token-123"},
            )

    assert response.status_code == 200
    assert response.json()["data"]["onboardingInfoConfirmed"] is True
