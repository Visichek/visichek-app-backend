"""
Integration tests for the tenant bootstrap flow:
  - Application admin creates tenant + first super_admin in one request
  - Super_admin created via bootstrap can log in and manage the tenant
  - Duplicate bootstrap attempts are rejected
  - Tenant creation via POST /tenants/ now requires application admin auth

Requires: MongoDB on localhost:27017, Redis on localhost:6379
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient

from schemas.admin_schema import AdminCreate
from repositories.admin_repo import create_admin


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def admin_auth_headers(
    mongo_db,
    integration_client: AsyncClient,
):
    """
    Create a application admin directly in MongoDB and log them in.
    Returns auth headers dict.
    """
    raw_password = f"AdminPass_{int(time.time())}!"
    admin_data = AdminCreate(
        full_name="Bootstrap Test Admin",
        email=f"bootstrap_admin_{int(time.time())}@test.example.com",
        password=raw_password,
        invited_by="seed",
    )
    await create_admin(admin_data)

    # Login via API
    response = await integration_client.post(
        "/v1/admins/login",
        json={
            "email": admin_data.email,
            "password": raw_password,
        },
    )
    assert response.status_code == 200, f"Admin login failed: {response.text}"

    data = response.json()
    assert data["success"] is True
    access_token = data["data"]["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


class TestBootstrapFlow:
    """Full integration test for the bootstrap endpoint."""

    async def test_bootstrap_creates_tenant_and_super_admin(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        """Bootstrap should return a tenant and a super_admin with tokens."""
        ts = int(time.time())
        payload = {
            "company_name": f"Bootstrap Corp {ts}",
            "lawful_basis": "legitimate_interest",
            "notice_display_mode": "passive",
            "retention_days": 365,
            "dpo_contact_email": "dpo@bootstrap.test",
            "country_of_hosting": "Nigeria",
            "admin_full_name": "First Super Admin",
            "admin_email": f"super_{ts}@bootstrap.test",
            "admin_password": "SuperSecure123!",
        }

        resp = await integration_client.post(
            "/v1/admins/tenants/bootstrap",
            json=payload,
            headers=admin_auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]

        # Tenant assertions
        assert data["tenant"]["company_name"] == payload["company_name"]
        assert data["tenant"]["id"] is not None

        # Super admin assertions
        assert data["super_admin"]["full_name"] == "First Super Admin"
        assert data["super_admin"]["email"] == payload["admin_email"]
        assert data["super_admin"]["role"] == "super_admin"
        assert data["super_admin"]["tenant_id"] == data["tenant"]["id"]
        assert data["super_admin"]["access_token"] is not None
        assert data["super_admin"]["refresh_token"] is not None

    async def test_bootstrapped_super_admin_can_login(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        """The super_admin created by bootstrap should be able to login."""
        ts = int(time.time())
        email = f"login_test_{ts}@bootstrap.test"
        password = "LoginTest123!"

        # Bootstrap
        resp = await integration_client.post(
            "/v1/admins/tenants/bootstrap",
            json={
                "company_name": f"Login Test Corp {ts}",
                "admin_full_name": "Login Test SA",
                "admin_email": email,
                "admin_password": password,
            },
            headers=admin_auth_headers,
        )
        assert resp.status_code == 201

        # Login as the new super_admin
        login_resp = await integration_client.post(
            "/v1/system-users/login",
            json={"email": email, "password": password},
        )
        assert login_resp.status_code == 200, login_resp.text
        login_data = login_resp.json()["data"]
        assert login_data["role"] == "super_admin"
        assert login_data["access_token"] is not None

    async def test_bootstrapped_super_admin_can_invite_users(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        """The bootstrapped super_admin should be able to create system users."""
        ts = int(time.time())
        sa_email = f"invite_sa_{ts}@bootstrap.test"
        sa_password = "InviteSA123!"

        # Bootstrap
        resp = await integration_client.post(
            "/v1/admins/tenants/bootstrap",
            json={
                "company_name": f"Invite Test Corp {ts}",
                "admin_full_name": "Invite Test SA",
                "admin_email": sa_email,
                "admin_password": sa_password,
            },
            headers=admin_auth_headers,
        )
        assert resp.status_code == 201
        bootstrap_data = resp.json()["data"]
        sa_token = bootstrap_data["super_admin"]["access_token"]
        tenant_id = bootstrap_data["tenant"]["id"]
        sa_headers = {"Authorization": f"Bearer {sa_token}"}

        # Invite a receptionist
        invite_resp = await integration_client.post(
            "/v1/system-users/signup",
            json={
                "tenant_id": tenant_id,
                "full_name": "Front Desk",
                "email": f"receptionist_{ts}@bootstrap.test",
                "role": "receptionist",
                "account_status": "ACTIVE",
                "password_hash": "Receptionist123!",
            },
            headers=sa_headers,
        )
        assert invite_resp.status_code in (200, 201), invite_resp.text
        assert invite_resp.json()["data"]["role"] == "receptionist"

    async def test_duplicate_bootstrap_same_company_rejected(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        """Bootstrap with an already-existing company name should be rejected."""
        ts = int(time.time())
        company = f"Duplicate Corp {ts}"

        # First bootstrap — should succeed
        resp1 = await integration_client.post(
            "/v1/admins/tenants/bootstrap",
            json={
                "company_name": company,
                "admin_full_name": "SA One",
                "admin_email": f"sa1_{ts}@dup.test",
                "admin_password": "Pass123!",
            },
            headers=admin_auth_headers,
        )
        assert resp1.status_code == 201

        # Second bootstrap with same company — should fail 409
        resp2 = await integration_client.post(
            "/v1/admins/tenants/bootstrap",
            json={
                "company_name": company,
                "admin_full_name": "SA Two",
                "admin_email": f"sa2_{ts}@dup.test",
                "admin_password": "Pass456!",
            },
            headers=admin_auth_headers,
        )
        assert resp2.status_code == 409

    async def test_bootstrap_without_auth_rejected(
        self,
        integration_client: AsyncClient,
    ):
        """Bootstrap endpoint without auth should be rejected."""
        resp = await integration_client.post(
            "/v1/admins/tenants/bootstrap",
            json={
                "company_name": "No Auth Corp",
                "admin_full_name": "Jane Doe",
                "admin_email": "jane@noauth.test",
                "admin_password": "Pass123!",
            },
        )
        assert resp.status_code in (401, 403)


class TestTenantCreationAuthChange:
    """Verify that POST /tenants/ now requires application admin auth, not super_admin."""

    async def test_tenant_creation_with_admin_auth_succeeds(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        """Application admin should be able to create a tenant directly."""
        ts = int(time.time())
        resp = await integration_client.post(
            "/v1/tenants/",
            json={
                "company_name": f"Admin Created Tenant {ts}",
                "lawful_basis": "legitimate_interest",
                "notice_display_mode": "passive",
                "retention_days": 730,
            },
            headers=admin_auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["company_name"] == f"Admin Created Tenant {ts}"

    async def test_tenant_creation_without_auth_rejected(
        self,
        integration_client: AsyncClient,
    ):
        """Tenant creation without auth should fail."""
        resp = await integration_client.post(
            "/v1/tenants/",
            json={"company_name": "Unauthed Tenant"},
        )
        assert resp.status_code in (401, 403)

    async def test_tenant_creation_with_system_user_auth_rejected(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
    ):
        """System user (super_admin) should no longer be able to create tenants via POST /tenants/."""
        ts = int(time.time())
        resp = await integration_client.post(
            "/v1/tenants/",
            json={
                "company_name": f"SA Attempted Tenant {ts}",
                "lawful_basis": "consent",
                "retention_days": 365,
            },
            headers=auth_headers,
        )
        # Should fail because the endpoint now checks for application admin role,
        # not system user role
        assert resp.status_code in (401, 403, 422), (
            f"Expected rejection for super_admin, got {resp.status_code}: {resp.text}"
        )
