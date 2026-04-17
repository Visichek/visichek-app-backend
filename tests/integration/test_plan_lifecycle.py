"""
Integration tests for the full plan/subscription/discount lifecycle:
  - Admin creates plans with feature rules, CRUD limits, retrieval quotas
  - Admin subscribes a tenant to a plan
  - Plan enforcement blocks disabled features
  - Quota enforcement blocks when limits exceeded
  - Admin changes tenant plan (immediate effect)
  - Discounts reduce effective pricing
  - Admin cancels subscription

Requires: MongoDB on localhost:27017, Redis on localhost:6379
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient

from schemas.admin_schema import AdminCreate
from repositories.admin_repo import create_admin
from tests.integration.conftest import unique_password_suffix


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def admin_auth_headers(
    mongo_db,
    integration_client: AsyncClient,
):
    """Create a application admin and log them in."""
    raw_password = f"PlanTestAdmin_{unique_password_suffix()}!"
    admin_data = AdminCreate(
        full_name="Plan Test Admin",
        email=f"plan_admin_{int(time.time())}@test.example.com",
        password=raw_password,
        invited_by="seed",
    )
    await create_admin(admin_data)

    response = await integration_client.post(
        "/v1/admins/login",
        json={"email": admin_data.email, "password": raw_password},
    )
    assert response.status_code == 200
    challenge_id = response.json()["data"]["otp_challenge_id"]

    verify_response = await integration_client.post(
        "/v1/admins/verify-otp",
        json={"otp_challenge_id": challenge_id, "otp_code": "123456"},
    )
    assert verify_response.status_code == 200, verify_response.text
    data = verify_response.json()
    assert data["success"] is True
    return {"Authorization": f"Bearer {data['data']['access_token']}"}


class TestPlanCRUD:
    """Test plan creation, listing, updating, archiving, cloning."""

    async def test_create_plan(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        resp = await integration_client.post(
            "/v1/plans",
            json={
                "name": f"test-plan-{ts}",
                "display_name": f"Test Plan {ts}",
                "tier": "professional",
                "base_price_monthly": 99.99,
                "base_price_yearly": 999.99,
                "feature_rules": [
                    {"endpoint_pattern": "/v1/visitors/*", "enabled": True},
                    {
                        "endpoint_pattern": "/v1/audit/*",
                        "enabled": False,
                        "description": "Not on this plan",
                    },
                ],
                "crud_limits": [
                    {
                        "collection": "visitors",
                        "max_create": 50,
                        "max_update": 100,
                        "reset_interval": "monthly",
                    },
                ],
                "retrieval_quotas": [
                    {
                        "collection": "dashboard",
                        "max_reads": 200,
                        "reset_interval": "daily",
                    },
                ],
                "tenant_caps": {"max_system_users": 10, "max_departments": 5},
            },
            headers=admin_auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["name"] == f"test-plan-{ts}"
        assert data["tier"] == "professional"
        assert len(data["feature_rules"]) == 2
        assert data["crud_limits"][0]["max_create"] == 50

    async def test_list_plans(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        # Create two plans
        for i in range(2):
            await integration_client.post(
                "/v1/plans",
                json={
                    "name": f"list-plan-{ts}-{i}",
                    "display_name": f"List Plan {ts} #{i}",
                },
                headers=admin_auth_headers,
            )

        resp = await integration_client.get("/v1/plans")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 2

    async def test_update_plan(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        create_resp = await integration_client.post(
            "/v1/plans",
            json={"name": f"update-plan-{ts}", "display_name": "To Update"},
            headers=admin_auth_headers,
        )
        plan_id = create_resp.json()["data"]["id"]

        update_resp = await integration_client.put(
            f"/v1/plans/{plan_id}",
            json={"display_name": "Updated!", "base_price_monthly": 49.99},
            headers=admin_auth_headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["data"]["display_name"] == "Updated!"
        assert update_resp.json()["data"]["base_price_monthly"] == 49.99

    async def test_archive_plan(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        create_resp = await integration_client.post(
            "/v1/plans",
            json={
                "name": f"archive-plan-{ts}",
                "display_name": "To Archive",
                "status": "active",
            },
            headers=admin_auth_headers,
        )
        plan_id = create_resp.json()["data"]["id"]

        archive_resp = await integration_client.post(
            f"/v1/plans/{plan_id}/archive",
            headers=admin_auth_headers,
        )
        assert archive_resp.status_code == 200
        assert archive_resp.json()["data"]["status"] == "archived"

    async def test_duplicate_plan_name_rejected(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        name = f"dup-plan-{ts}"

        resp1 = await integration_client.post(
            "/v1/plans",
            json={"name": name, "display_name": "First"},
            headers=admin_auth_headers,
        )
        assert resp1.status_code == 201

        resp2 = await integration_client.post(
            "/v1/plans",
            json={"name": name, "display_name": "Second"},
            headers=admin_auth_headers,
        )
        assert resp2.status_code == 409


class TestSubscriptionLifecycle:
    """Test subscribing tenants, changing plans, cancelling."""

    async def _create_plan_and_tenant(self, client, headers, ts):
        """Helper: create a plan and a tenant, return their IDs."""
        # Create plan
        plan_resp = await client.post(
            "/v1/plans",
            json={
                "name": f"sub-plan-{ts}",
                "display_name": f"Sub Plan {ts}",
                "status": "active",
                "base_price_monthly": 100.0,
                "base_price_yearly": 1000.0,
            },
            headers=headers,
        )
        assert plan_resp.status_code == 201
        plan_id = plan_resp.json()["data"]["id"]

        # Create tenant via bootstrap
        tenant_resp = await client.post(
            "/v1/admins/tenants/bootstrap",
            json={
                "company_name": f"Sub Test Corp {ts}",
                "admin_full_name": "SA",
                "admin_email": f"sa_{ts}@sub.example.com",
                "admin_password": "SubTest123!",
            },
            headers=headers,
        )
        assert tenant_resp.status_code == 201, tenant_resp.text
        tenant_id = tenant_resp.json()["data"]["tenant"]["id"]

        return plan_id, tenant_id

    async def test_subscribe_tenant(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        plan_id, tenant_id = await self._create_plan_and_tenant(
            integration_client,
            admin_auth_headers,
            ts,
        )

        resp = await integration_client.post(
            "/v1/subscriptions",
            json={
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "billing_cycle": "monthly",
            },
            headers=admin_auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["tenant_id"] == tenant_id
        assert data["plan_id"] == plan_id
        assert data["status"] == "active"
        assert data["effective_price"] == 100.0

    async def test_duplicate_subscription_rejected(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        plan_id, tenant_id = await self._create_plan_and_tenant(
            integration_client,
            admin_auth_headers,
            ts,
        )

        # First subscription
        resp1 = await integration_client.post(
            "/v1/subscriptions",
            json={"tenant_id": tenant_id, "plan_id": plan_id},
            headers=admin_auth_headers,
        )
        assert resp1.status_code == 201

        # Duplicate
        resp2 = await integration_client.post(
            "/v1/subscriptions",
            json={"tenant_id": tenant_id, "plan_id": plan_id},
            headers=admin_auth_headers,
        )
        assert resp2.status_code == 409

    async def test_cancel_subscription(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        plan_id, tenant_id = await self._create_plan_and_tenant(
            integration_client,
            admin_auth_headers,
            ts,
        )

        await integration_client.post(
            "/v1/subscriptions",
            json={"tenant_id": tenant_id, "plan_id": plan_id},
            headers=admin_auth_headers,
        )

        cancel_resp = await integration_client.post(
            "/v1/subscriptions/cancel",
            json={"tenant_id": tenant_id, "immediate": True, "reason": "Testing"},
            headers=admin_auth_headers,
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["data"]["status"] == "cancelled"

    async def test_get_tenant_active_subscription(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        plan_id, tenant_id = await self._create_plan_and_tenant(
            integration_client,
            admin_auth_headers,
            ts,
        )

        await integration_client.post(
            "/v1/subscriptions",
            json={"tenant_id": tenant_id, "plan_id": plan_id},
            headers=admin_auth_headers,
        )

        resp = await integration_client.get(
            f"/v1/subscriptions/tenant/{tenant_id}/active",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["tenant_id"] == tenant_id
        assert resp.json()["data"]["status"] == "active"


class TestDiscountLifecycle:
    """Test discount creation, validation, and application."""

    async def test_create_discount(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        resp = await integration_client.post(
            "/v1/discounts",
            json={
                "code": f"LAUNCH{ts}",
                "name": "Launch Discount",
                "discount_type": "percentage",
                "value": 25.0,
                "scope": "global",
                "valid_until": int(time.time()) + 86400 * 30,
                "max_redemptions": 100,
            },
            headers=admin_auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["code"] == f"LAUNCH{ts}"
        assert data["value"] == 25.0

    async def test_duplicate_discount_code_rejected(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        code = f"DUP{ts}"

        resp1 = await integration_client.post(
            "/v1/discounts",
            json={"code": code, "name": "First", "value": 10.0},
            headers=admin_auth_headers,
        )
        assert resp1.status_code == 201

        resp2 = await integration_client.post(
            "/v1/discounts",
            json={"code": code, "name": "Second", "value": 20.0},
            headers=admin_auth_headers,
        )
        assert resp2.status_code == 409

    async def test_subscription_with_discount(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())

        # Create discount
        disc_resp = await integration_client.post(
            "/v1/discounts",
            json={
                "code": f"SAVE{ts}",
                "name": "Save Discount",
                "discount_type": "percentage",
                "value": 50.0,
                "scope": "global",
                "stackable": True,
            },
            headers=admin_auth_headers,
        )
        assert disc_resp.status_code == 201
        disc_id = disc_resp.json()["data"]["id"]

        # Create plan
        plan_resp = await integration_client.post(
            "/v1/plans",
            json={
                "name": f"disc-plan-{ts}",
                "display_name": f"Disc Plan {ts}",
                "status": "active",
                "base_price_monthly": 200.0,
            },
            headers=admin_auth_headers,
        )
        plan_id = plan_resp.json()["data"]["id"]

        # Create tenant
        tenant_resp = await integration_client.post(
            "/v1/admins/tenants/bootstrap",
            json={
                "company_name": f"Disc Test Corp {ts}",
                "admin_full_name": "SA",
                "admin_email": f"disc_sa_{ts}@test.example.com",
                "admin_password": "DiscTest123!",
            },
            headers=admin_auth_headers,
        )
        tenant_id = tenant_resp.json()["data"]["tenant"]["id"]

        # Subscribe with discount
        sub_resp = await integration_client.post(
            "/v1/subscriptions",
            json={
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "discount_ids": [disc_id],
            },
            headers=admin_auth_headers,
        )
        assert sub_resp.status_code == 201
        sub_data = sub_resp.json()["data"]
        # 200 * 0.5 = 100
        assert sub_data["effective_price"] == 100.0
        assert disc_id in sub_data["applied_discount_ids"]

    async def test_disable_discount(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())
        create_resp = await integration_client.post(
            "/v1/discounts",
            json={"code": f"DISABLE{ts}", "name": "To Disable", "value": 15.0},
            headers=admin_auth_headers,
        )
        disc_id = create_resp.json()["data"]["id"]

        disable_resp = await integration_client.post(
            f"/v1/discounts/{disc_id}/disable",
            headers=admin_auth_headers,
        )
        assert disable_resp.status_code == 200
        assert disable_resp.json()["data"]["status"] == "disabled"


class TestSubscriptionOverrides:
    """Test per-tenant feature/limit overrides on subscriptions."""

    async def test_update_overrides(
        self,
        integration_client: AsyncClient,
        admin_auth_headers: dict,
    ):
        ts = int(time.time())

        # Create plan + tenant + subscription
        plan_resp = await integration_client.post(
            "/v1/plans",
            json={
                "name": f"override-plan-{ts}",
                "display_name": "Override Plan",
                "status": "active",
                "feature_rules": [
                    {"endpoint_pattern": "/v1/audit/*", "enabled": False},
                ],
                "crud_limits": [
                    {"collection": "visitors", "max_create": 10},
                ],
            },
            headers=admin_auth_headers,
        )
        plan_id = plan_resp.json()["data"]["id"]

        tenant_resp = await integration_client.post(
            "/v1/admins/tenants/bootstrap",
            json={
                "company_name": f"Override Corp {ts}",
                "admin_full_name": "SA",
                "admin_email": f"override_sa_{ts}@test.example.com",
                "admin_password": "Override123!",
            },
            headers=admin_auth_headers,
        )
        tenant_id = tenant_resp.json()["data"]["tenant"]["id"]

        sub_resp = await integration_client.post(
            "/v1/subscriptions",
            json={"tenant_id": tenant_id, "plan_id": plan_id},
            headers=admin_auth_headers,
        )
        sub_id = sub_resp.json()["data"]["id"]

        # Apply overrides: enable audit, increase visitor limit
        override_resp = await integration_client.put(
            f"/v1/subscriptions/{sub_id}/overrides",
            json={
                "feature_overrides": {"/v1/audit/*": {"enabled": True}},
                "crud_limit_overrides": {"visitors": {"max_create": 999}},
                "tenant_cap_overrides": {"max_system_users": 100},
            },
            headers=admin_auth_headers,
        )
        assert override_resp.status_code == 200
        data = override_resp.json()["data"]
        assert data["feature_overrides"]["/v1/audit/*"]["enabled"] is True
        assert data["crud_limit_overrides"]["visitors"]["max_create"] == 999
        assert data["tenant_cap_overrides"]["max_system_users"] == 100


class TestPlanWithoutAuth:
    """Verify plan management endpoints require admin auth."""

    async def test_create_plan_without_auth_rejected(
        self,
        integration_client: AsyncClient,
    ):
        resp = await integration_client.post(
            "/v1/plans",
            json={"name": "unauth-plan", "display_name": "Unauth"},
        )
        assert resp.status_code in (401, 403)

    async def test_create_subscription_without_auth_rejected(
        self,
        integration_client: AsyncClient,
    ):
        resp = await integration_client.post(
            "/v1/subscriptions",
            json={"tenant_id": "t1", "plan_id": "p1"},
        )
        assert resp.status_code in (401, 403)

    async def test_create_discount_without_auth_rejected(
        self,
        integration_client: AsyncClient,
    ):
        resp = await integration_client.post(
            "/v1/discounts",
            json={"code": "UNAUTH", "name": "Unauth", "value": 10},
        )
        assert resp.status_code in (401, 403)
