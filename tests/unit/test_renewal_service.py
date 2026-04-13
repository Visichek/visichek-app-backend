"""
Unit tests for renewal service with mocked repositories and providers.
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
from core.payments import PaymentIntentResponse, PaymentProviderName, PaymentStatus

pytestmark = pytest.mark.asyncio


def _make_plan_out(**overrides) -> PlanOut:
    """Factory for PlanOut test objects."""
    defaults = {
        "_id": "507f1f77bcf86cd799439111",
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
    return PlanOut(**defaults)  # type: ignore[arg-type]


def _make_subscription_out(**overrides) -> SubscriptionOut:
    """Factory for SubscriptionOut test objects."""
    now = int(time.time())
    defaults = {
        "_id": "507f1f77bcf86cd799439112",
        "tenant_id": "507f1f77bcf86cd799439200",
        "plan_id": "507f1f77bcf86cd799439111",
        "status": SubscriptionStatus.ACTIVE.value,
        "billing_cycle": BillingCycle.MONTHLY.value,
        "effective_price": 100.0,
        "currency": "NGN",
        "current_period_start": now,
        "current_period_end": now + 2592000,
        "applied_discount_ids": [],
        "date_created": now,
        "last_updated": now,
    }
    defaults.update(overrides)
    return SubscriptionOut(**defaults)  # type: ignore[arg-type]


class TestRenewalService:
    """Tests for renewal service functions."""

    @patch("services.renewal_service.get_subscriptions", new_callable=AsyncMock)
    async def test_renew_due_subscriptions_no_subscriptions(self, mock_get_subs):
        """Test renewal with no subscriptions due."""
        mock_get_subs.return_value = []

        from services.renewal_service import renew_due_subscriptions

        result = await renew_due_subscriptions()

        assert result["renewed_count"] == 0
        assert result["failed_count"] == 0
        assert result["total_processed"] == 0
        mock_get_subs.assert_called_once()

    @patch(
        "services.renewal_service._get_provider_for_tenant",
        new_callable=AsyncMock,
        return_value="stripe",
    )
    @patch(
        "services.renewal_service.invalidate_tenant_plan_cache", new_callable=AsyncMock
    )
    @patch("services.renewal_service.generate_invoice", new_callable=AsyncMock)
    @patch("services.renewal_service.update_subscription", new_callable=AsyncMock)
    @patch("services.renewal_service.get_plan", new_callable=AsyncMock)
    @patch("services.renewal_service.PaymentManager")
    @patch("services.renewal_service.get_subscriptions", new_callable=AsyncMock)
    async def test_renew_due_subscriptions_success(
        self,
        mock_get_subs,
        mock_payment_mgr,
        mock_get_plan,
        mock_update_sub,
        mock_generate_invoice,
        mock_invalidate_cache,
        mock_get_provider_for_tenant,
    ):
        """Test successful renewal of a subscription."""
        now = int(time.time())
        sub = _make_subscription_out(
            **{
                "_id": "507f1f77bcf86cd799439113",
                "current_period_end": now - 3600,  # Expired
            }
        )
        mock_get_subs.return_value = [sub]

        plan = _make_plan_out()
        mock_get_plan.return_value = plan

        # Mock payment manager
        mock_provider = MagicMock()
        mock_intent = PaymentIntentResponse(
            provider=PaymentProviderName.STRIPE,
            reference="intent_123",
            status=PaymentStatus.SUCCEEDED,
            checkout_url=None,
            provider_payload={"id": "txn_stripe_123"},
        )
        mock_provider.create_intent.return_value = mock_intent
        mock_payment_mgr_instance = MagicMock()
        mock_payment_mgr_instance.get_provider.return_value = mock_provider
        mock_payment_mgr.get_instance.return_value = mock_payment_mgr_instance

        updated_sub = _make_subscription_out(**{"_id": "507f1f77bcf86cd799439113"})
        mock_update_sub.return_value = updated_sub

        from services.renewal_service import renew_due_subscriptions

        result = await renew_due_subscriptions()

        assert result["renewed_count"] == 1
        assert result["failed_count"] == 0
        mock_update_sub.assert_called_once()
        mock_generate_invoice.assert_called_once()
        mock_invalidate_cache.assert_called_once_with("507f1f77bcf86cd799439200")

    @patch("services.renewal_service.update_subscription", new_callable=AsyncMock)
    @patch("services.renewal_service.get_plan", new_callable=AsyncMock)
    @patch("services.renewal_service.PaymentManager")
    @patch("services.renewal_service.get_subscriptions", new_callable=AsyncMock)
    async def test_renew_due_subscriptions_payment_failure(
        self,
        mock_get_subs,
        mock_payment_mgr,
        mock_get_plan,
        mock_update_sub,
    ):
        """Test renewal failure when payment provider fails."""
        now = int(time.time())
        sub = _make_subscription_out(
            **{
                "_id": "507f1f77bcf86cd799439114",
                "current_period_end": now - 3600,
            }
        )
        mock_get_subs.return_value = [sub]

        plan = _make_plan_out()
        mock_get_plan.return_value = plan

        # Mock payment manager that raises error
        mock_provider = MagicMock()
        mock_provider.create_intent.side_effect = Exception("Payment failed")
        mock_payment_mgr_instance = MagicMock()
        mock_payment_mgr_instance.get_provider.return_value = mock_provider
        mock_payment_mgr.get_instance.return_value = mock_payment_mgr_instance

        from services.renewal_service import renew_due_subscriptions

        result = await renew_due_subscriptions()

        assert result["renewed_count"] == 0
        assert result["failed_count"] == 1
        # Should NOT call update_subscription on payment failure
        mock_update_sub.assert_not_called()

    @patch(
        "services.renewal_service.invalidate_tenant_plan_cache", new_callable=AsyncMock
    )
    @patch("services.renewal_service.generate_invoice", new_callable=AsyncMock)
    @patch("services.renewal_service.update_subscription", new_callable=AsyncMock)
    @patch("services.renewal_service.get_plan", new_callable=AsyncMock)
    @patch("services.renewal_service.PaymentManager")
    async def test_attempt_renewal_invalid_plan_id(
        self,
        mock_payment_mgr,
        mock_get_plan,
        mock_update_sub,
        mock_generate_invoice,
        mock_invalidate_cache,
    ):
        """Test renewal with invalid plan ID."""
        sub = _make_subscription_out(**{"plan_id": "invalid_plan_id"})
        mock_get_plan.return_value = None

        from services.renewal_service import _attempt_renewal

        result = await _attempt_renewal(sub)

        assert result is False
        mock_update_sub.assert_not_called()

    @patch("services.renewal_service.get_subscriptions", new_callable=AsyncMock)
    async def test_convert_expiring_trials_no_trials(self, mock_get_subs):
        """Test trial conversion with no expiring trials."""
        mock_get_subs.return_value = []

        from services.renewal_service import convert_expiring_trials

        result = await convert_expiring_trials()

        assert result["converted_active"] == 0
        assert result["converted_past_due"] == 0
        assert result["failed_count"] == 0

    @patch(
        "services.renewal_service.invalidate_tenant_plan_cache", new_callable=AsyncMock
    )
    @patch("services.renewal_service.generate_invoice", new_callable=AsyncMock)
    @patch("services.renewal_service.update_subscription", new_callable=AsyncMock)
    @patch("services.renewal_service.get_plan", new_callable=AsyncMock)
    @patch("services.renewal_service.PaymentManager")
    @patch("services.renewal_service.get_subscriptions", new_callable=AsyncMock)
    @patch(
        "services.renewal_service._get_provider_for_tenant",
        new_callable=AsyncMock,
        return_value="stripe",
    )
    async def test_convert_expiring_trials_success(
        self,
        mock_get_provider_for_tenant,
        mock_get_subs,
        mock_payment_mgr,
        mock_get_plan,
        mock_update_sub,
        mock_generate_invoice,
        mock_invalidate_cache,
    ):
        """Test successful trial conversion to ACTIVE."""
        now = int(time.time())
        trial_sub = _make_subscription_out(
            **{
                "_id": "507f1f77bcf86cd799439115",
                "status": SubscriptionStatus.TRIALING.value,
                "trial_ends_at": now - 3600,
            }
        )
        mock_get_subs.return_value = [trial_sub]

        plan = _make_plan_out()
        mock_get_plan.return_value = plan

        # Mock payment provider
        mock_provider = MagicMock()
        mock_intent = PaymentIntentResponse(
            provider=PaymentProviderName.STRIPE,
            reference="intent_507f1f77bcf86cd799439115",
            status=PaymentStatus.SUCCEEDED,
            checkout_url=None,
            provider_payload={"id": "txn_507f1f77bcf86cd799439115"},
        )
        mock_provider.create_intent.return_value = mock_intent
        mock_payment_mgr_instance = MagicMock()
        mock_payment_mgr_instance.get_provider.return_value = mock_provider
        mock_payment_mgr.get_instance.return_value = mock_payment_mgr_instance

        updated_sub = _make_subscription_out(
            **{
                "_id": "507f1f77bcf86cd799439115",
                "status": SubscriptionStatus.ACTIVE.value,
            }
        )
        mock_update_sub.return_value = updated_sub

        from services.renewal_service import convert_expiring_trials

        result = await convert_expiring_trials()

        assert result["converted_active"] == 1
        assert result["converted_past_due"] == 0
        mock_update_sub.assert_called_once()
        mock_generate_invoice.assert_called_once()

    @patch("services.renewal_service.get_subscriptions", new_callable=AsyncMock)
    async def test_convert_expiring_trials_payment_failure(
        self,
        mock_get_subs,
    ):
        """Test trial conversion when payment fails."""
        with patch(
            "services.renewal_service.update_subscription", new_callable=AsyncMock
        ) as mock_update_sub:
            with patch(
                "services.renewal_service.get_plan", new_callable=AsyncMock
            ) as mock_get_plan:
                with patch(
                    "services.renewal_service.PaymentManager"
                ) as mock_payment_mgr:
                    now = int(time.time())
                    trial_sub = _make_subscription_out(
                        **{
                            "_id": "507f1f77bcf86cd799439116",
                            "status": SubscriptionStatus.TRIALING.value,
                            "trial_ends_at": now - 3600,
                        }
                    )
                    mock_get_subs.return_value = [trial_sub]

                    plan = _make_plan_out()
                    mock_get_plan.return_value = plan

                    # Payment fails
                    mock_provider = MagicMock()
                    mock_provider.create_intent.side_effect = Exception(
                        "Payment failed"
                    )
                    mock_payment_mgr_instance = MagicMock()
                    mock_payment_mgr_instance.get_provider.return_value = mock_provider
                    mock_payment_mgr.get_instance.return_value = (
                        mock_payment_mgr_instance
                    )

                    updated_sub = _make_subscription_out(
                        **{
                            "_id": "507f1f77bcf86cd799439116",
                            "status": SubscriptionStatus.PAST_DUE.value,
                        }
                    )
                    mock_update_sub.return_value = updated_sub

                    from services.renewal_service import convert_expiring_trials

                    result = await convert_expiring_trials()

                    assert result["converted_active"] == 0
                    assert result["converted_past_due"] == 1
                    # Should update to PAST_DUE
                    assert mock_update_sub.called

    @patch("services.renewal_service.PaymentManager")
    async def test_attempt_renewal_payment_manager_not_available(
        self, mock_payment_mgr
    ):
        """Test renewal when payment manager is not configured."""
        sub = _make_subscription_out()

        mock_payment_mgr.get_instance.side_effect = RuntimeError(
            "PaymentManager not configured"
        )

        with patch(
            "services.renewal_service.get_plan", new_callable=AsyncMock
        ) as mock_get_plan:
            plan = _make_plan_out()
            mock_get_plan.return_value = plan

            from services.renewal_service import _attempt_renewal

            result = await _attempt_renewal(sub)

            assert result is False
