"""
Unit tests for dunning service with mocked repositories and providers.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from schemas.plan_schema import PlanOut, PlanTier, PlanStatus
from schemas.subscription_schema import (
    SubscriptionOut,
    SubscriptionStatus,
    BillingCycle,
)
from core.payments import PaymentIntent
from core.settings import Settings

pytestmark = pytest.mark.asyncio


def _make_plan_out(**overrides) -> PlanOut:
    """Factory for PlanOut test objects."""
    defaults = {
        "_id": "plan_123",
        "name": "professional",
        "display_name": "Professional Plan",
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
    return PlanOut(**defaults)


def _make_subscription_out(**overrides) -> SubscriptionOut:
    """Factory for SubscriptionOut test objects."""
    now = int(time.time())
    defaults = {
        "_id": "sub_123",
        "tenant_id": "tenant_abc",
        "plan_id": "plan_123",
        "status": SubscriptionStatus.PAST_DUE.value,
        "billing_cycle": BillingCycle.MONTHLY.value,
        "effective_price": 100.0,
        "currency": "NGN",
        "current_period_start": now - 86400,
        "current_period_end": now + 2505600,
        "renewal_attempts": 0,
        "next_retry_at": now,
        "applied_discount_ids": [],
        "date_created": now,
        "last_updated": now,
    }
    defaults.update(overrides)
    return SubscriptionOut(**defaults)


class TestDunningService:
    """Tests for dunning service functions."""

    @patch("services.dunning_service.get_settings")
    @patch("services.dunning_service.get_subscriptions", new_callable=AsyncMock)
    async def test_process_dunning_no_past_due(self, mock_get_subs, mock_settings):
        """Test dunning with no subscriptions in PAST_DUE status."""
        mock_get_subs.return_value = []
        mock_settings.return_value = MagicMock(max_dunning_attempts=5)

        from services.dunning_service import process_dunning

        result = await process_dunning()

        assert result["retried_count"] == 0
        assert result["suspended_count"] == 0
        assert result["failed_count"] == 0

    @patch("services.dunning_service.get_settings")
    @patch("services.dunning_service.invalidate_tenant_plan_cache", new_callable=AsyncMock)
    @patch("services.dunning_service.generate_invoice", new_callable=AsyncMock)
    @patch("services.dunning_service.update_subscription", new_callable=AsyncMock)
    @patch("services.dunning_service.get_plan", new_callable=AsyncMock)
    @patch("services.dunning_service.PaymentManager")
    @patch("services.dunning_service.get_subscriptions", new_callable=AsyncMock)
    async def test_process_dunning_retry_success(
        self,
        mock_get_subs,
        mock_payment_mgr,
        mock_get_plan,
        mock_update_sub,
        mock_generate_invoice,
        mock_invalidate_cache,
        mock_settings,
    ):
        """Test successful dunning retry that resets to ACTIVE."""
        now = int(time.time())
        sub = _make_subscription_out(
            **{
                "_id": "sub_dunning_1",
                "status": SubscriptionStatus.PAST_DUE.value,
                "renewal_attempts": 1,
                "next_retry_at": now - 3600,
            }
        )
        mock_get_subs.return_value = [sub]

        mock_settings_instance = MagicMock()
        mock_settings_instance.max_dunning_attempts = 5
        mock_settings_instance.dunning_retry_days = [1, 3, 7, 14]
        mock_settings.return_value = mock_settings_instance

        plan = _make_plan_out()
        mock_get_plan.return_value = plan

        # Mock payment success
        mock_provider = MagicMock()
        mock_intent = PaymentIntent(
            id="intent_dunning_1",
            status="succeeded",
            amount_minor=10000,
            currency="NGN",
            provider_payload={"id": "txn_dunning_1"},
        )
        mock_provider.create_intent.return_value = mock_intent
        mock_payment_mgr_instance = MagicMock()
        mock_payment_mgr_instance.get_provider.return_value = mock_provider
        mock_payment_mgr.get_instance.return_value = mock_payment_mgr_instance

        updated_sub = _make_subscription_out(
            **{
                "_id": "sub_dunning_1",
                "status": SubscriptionStatus.ACTIVE.value,
                "renewal_attempts": 0,
            }
        )
        mock_update_sub.return_value = updated_sub

        from services.dunning_service import process_dunning

        result = await process_dunning()

        assert result["retried_count"] == 1
        assert result["suspended_count"] == 0
        mock_generate_invoice.assert_called_once()

    @patch("services.dunning_service.get_settings")
    @patch("services.dunning_service._queue_suspension_email", new_callable=AsyncMock)
    @patch("services.dunning_service.update_subscription", new_callable=AsyncMock)
    @patch("services.dunning_service.get_subscriptions", new_callable=AsyncMock)
    async def test_process_dunning_suspend_max_attempts(
        self,
        mock_get_subs,
        mock_update_sub,
        mock_queue_email,
        mock_settings,
    ):
        """Test suspension of subscription after max dunning attempts reached."""
        now = int(time.time())
        sub = _make_subscription_out(
            **{
                "_id": "sub_max_attempts",
                "status": SubscriptionStatus.PAST_DUE.value,
                "renewal_attempts": 5,  # Reached max
                "next_retry_at": now - 3600,
            }
        )
        mock_get_subs.return_value = [sub]

        mock_settings_instance = MagicMock()
        mock_settings_instance.max_dunning_attempts = 5
        mock_settings.return_value = mock_settings_instance

        updated_sub = _make_subscription_out(
            **{
                "_id": "sub_max_attempts",
                "status": SubscriptionStatus.SUSPENDED.value,
            }
        )
        mock_update_sub.return_value = updated_sub

        from services.dunning_service import process_dunning

        result = await process_dunning()

        assert result["retried_count"] == 0
        assert result["suspended_count"] == 1
        mock_queue_email.assert_called_once()
        mock_update_sub.assert_called_once()

    @patch("services.dunning_service.get_settings")
    @patch("services.dunning_service._queue_dunning_email", new_callable=AsyncMock)
    @patch("services.dunning_service.update_subscription", new_callable=AsyncMock)
    async def test_increment_dunning_attempt_calculates_next_retry(
        self,
        mock_update_sub,
        mock_queue_email,
        mock_settings,
    ):
        """Test that increment_dunning_attempt calculates correct next_retry_at."""
        now = int(time.time())
        sub = _make_subscription_out(
            **{
                "_id": "sub_calc_retry",
                "renewal_attempts": 0,
            }
        )

        mock_settings_instance = MagicMock()
        mock_settings_instance.dunning_retry_days = [1, 3, 7, 14]
        mock_settings.return_value = mock_settings_instance

        updated_sub = _make_subscription_out(
            **{
                "_id": "sub_calc_retry",
                "renewal_attempts": 1,
                "next_retry_at": now + (1 * 86400),  # 1 day from now
            }
        )
        mock_update_sub.return_value = updated_sub

        from services.dunning_service import _increment_dunning_attempt

        await _increment_dunning_attempt(sub, now)

        # Verify update was called
        mock_update_sub.assert_called_once()
        call_args = mock_update_sub.call_args
        update_data = call_args[1]["sub_data"]

        # Check that renewal_attempts was incremented
        assert update_data.renewal_attempts == 1
        # Check that next_retry_at is approximately 1 day from now
        assert update_data.next_retry_at == now + (1 * 86400)

    @patch("services.dunning_service.get_settings")
    @patch("services.dunning_service._queue_dunning_email", new_callable=AsyncMock)
    @patch("services.dunning_service.update_subscription", new_callable=AsyncMock)
    async def test_increment_dunning_attempt_second_retry(
        self,
        mock_update_sub,
        mock_queue_email,
        mock_settings,
    ):
        """Test retry calculation for second dunning attempt."""
        now = int(time.time())
        sub = _make_subscription_out(
            **{
                "_id": "sub_second_retry",
                "renewal_attempts": 1,
            }
        )

        mock_settings_instance = MagicMock()
        mock_settings_instance.dunning_retry_days = [1, 3, 7, 14]
        mock_settings.return_value = mock_settings_instance

        updated_sub = _make_subscription_out(
            **{
                "_id": "sub_second_retry",
                "renewal_attempts": 2,
                "next_retry_at": now + (3 * 86400),  # 3 days from now
            }
        )
        mock_update_sub.return_value = updated_sub

        from services.dunning_service import _increment_dunning_attempt

        await _increment_dunning_attempt(sub, now)

        call_args = mock_update_sub.call_args
        update_data = call_args[1]["sub_data"]

        assert update_data.renewal_attempts == 2
        assert update_data.next_retry_at == now + (3 * 86400)

    @patch("services.dunning_service.update_subscription", new_callable=AsyncMock)
    async def test_suspend_subscription(self, mock_update_sub):
        """Test subscription suspension."""
        now = int(time.time())
        sub = _make_subscription_out(**{"_id": "sub_suspend"})

        suspended_sub = _make_subscription_out(
            **{
                "_id": "sub_suspend",
                "status": SubscriptionStatus.SUSPENDED.value,
            }
        )
        mock_update_sub.return_value = suspended_sub

        from services.dunning_service import _suspend_subscription

        await _suspend_subscription(sub, now)

        mock_update_sub.assert_called_once()
        call_args = mock_update_sub.call_args
        update_data = call_args[1]["sub_data"]
        assert update_data.status == SubscriptionStatus.SUSPENDED

    @patch("services.dunning_service.get_settings")
    @patch("services.dunning_service.update_subscription", new_callable=AsyncMock)
    @patch("services.dunning_service.get_plan", new_callable=AsyncMock)
    @patch("services.dunning_service.PaymentManager")
    @patch("services.dunning_service.get_subscriptions", new_callable=AsyncMock)
    async def test_process_dunning_payment_failure(
        self,
        mock_get_subs,
        mock_payment_mgr,
        mock_get_plan,
        mock_update_sub,
        mock_settings,
    ):
        """Test dunning when payment provider fails."""
        now = int(time.time())
        sub = _make_subscription_out(
            **{
                "_id": "sub_payment_fail",
                "status": SubscriptionStatus.PAST_DUE.value,
                "renewal_attempts": 0,
                "next_retry_at": now - 3600,
            }
        )
        mock_get_subs.return_value = [sub]

        mock_settings_instance = MagicMock()
        mock_settings_instance.max_dunning_attempts = 5
        mock_settings_instance.dunning_retry_days = [1, 3, 7, 14]
        mock_settings.return_value = mock_settings_instance

        plan = _make_plan_out()
        mock_get_plan.return_value = plan

        # Payment fails
        mock_provider = MagicMock()
        mock_provider.create_intent.side_effect = Exception("Payment provider error")
        mock_payment_mgr_instance = MagicMock()
        mock_payment_mgr_instance.get_provider.return_value = mock_provider
        mock_payment_mgr.get_instance.return_value = mock_payment_mgr_instance

        updated_sub = _make_subscription_out(
            **{
                "_id": "sub_payment_fail",
                "status": SubscriptionStatus.PAST_DUE.value,
                "renewal_attempts": 1,
            }
        )
        mock_update_sub.return_value = updated_sub

        from services.dunning_service import process_dunning

        result = await process_dunning()

        assert result["retried_count"] == 0
        assert result["failed_count"] == 1
        # Should still update the subscription with incremented attempts
        assert mock_update_sub.called
