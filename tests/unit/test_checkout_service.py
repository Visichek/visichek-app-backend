"""Unit tests for the agnostic checkout service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.payments.types import (
    PaymentIntentResponse,
    PaymentProviderName,
    PaymentStatus,
)
from schemas.checkout_schema import (
    CheckoutProvider,
    CheckoutSessionOut,
    CheckoutStatus,
    PriceBreakdown,
)
from schemas.plan_schema import PlanOut, PlanStatus, PlanTier
from schemas.subscription_schema import BillingCycle

pytestmark = pytest.mark.asyncio


def _plan() -> PlanOut:
    return PlanOut(
        _id="507f1f77bcf86cd799439011",
        name="pro",
        display_name="Pro",
        tier=PlanTier.PROFESSIONAL,
        status=PlanStatus.ACTIVE,
        base_price_monthly=100.0,
        base_price_yearly=1000.0,
        currency="NGN",
    )


def _ok_intent() -> PaymentIntentResponse:
    return PaymentIntentResponse(
        provider=PaymentProviderName.APP,
        reference="chk_test",
        status=PaymentStatus.PENDING,
        checkout_url="/payments/app-checkout/chk_test",
        provider_payload={"mode": "app_test"},
    )


def _session_stub(**overrides) -> CheckoutSessionOut:
    session = CheckoutSessionOut(
        _id="64f0000000000000000000aa",
        tenant_id="tenant_1",
        plan_id="507f1f77bcf86cd799439011",
        billing_cycle=BillingCycle.MONTHLY,
        currency="NGN",
        amount_minor=10000,
        provider=CheckoutProvider.APP,
        status=CheckoutStatus.PENDING,
        checkout_url="/payments/app-checkout/chk_test",
        provider_reference="chk_test",
        provider_payload={"mode": "app_test"},
        breakdown=PriceBreakdown(
            base_price=100.0,
            billing_cycle=BillingCycle.MONTHLY,
            currency="NGN",
            final_price=100.0,
            amount_minor=10000,
        ),
        applied_discount_ids=[],
        created_by_user_id="user_1",
        expires_at=9_999_999_999,
    )
    for key, value in overrides.items():
        object.__setattr__(session, key, value)
    return session


async def test_create_checkout_falls_back_when_preferred_provider_missing() -> None:
    manager = MagicMock()
    manager.has_provider = lambda name: name == "app"

    def _get_provider(name):
        assert name == "app"
        p = MagicMock()
        p.create_intent = MagicMock(return_value=_ok_intent())
        return p

    manager.get_provider = _get_provider

    from services import checkout_service

    with (
        patch.object(
            checkout_service.PaymentManager, "get_instance", return_value=manager
        ),
        patch.object(checkout_service, "get_plan", new=AsyncMock(return_value=_plan())),
        patch.object(
            checkout_service,
            "_validate_and_collect_discounts",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            checkout_service,
            "create_checkout",
            new=AsyncMock(
                side_effect=lambda data: _session_stub(amount_minor=data.amount_minor)
            ),
        ),
        patch.object(checkout_service, "record_audit_event", new=AsyncMock()),
    ):
        session = await checkout_service.create_checkout_session(
            tenant_id="tenant_1",
            created_by_user_id="user_1",
            plan_id="507f1f77bcf86cd799439011",
            billing_cycle=BillingCycle.MONTHLY,
            preferred_provider=CheckoutProvider.STRIPE,  # not registered
        )

    assert session.provider == CheckoutProvider.APP
    assert session.amount_minor == 10000


async def test_create_checkout_falls_back_when_first_provider_raises() -> None:
    manager = MagicMock()
    manager.has_provider = lambda name: name in ("stripe", "app")

    bad_stripe = MagicMock()
    bad_stripe.create_intent = MagicMock(side_effect=RuntimeError("bad key"))

    good_app = MagicMock()
    good_app.create_intent = MagicMock(return_value=_ok_intent())

    providers = {"stripe": bad_stripe, "app": good_app}
    manager.get_provider = lambda name: providers[name]

    from services import checkout_service

    with (
        patch.object(
            checkout_service.PaymentManager, "get_instance", return_value=manager
        ),
        patch.object(checkout_service, "get_plan", new=AsyncMock(return_value=_plan())),
        patch.object(
            checkout_service,
            "_validate_and_collect_discounts",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            checkout_service,
            "create_checkout",
            new=AsyncMock(
                side_effect=lambda data: _session_stub(
                    provider=CheckoutProvider(data.provider.value)
                )
            ),
        ),
        patch.object(checkout_service, "record_audit_event", new=AsyncMock()),
    ):
        session = await checkout_service.create_checkout_session(
            tenant_id="tenant_1",
            created_by_user_id="user_1",
            plan_id="507f1f77bcf86cd799439011",
            billing_cycle=BillingCycle.MONTHLY,
            preferred_provider=CheckoutProvider.STRIPE,
        )

    # Stripe raised, so we silently fell back to app mode.
    assert session.provider == CheckoutProvider.APP
    bad_stripe.create_intent.assert_called_once()
    good_app.create_intent.assert_called_once()


async def test_complete_checkout_success_provisions_subscription() -> None:
    from services import checkout_service

    pending = _session_stub()
    updated = _session_stub(status=CheckoutStatus.SUCCEEDED, subscription_id="sub_1")
    sub = MagicMock(id="sub_1")

    with (
        patch.object(
            checkout_service, "get_checkout_by_id", new=AsyncMock(return_value=pending)
        ),
        patch.object(
            checkout_service,
            "retrieve_tenant_active_subscription",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            checkout_service, "subscribe_tenant", new=AsyncMock(return_value=sub)
        ),
        patch.object(
            checkout_service, "update_checkout", new=AsyncMock(return_value=updated)
        ),
        patch.object(checkout_service, "record_audit_event", new=AsyncMock()),
    ):
        result = await checkout_service.complete_checkout(
            checkout_id="64f0000000000000000000aa", outcome="success"
        )

    assert result.status == CheckoutStatus.SUCCEEDED
    assert result.subscription_id == "sub_1"


async def test_complete_checkout_changes_plan_when_subscription_exists() -> None:
    """When the tenant already has an active sub, checkout completion
    must switch plans rather than 409ing on subscribe_tenant."""
    from services import checkout_service

    pending = _session_stub()
    updated = _session_stub(status=CheckoutStatus.SUCCEEDED, subscription_id="sub_old")
    existing = MagicMock(id="sub_old")
    switched = MagicMock(id="sub_old")

    subscribe_mock = AsyncMock()
    change_mock = AsyncMock(return_value=switched)

    with (
        patch.object(
            checkout_service, "get_checkout_by_id", new=AsyncMock(return_value=pending)
        ),
        patch.object(
            checkout_service,
            "retrieve_tenant_active_subscription",
            new=AsyncMock(return_value=existing),
        ),
        patch.object(checkout_service, "subscribe_tenant", new=subscribe_mock),
        patch.object(
            checkout_service, "provision_plan_change_from_checkout", new=change_mock
        ),
        patch.object(
            checkout_service, "update_checkout", new=AsyncMock(return_value=updated)
        ),
        patch.object(checkout_service, "record_audit_event", new=AsyncMock()),
    ):
        result = await checkout_service.complete_checkout(
            checkout_id="64f0000000000000000000aa", outcome="success"
        )

    assert result.status == CheckoutStatus.SUCCEEDED
    subscribe_mock.assert_not_called()
    change_mock.assert_awaited_once()
    await_args = change_mock.await_args
    assert await_args is not None
    kwargs = await_args.kwargs
    assert kwargs["existing_sub_id"] == "sub_old"
    assert kwargs["tenant_id"] == "tenant_1"
    assert kwargs["new_plan_id"] == "507f1f77bcf86cd799439011"


async def test_complete_checkout_is_idempotent_for_terminal_state() -> None:
    from services import checkout_service

    already_succeeded = _session_stub(status=CheckoutStatus.SUCCEEDED)
    subscribe = AsyncMock()

    with (
        patch.object(
            checkout_service,
            "get_checkout_by_id",
            new=AsyncMock(return_value=already_succeeded),
        ),
        patch.object(checkout_service, "subscribe_tenant", new=subscribe),
        patch.object(checkout_service, "update_checkout", new=AsyncMock()),
    ):
        result = await checkout_service.complete_checkout(
            checkout_id="64f0000000000000000000aa", outcome="success"
        )

    assert result.status == CheckoutStatus.SUCCEEDED
    subscribe.assert_not_called()


async def test_get_tenant_checkout_does_not_leak_across_tenants() -> None:
    from core.errors import AppException
    from services import checkout_service

    other_tenants = _session_stub(tenant_id="other_tenant")

    with patch.object(
        checkout_service,
        "get_checkout_by_id",
        new=AsyncMock(return_value=other_tenants),
    ):
        with pytest.raises(AppException) as err:
            await checkout_service.get_tenant_checkout(
                tenant_id="tenant_1", checkout_id="64f0000000000000000000aa"
            )

    assert err.value.status_code == 404
