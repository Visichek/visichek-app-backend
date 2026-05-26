"""
Unit tests for plan, subscription, discount, and usage route endpoints.
Uses httpx AsyncClient with ASGITransport to test route-level behavior.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import app
from schemas.plan_schema import PlanOut, PlanStatus, PlanTier
from schemas.subscription_schema import (
    SubscriptionOut,
    SubscriptionStatus,
    BillingCycle,
)
from schemas.discount_schema import (
    DiscountOut,
    DiscountType,
    DiscountScope,
    DiscountStatus,
)
from security.account_status_check import check_admin_account_status_and_permissions

pytestmark = pytest.mark.asyncio


# Mock admin object for dependency override
MOCK_ADMIN = type(
    "MockAdmin",
    (),
    {
        "id": "admin_mock",
        "email": "admin@test.com",
        "full_name": "Mock Admin",
        "role": "admin",
    },
)()


@pytest_asyncio.fixture
async def client():
    app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
        MOCK_ADMIN
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ============================================================================
# Helper factories
# ============================================================================


def _plan_out(**overrides) -> PlanOut:
    defaults = {
        "_id": "plan_test",
        "name": "test-plan",
        "display_name": "Test Plan",
        "tier": PlanTier.FREE.value,
        "status": PlanStatus.ACTIVE.value,
        "base_price_monthly": 0,
        "base_price_yearly": 0,
        "currency": "NGN",
        "feature_rules": [],
        "crud_limits": [],
        "retrieval_quotas": [],
        "storage_limits": {},
        "tenant_caps": {},
        "priority_support": False,
        "custom_branding": False,
        "api_access": False,
        "is_public": True,
        "sort_order": 0,
        "date_created": int(time.time()),
        "last_updated": int(time.time()),
    }
    defaults.update(overrides)
    return PlanOut(**defaults)  # type: ignore[arg-type]


def _sub_out(**overrides) -> SubscriptionOut:
    defaults = {
        "_id": "sub_test",
        "tenant_id": "tenant_abc",
        "plan_id": "plan_test",
        "status": SubscriptionStatus.ACTIVE.value,
        "billing_cycle": BillingCycle.MONTHLY.value,
        "effective_price": 100.0,
        "currency": "NGN",
        "current_period_start": int(time.time()),
        "applied_discount_ids": [],
        "date_created": int(time.time()),
        "last_updated": int(time.time()),
    }
    defaults.update(overrides)
    return SubscriptionOut(**defaults)  # type: ignore[arg-type]


def _discount_out(**overrides) -> DiscountOut:
    defaults = {
        "_id": "disc_test",
        "code": "TESTCODE",
        "name": "Test Discount",
        "discount_type": DiscountType.PERCENTAGE.value,
        "value": 10.0,
        "scope": DiscountScope.GLOBAL.value,
        "status": DiscountStatus.ACTIVE.value,
        "target_plan_ids": [],
        "current_redemptions": 0,
        "stackable": False,
        "date_created": int(time.time()),
        "last_updated": int(time.time()),
    }
    defaults.update(overrides)
    return DiscountOut(**defaults)  # type: ignore[arg-type]


# ============================================================================
# Plan Route Tests
# ============================================================================


class TestPlanRoutes:
    @patch("api.v1.plan_route.enqueue_write", new_callable=AsyncMock)
    async def test_create_plan(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "plan_test",
            "job_id": "job-plan-create",
            "status": "queued",
        }
        resp = await client.post(
            "/v1/plans",
            json={
                "name": "test-plan",
                "display_name": "Test Plan",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["jobId"] == "job-plan-create"
        mock_enqueue.assert_awaited_once()
        assert mock_enqueue.await_args.kwargs["writer_key"] == "plan.create"

    @patch("api.v1.plan_route.retrieve_plans", new_callable=AsyncMock)
    async def test_list_plans(self, mock_list, client):
        mock_list.return_value = [_plan_out(), _plan_out(_id="plan2", name="plan-2")]
        resp = await client.get("/v1/plans")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 2

    @patch("api.v1.plan_route.retrieve_plan_by_id", new_callable=AsyncMock)
    async def test_get_plan(self, mock_get, client):
        mock_get.return_value = _plan_out()
        resp = await client.get("/v1/plans/plan_test")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "test-plan"

    @patch("api.v1.plan_route.enqueue_write", new_callable=AsyncMock)
    async def test_update_plan(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "plan_test",
            "job_id": "job-plan-update",
            "status": "queued",
        }
        resp = await client.put(
            "/v1/plans/plan_test",
            json={
                "display_name": "Updated Plan",
            },
        )
        assert resp.status_code == 202
        assert resp.json()["data"]["jobId"] == "job-plan-update"
        mock_enqueue.assert_awaited_once()
        assert mock_enqueue.await_args.kwargs["writer_key"] == "plan.update"

    @patch("api.v1.plan_route.enqueue_write", new_callable=AsyncMock)
    async def test_archive_plan(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "plan_test",
            "job_id": "job-plan-archive",
            "status": "queued",
        }
        resp = await client.post("/v1/plans/plan_test/archive")
        assert resp.status_code == 202
        assert resp.json()["data"]["jobId"] == "job-plan-archive"
        assert mock_enqueue.await_args.kwargs["writer_key"] == "plan.archive"

    @patch("api.v1.plan_route.enqueue_write", new_callable=AsyncMock)
    async def test_activate_plan(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "plan_test",
            "job_id": "job-plan-activate",
            "status": "queued",
        }
        resp = await client.post("/v1/plans/plan_test/activate")
        assert resp.status_code == 202
        assert resp.json()["data"]["jobId"] == "job-plan-activate"
        assert mock_enqueue.await_args.kwargs["writer_key"] == "plan.activate"

    @patch("api.v1.plan_route.enqueue_write", new_callable=AsyncMock)
    async def test_delete_plan(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "plan_test",
            "job_id": "job-plan-delete",
            "status": "queued",
        }
        resp = await client.delete("/v1/plans/plan_test")
        assert resp.status_code == 202
        assert resp.json()["data"]["jobId"] == "job-plan-delete"
        assert mock_enqueue.await_args.kwargs["writer_key"] == "plan.delete"


# ============================================================================
# Subscription Route Tests
# ============================================================================


class TestSubscriptionRoutes:
    @patch("api.v1.subscription_route.enqueue_write_inline", new_callable=AsyncMock)
    async def test_create_subscription(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "sub_test",
            "job_id": "job-sub-create",
            "status": "queued",
        }
        resp = await client.post(
            "/v1/subscriptions",
            json={
                "tenant_id": "tenant_abc",
                "plan_id": "plan_test",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["jobId"] == "job-sub-create"
        assert mock_enqueue.await_args.kwargs["writer_key"] == "subscription.create"

    @patch(
        "api.v1.subscription_route.retrieve_subscriptions_with_details",
        new_callable=AsyncMock,
    )
    async def test_list_subscriptions(self, mock_list, client):
        mock_list.return_value = [_sub_out()]
        resp = await client.get("/v1/subscriptions")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @patch(
        "api.v1.subscription_route.retrieve_subscription_by_id", new_callable=AsyncMock
    )
    async def test_get_subscription(self, mock_get, client):
        mock_get.return_value = _sub_out()
        resp = await client.get("/v1/subscriptions/sub_test")
        assert resp.status_code == 200
        assert (
            resp.json()["data"].get("plan_id") or resp.json()["data"].get("planId")
        ) == "plan_test"

    @patch("api.v1.subscription_route.enqueue_write_inline", new_callable=AsyncMock)
    async def test_change_plan(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "sub_test",
            "job_id": "job-sub-change",
            "status": "queued",
        }
        resp = await client.post(
            "/v1/subscriptions/change-plan",
            json={
                "tenant_id": "tenant_abc",
                "new_plan_id": "plan_new",
            },
        )
        assert resp.status_code == 202
        assert resp.json()["data"]["jobId"] == "job-sub-change"
        assert (
            mock_enqueue.await_args.kwargs["writer_key"] == "subscription.change_plan"
        )

    @patch("api.v1.subscription_route.enqueue_write", new_callable=AsyncMock)
    async def test_cancel_subscription(self, mock_enqueue, client):
        # The cancel route authorises via verify_any_token (app admins → any
        # tenant; super_admins → own tenant), not the admin-permission gate the
        # client fixture overrides. Override it with an app-admin principal.
        from security.auth import verify_any_token
        from security.principal import AuthPrincipal

        app.dependency_overrides[verify_any_token] = lambda: AuthPrincipal(
            user_id="admin1",
            role="admin",
            access_token_id="tok",
            jwt_token="tok",
        )
        mock_enqueue.return_value = {
            "id": "sub_test",
            "job_id": "job-sub-cancel",
            "status": "queued",
        }
        resp = await client.post(
            "/v1/subscriptions/cancel",
            json={
                "tenant_id": "tenant_abc",
                "immediate": True,
            },
        )
        assert resp.status_code == 202
        assert resp.json()["data"]["jobId"] == "job-sub-cancel"
        assert mock_enqueue.await_args.kwargs["writer_key"] == "subscription.cancel"


# ============================================================================
# Discount Route Tests
# ============================================================================


class TestDiscountRoutes:
    @patch("api.v1.discount_route.enqueue_write", new_callable=AsyncMock)
    async def test_create_discount(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "disc_test",
            "job_id": "job-disc-create",
            "status": "queued",
        }
        resp = await client.post(
            "/v1/discounts",
            json={
                "code": "TESTCODE",
                "name": "Test Discount",
                "value": 10.0,
            },
        )
        assert resp.status_code == 202
        assert resp.json()["data"]["jobId"] == "job-disc-create"
        assert mock_enqueue.await_args.kwargs["writer_key"] == "discount.create"

    @patch("api.v1.discount_route.retrieve_discounts", new_callable=AsyncMock)
    async def test_list_discounts(self, mock_list, client):
        mock_list.return_value = [_discount_out()]
        resp = await client.get("/v1/discounts")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @patch("api.v1.discount_route.retrieve_discount_by_id", new_callable=AsyncMock)
    async def test_get_discount(self, mock_get, client):
        mock_get.return_value = _discount_out()
        resp = await client.get("/v1/discounts/disc_test")
        assert resp.status_code == 200
        assert resp.json()["data"]["value"] == 10.0

    @patch("api.v1.discount_route.enqueue_write", new_callable=AsyncMock)
    async def test_disable_discount(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "disc_test",
            "job_id": "job-disc-disable",
            "status": "queued",
        }
        resp = await client.post("/v1/discounts/disc_test/disable")
        assert resp.status_code == 202
        assert resp.json()["data"]["jobId"] == "job-disc-disable"
        assert mock_enqueue.await_args.kwargs["writer_key"] == "discount.disable"

    @patch("api.v1.discount_route.enqueue_write", new_callable=AsyncMock)
    @patch("api.v1.discount_route.enqueue_bulk_write", new_callable=AsyncMock)
    async def test_bulk_disable_discount_uses_bulk_writer(
        self, mock_bulk_enqueue, mock_single_enqueue, client
    ):
        ids = [
            "507f1f77bcf86cd799439011",
            "507f1f77bcf86cd799439012",
        ]
        mock_bulk_enqueue.return_value = {
            "id": "507f1f77bcf86cd799439099",
            "job_id": "job-disc-bulk-disable",
            "status": "queued",
        }

        resp = await client.post("/v1/discounts/bulk/disable", json={"ids": ids})

        assert resp.status_code == 202
        assert resp.json()["message"] == "Bulk discount disable queued"
        assert resp.json()["data"]["jobId"] == "job-disc-bulk-disable"
        mock_bulk_enqueue.assert_awaited_once()
        mock_single_enqueue.assert_not_awaited()
        assert (
            mock_bulk_enqueue.await_args.kwargs["writer_key"] == "discount.bulk_disable"
        )
        assert mock_bulk_enqueue.await_args.kwargs["ids"] == ids

    @patch("api.v1.discount_route.enqueue_write", new_callable=AsyncMock)
    async def test_delete_discount(self, mock_enqueue, client):
        mock_enqueue.return_value = {
            "id": "disc_test",
            "job_id": "job-disc-delete",
            "status": "queued",
        }
        resp = await client.delete("/v1/discounts/disc_test")
        assert resp.status_code == 202
        assert resp.json()["data"]["jobId"] == "job-disc-delete"
        assert mock_enqueue.await_args.kwargs["writer_key"] == "discount.delete"
