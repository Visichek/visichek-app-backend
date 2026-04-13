"""
Unit tests for plan, subscription, discount, and usage services.
Uses mocking to isolate service logic from database.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from schemas.plan_schema import (
    PlanCreate,
    PlanUpdate,
    PlanOut,
    PlanStatus,
    PlanTier,
    QuotaResetInterval,
)
from schemas.subscription_schema import (
    SubscriptionOut,
    SubscriptionStatus,
    BillingCycle,
)
from schemas.discount_schema import (
    DiscountCreate,
    DiscountOut,
    DiscountType,
    DiscountScope,
    DiscountStatus,
)

pytestmark = pytest.mark.asyncio


# ============================================================================
# Helper factories
# ============================================================================


def _make_plan_out(**overrides) -> PlanOut:
    defaults = {
        "_id": "plan_123",
        "name": "test-plan",
        "display_name": "Test Plan",
        "tier": PlanTier.PROFESSIONAL.value,
        "status": PlanStatus.ACTIVE.value,
        "base_price_monthly": 100.0,
        "base_price_yearly": 1000.0,
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


def _make_sub_out(**overrides) -> SubscriptionOut:
    defaults = {
        "_id": "sub_123",
        "tenant_id": "tenant_abc",
        "plan_id": "plan_123",
        "status": SubscriptionStatus.ACTIVE.value,
        "billing_cycle": BillingCycle.MONTHLY.value,
        "effective_price": 100.0,
        "currency": "NGN",
        "current_period_start": int(time.time()),
        "current_period_end": int(time.time()) + 2592000,
        "applied_discount_ids": [],
        "date_created": int(time.time()),
        "last_updated": int(time.time()),
    }
    defaults.update(overrides)
    return SubscriptionOut(**defaults)  # type: ignore[arg-type]


def _make_discount_out(**overrides) -> DiscountOut:
    defaults = {
        "_id": "disc_123",
        "code": "TEST50",
        "name": "Test Discount",
        "discount_type": DiscountType.PERCENTAGE.value,
        "value": 50.0,
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
# Plan Service Tests
# ============================================================================


class TestPlanService:
    @patch("services.plan_service.create_plan", new_callable=AsyncMock)
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_add_plan_success(self, mock_get, mock_create):
        mock_get.return_value = None  # no duplicate
        expected = _make_plan_out()
        mock_create.return_value = expected

        from services.plan_service import add_plan

        result = await add_plan(PlanCreate(name="test-plan", display_name="Test Plan"))
        assert result.name == "test-plan"
        mock_create.assert_called_once()

    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_add_plan_duplicate_rejected(self, mock_get):
        mock_get.return_value = _make_plan_out()  # already exists

        from services.plan_service import add_plan
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await add_plan(PlanCreate(name="test-plan", display_name="Test Plan"))
        assert exc_info.value.status_code == 409

    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_retrieve_plan_by_id(self, mock_get):
        expected = _make_plan_out()
        mock_get.return_value = expected

        from services.plan_service import retrieve_plan_by_id

        result = await retrieve_plan_by_id("plan_123")
        # invalid ObjectId returns None
        assert result is None  # "plan_123" is not a valid ObjectId

    @patch("services.plan_service.update_plan", new_callable=AsyncMock)
    async def test_update_plan_by_id_invalid_id(self, mock_update):
        from services.plan_service import update_plan_by_id

        result = await update_plan_by_id("not-valid", PlanUpdate(name="new"))
        assert result is None
        mock_update.assert_not_called()

    @patch("services.plan_service.retrieve_plan_by_id", new_callable=AsyncMock)
    @patch("services.plan_service.update_plan_by_id", new_callable=AsyncMock)
    async def test_archive_plan(self, mock_update, mock_retrieve):
        expected = _make_plan_out(status=PlanStatus.ARCHIVED.value)
        mock_update.return_value = expected

        from services.plan_service import archive_plan

        await archive_plan("plan_123")
        mock_update.assert_called_once()

    @patch("services.plan_service.retrieve_plan_by_id", new_callable=AsyncMock)
    async def test_activate_plan_from_archived_rejected(self, mock_retrieve):
        mock_retrieve.return_value = _make_plan_out(status=PlanStatus.ARCHIVED.value)

        from services.plan_service import activate_plan
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await activate_plan("507f1f77bcf86cd799439011")
        assert exc_info.value.status_code == 400

    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    @patch("services.plan_service.retrieve_plan_by_id", new_callable=AsyncMock)
    @patch("services.plan_service.create_plan", new_callable=AsyncMock)
    async def test_clone_plan_success(self, mock_create, mock_retrieve, mock_get):
        source = _make_plan_out(name="source-plan")
        mock_retrieve.return_value = source
        mock_get.return_value = None  # no name conflict
        cloned = _make_plan_out(name="cloned-plan", _id="plan_456")
        mock_create.return_value = cloned

        from services.plan_service import clone_plan

        result = await clone_plan(
            "507f1f77bcf86cd799439011", "cloned-plan", "Cloned Plan"
        )
        assert result.name == "cloned-plan"

    @patch("services.plan_service.retrieve_plan_by_id", new_callable=AsyncMock)
    async def test_remove_plan_non_draft_rejected(self, mock_retrieve):
        mock_retrieve.return_value = _make_plan_out(status=PlanStatus.ACTIVE.value)

        from services.plan_service import remove_plan
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await remove_plan("507f1f77bcf86cd799439011")
        assert exc_info.value.status_code == 400


# ============================================================================
# Subscription Service Tests
# ============================================================================


class TestSubscriptionService:
    @patch(
        "services.subscription_service.invalidate_tenant_plan_cache",
        new_callable=AsyncMock,
    )
    @patch("services.subscription_service.create_subscription", new_callable=AsyncMock)
    @patch("services.subscription_service.get_subscription", new_callable=AsyncMock)
    @patch("services.subscription_service.get_plan", new_callable=AsyncMock)
    async def test_subscribe_tenant_success(
        self, mock_plan, mock_sub, mock_create, mock_cache
    ):
        mock_plan.return_value = _make_plan_out()
        mock_sub.return_value = None  # no existing subscription
        expected = _make_sub_out()
        mock_create.return_value = expected

        from services.subscription_service import subscribe_tenant

        result = await subscribe_tenant("tenant_abc", "507f1f77bcf86cd799439011")
        assert result.tenant_id == "tenant_abc"
        mock_cache.assert_called_once_with("tenant_abc")

    @patch("services.subscription_service.get_subscription", new_callable=AsyncMock)
    @patch("services.subscription_service.get_plan", new_callable=AsyncMock)
    async def test_subscribe_tenant_duplicate_rejected(self, mock_plan, mock_sub):
        mock_plan.return_value = _make_plan_out()
        mock_sub.return_value = _make_sub_out()  # already subscribed

        from services.subscription_service import subscribe_tenant
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await subscribe_tenant("tenant_abc", "507f1f77bcf86cd799439011")
        assert exc_info.value.status_code == 409

    @patch("services.subscription_service.get_plan", new_callable=AsyncMock)
    async def test_subscribe_tenant_inactive_plan_rejected(self, mock_plan):
        mock_plan.return_value = _make_plan_out(status=PlanStatus.ARCHIVED.value)

        from services.subscription_service import subscribe_tenant
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await subscribe_tenant("tenant_abc", "507f1f77bcf86cd799439011")
        assert exc_info.value.status_code == 400

    @patch(
        "services.subscription_service.invalidate_tenant_plan_cache",
        new_callable=AsyncMock,
    )
    @patch("services.subscription_service.update_subscription", new_callable=AsyncMock)
    @patch("services.subscription_service.get_discount", new_callable=AsyncMock)
    @patch("services.subscription_service.get_plan", new_callable=AsyncMock)
    @patch(
        "services.subscription_service.retrieve_tenant_active_subscription",
        new_callable=AsyncMock,
    )
    async def test_change_plan_success(
        self, mock_active, mock_plan, mock_disc, mock_update, mock_cache
    ):
        mock_active.return_value = _make_sub_out()
        mock_plan.return_value = _make_plan_out(_id="plan_999", name="new-plan")
        mock_update.return_value = _make_sub_out(plan_id="plan_999")

        from services.subscription_service import change_plan

        await change_plan("tenant_abc", "507f1f77bcf86cd799439011")
        mock_update.assert_called_once()
        mock_cache.assert_called_once_with("tenant_abc")

    @patch(
        "services.subscription_service.retrieve_tenant_active_subscription",
        new_callable=AsyncMock,
    )
    async def test_cancel_subscription_no_active(self, mock_active):
        mock_active.return_value = None

        from services.subscription_service import cancel_subscription
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await cancel_subscription("tenant_abc")
        assert exc_info.value.status_code == 404


# ============================================================================
# Discount Service Tests
# ============================================================================


class TestDiscountService:
    @patch("services.discount_service.create_discount", new_callable=AsyncMock)
    @patch("services.discount_service.get_discount", new_callable=AsyncMock)
    async def test_add_discount_success(self, mock_get, mock_create):
        mock_get.return_value = None  # no duplicate
        expected = _make_discount_out()
        mock_create.return_value = expected

        from services.discount_service import add_discount

        result = await add_discount(
            DiscountCreate(
                code="TEST50",
                name="Test",
                value=50.0,
            )
        )
        assert result.code == "TEST50"

    @patch("services.discount_service.get_discount", new_callable=AsyncMock)
    async def test_add_discount_duplicate_rejected(self, mock_get):
        mock_get.return_value = _make_discount_out()

        from services.discount_service import add_discount
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await add_discount(
                DiscountCreate(
                    code="TEST50",
                    name="Test",
                    value=50.0,
                )
            )
        assert exc_info.value.status_code == 409

    @patch(
        "services.discount_service.retrieve_discount_by_code", new_callable=AsyncMock
    )
    async def test_validate_discount_expired(self, mock_get):
        expired = _make_discount_out(valid_until=int(time.time()) - 3600)
        mock_get.return_value = expired

        from services.discount_service import validate_discount_code
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await validate_discount_code("TEST50", "t1", "p1", 100.0)
        assert exc_info.value.status_code == 400

    @patch(
        "services.discount_service.retrieve_discount_by_code", new_callable=AsyncMock
    )
    async def test_validate_discount_max_redemptions(self, mock_get):
        maxed = _make_discount_out(max_redemptions=5, current_redemptions=5)
        mock_get.return_value = maxed

        from services.discount_service import validate_discount_code
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await validate_discount_code("TEST50", "t1", "p1", 100.0)
        assert exc_info.value.status_code == 400

    @patch("services.discount_service.retrieve_discount_by_id", new_callable=AsyncMock)
    async def test_remove_active_discount_rejected(self, mock_get):
        mock_get.return_value = _make_discount_out(status=DiscountStatus.ACTIVE.value)

        from services.discount_service import remove_discount
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await remove_discount("507f1f77bcf86cd799439011")
        assert exc_info.value.status_code == 400


# ============================================================================
# Usage Service Tests
# ============================================================================


class TestUsageService:
    def test_get_period_key_monthly(self):
        from services.usage_service import get_period_key

        key = get_period_key(QuotaResetInterval.MONTHLY)
        assert len(key) == 7  # "YYYY-MM"
        assert "-" in key

    def test_get_period_key_daily(self):
        from services.usage_service import get_period_key

        key = get_period_key(QuotaResetInterval.DAILY)
        assert len(key) == 10  # "YYYY-MM-DD"

    def test_get_period_key_weekly(self):
        from services.usage_service import get_period_key

        key = get_period_key(QuotaResetInterval.WEEKLY)
        assert "-W" in key

    def test_get_period_key_never(self):
        from services.usage_service import get_period_key

        key = get_period_key(QuotaResetInterval.NEVER)
        assert key == "lifetime"

    @patch("services.usage_service.get_current_count", new_callable=AsyncMock)
    async def test_check_quota_unlimited(self, mock_count):
        from services.usage_service import check_quota

        allowed, current, limit = await check_quota(
            "t1",
            "s1",
            "visitors",
            "create",
            None,
            QuotaResetInterval.MONTHLY,
        )
        assert allowed is True
        assert limit is None
        mock_count.assert_not_called()

    @patch("services.usage_service.get_current_count", new_callable=AsyncMock)
    async def test_check_quota_under_limit(self, mock_count):
        mock_count.return_value = 50

        from services.usage_service import check_quota

        allowed, current, limit = await check_quota(
            "t1",
            "s1",
            "visitors",
            "create",
            100,
            QuotaResetInterval.MONTHLY,
        )
        assert allowed is True
        assert current == 50
        assert limit == 100

    @patch("services.usage_service.get_current_count", new_callable=AsyncMock)
    async def test_check_quota_exceeded(self, mock_count):
        mock_count.return_value = 100

        from services.usage_service import check_quota

        allowed, current, limit = await check_quota(
            "t1",
            "s1",
            "visitors",
            "create",
            100,
            QuotaResetInterval.MONTHLY,
        )
        assert allowed is False
        assert current == 100
        assert limit == 100


# ============================================================================
# Price Calculation Tests
# ============================================================================


class TestPriceCalculation:
    def test_percentage_discount(self):
        from services.subscription_service import _calculate_effective_price

        plan = _make_plan_out(base_price_monthly=200.0)
        discounts = [
            _make_discount_out(discount_type=DiscountType.PERCENTAGE.value, value=25.0)
        ]
        price = _calculate_effective_price(plan, BillingCycle.MONTHLY, discounts)
        assert price == 150.0

    def test_fixed_discount(self):
        from services.subscription_service import _calculate_effective_price

        plan = _make_plan_out(base_price_monthly=200.0)
        discounts = [
            _make_discount_out(discount_type=DiscountType.FIXED.value, value=50.0)
        ]
        price = _calculate_effective_price(plan, BillingCycle.MONTHLY, discounts)
        assert price == 150.0

    def test_stacked_discounts(self):
        from services.subscription_service import _calculate_effective_price

        plan = _make_plan_out(base_price_monthly=1000.0)
        discounts = [
            _make_discount_out(discount_type=DiscountType.PERCENTAGE.value, value=20.0),
            _make_discount_out(discount_type=DiscountType.FIXED.value, value=100.0),
        ]
        # 1000 * 0.8 = 800, 800 - 100 = 700
        price = _calculate_effective_price(plan, BillingCycle.MONTHLY, discounts)
        assert price == 700.0

    def test_discount_cannot_go_below_zero(self):
        from services.subscription_service import _calculate_effective_price

        plan = _make_plan_out(base_price_monthly=50.0)
        discounts = [
            _make_discount_out(discount_type=DiscountType.FIXED.value, value=100.0)
        ]
        price = _calculate_effective_price(plan, BillingCycle.MONTHLY, discounts)
        assert price == 0.0

    def test_yearly_billing_cycle(self):
        from services.subscription_service import _calculate_effective_price

        plan = _make_plan_out(base_price_yearly=1200.0)
        price = _calculate_effective_price(plan, BillingCycle.YEARLY, [])
        assert price == 1200.0

    def test_100_percent_discount(self):
        from services.subscription_service import _calculate_effective_price

        plan = _make_plan_out(base_price_monthly=500.0)
        discounts = [
            _make_discount_out(discount_type=DiscountType.PERCENTAGE.value, value=100.0)
        ]
        price = _calculate_effective_price(plan, BillingCycle.MONTHLY, discounts)
        assert price == 0.0
