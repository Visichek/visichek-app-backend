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


async def test_with_full_checkout_url_accepts_cached_dict() -> None:
    """Regression: the single-entity GET cache returns a dict on a hit, so the
    GET /checkout/sessions/{id} poll 500'd with
    "'dict' object has no attribute 'checkout_url'". with_full_checkout_url must
    coerce the dict (the same shape entity_cache stores) into the model."""
    from services import checkout_service

    model = _session_stub(checkout_url="https://checkout.paystack.com/abc")
    # Mirror exactly what core/queue/entity_cache stores + returns on a hit.
    cached = model.model_dump(mode="json", by_alias=True)
    assert isinstance(cached, dict)

    result = checkout_service.with_full_checkout_url(cached)

    assert isinstance(result, CheckoutSessionOut)
    assert result.checkout_url == "https://checkout.paystack.com/abc"


async def test_trial_checkout_uses_paystack_tokenization_charge() -> None:
    """A trial via /checkout/sessions must charge the Paystack tokenization
    amount (₦50 for NGN) with the trial-tokenization marker — NOT a ₦0 intent —
    so a reusable card is captured for the trial-end auto-charge. The session
    still displays ₦0 (free trial)."""
    from services import checkout_service

    manager = MagicMock()
    manager.has_provider = lambda name: name == "paystack"

    captured: dict = {}

    def _create_intent(req):
        captured["req"] = req
        return PaymentIntentResponse(
            provider=PaymentProviderName.PAYSTACK,
            reference=req.reference,
            status=PaymentStatus.PENDING,
            checkout_url="https://checkout.paystack.com/abc",
            provider_payload={},
        )

    provider = MagicMock()
    provider.create_intent = MagicMock(side_effect=_create_intent)
    manager.get_provider = lambda name: provider

    trial = MagicMock(trial_days_snapshot=14, code="TRIAL-X")

    with (
        patch.object(
            checkout_service.PaymentManager, "get_instance", return_value=manager
        ),
        patch.object(checkout_service, "get_plan", new=AsyncMock(return_value=_plan())),
        patch.object(
            checkout_service,
            "validate_trial_code_for_checkout",
            new=AsyncMock(return_value=trial),
        ),
        patch.object(
            checkout_service,
            "create_checkout",
            new=AsyncMock(
                side_effect=lambda data: _session_stub(
                    provider=CheckoutProvider(data.provider.value),
                    amount_minor=data.amount_minor,
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
            trial_code="TRIAL-X",
            customer_email="a@b.com",
        )

    # Provider was charged the NGN tokenization minimum, flagged as a trial.
    assert captured["req"].amount_minor == 5000
    assert captured["req"].metadata["purpose"] == "trial_tokenization"
    assert captured["req"].metadata["trial_days"] == 14
    # The session itself shows the free-trial price (₦0) on a real gateway.
    assert session.provider == CheckoutProvider.PAYSTACK
    assert session.amount_minor == 0


async def test_trial_checkout_rejected_on_non_paystack_provider() -> None:
    """Auto-charging trials are Paystack-only; a trial that would land on
    another real gateway is refused, not started un-billable."""
    from core.errors import AppException
    from services import checkout_service

    manager = MagicMock()
    manager.has_provider = lambda name: name == "flutterwave"
    manager.get_provider = lambda name: MagicMock()

    trial = MagicMock(trial_days_snapshot=14, code="TRIAL-X")

    with (
        patch.object(
            checkout_service.PaymentManager, "get_instance", return_value=manager
        ),
        patch.object(checkout_service, "get_plan", new=AsyncMock(return_value=_plan())),
        patch.object(
            checkout_service,
            "validate_trial_code_for_checkout",
            new=AsyncMock(return_value=trial),
        ),
        patch.object(checkout_service, "create_checkout", new=AsyncMock()),
        patch.object(checkout_service, "record_audit_event", new=AsyncMock()),
    ):
        with pytest.raises(AppException) as err:
            await checkout_service.create_checkout_session(
                tenant_id="tenant_1",
                created_by_user_id="user_1",
                plan_id="507f1f77bcf86cd799439011",
                billing_cycle=BillingCycle.MONTHLY,
                trial_code="TRIAL-X",
            )

    assert err.value.status_code == 400


async def test_complete_trial_tokenization_refunds_subscribes_and_reconciles() -> None:
    """The webhook completion refunds the ₦50, starts the trial, marks the
    checkout session SUCCEEDED, and redeems the trial code."""
    from services import paystack_billing_service as pbs

    provider = MagicMock()
    provider.refund = MagicMock()
    manager = MagicMock()
    manager.get_provider = lambda name: provider

    pending = _session_stub(status=CheckoutStatus.PENDING)
    sub = MagicMock(id="sub_1")

    payload = {
        "data": {
            "reference": "trialcap_abc",
            "customer": {"email": "a@b.com"},
            "authorization": {"authorization_code": "AUTH_x", "reusable": True},
            "metadata": {
                "tenant_id": "507f1f77bcf86cd799439011",
                "plan_id": "507f1f77bcf86cd799439011",
                "billing_cycle": "monthly",
                "trial_days": 14,
                "trial_code": "TRIAL-X",
            },
        }
    }

    with (
        patch.object(pbs.PaymentManager, "get_instance", return_value=manager),
        patch(
            "services.subscription_service.subscribe_tenant",
            new=AsyncMock(return_value=sub),
        ),
        patch(
            "repositories.checkout_repo.get_checkout_by_reference",
            new=AsyncMock(return_value=pending),
        ),
        patch(
            "repositories.checkout_repo.update_checkout", new=AsyncMock()
        ) as update_mock,
        patch(
            "services.trial_code_service.mark_trial_code_used", new=AsyncMock()
        ) as redeem_mock,
    ):
        result = await pbs.complete_trial_tokenization(payload)

    assert result["handled"] is True
    provider.refund.assert_called_once()
    assert provider.refund.call_args.kwargs["reference"] == "trialcap_abc"
    # Session reconciled to SUCCEEDED with the new subscription id.
    update_args = update_mock.await_args
    assert update_args is not None
    assert update_args.args[1].status == CheckoutStatus.SUCCEEDED
    assert update_args.args[1].subscription_id == "sub_1"
    # Trial code redeemed.
    redeem_args = redeem_mock.await_args
    assert redeem_args is not None
    assert redeem_args.kwargs["code"] == "TRIAL-X"


async def test_checkout_sets_redirect_callback_from_setting() -> None:
    """create_checkout_session passes PAYMENT_CALLBACK_URL as the provider
    redirect_url so Paystack returns the customer to a frontend page (with the
    reference) instead of the bare webhook URL."""
    from types import SimpleNamespace

    from services import checkout_service

    manager = MagicMock()
    manager.has_provider = lambda name: name == "paystack"
    captured: dict = {}

    def _create_intent(req):
        captured["req"] = req
        return PaymentIntentResponse(
            provider=PaymentProviderName.PAYSTACK,
            reference=req.reference,
            status=PaymentStatus.PENDING,
            checkout_url="https://checkout.paystack.com/x",
            provider_payload={},
        )

    provider = MagicMock()
    provider.create_intent = MagicMock(side_effect=_create_intent)
    manager.get_provider = lambda name: provider

    fake_settings = SimpleNamespace(
        payment_callback_url="https://client.visichek.app/app/billing/return",
        checkout_session_ttl_seconds=86400,
        app_base_url="",
    )

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
        patch.object(checkout_service, "get_settings", return_value=fake_settings),
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
        await checkout_service.create_checkout_session(
            tenant_id="tenant_1",
            created_by_user_id="user_1",
            plan_id="507f1f77bcf86cd799439011",
            billing_cycle=BillingCycle.MONTHLY,
        )

    assert (
        captured["req"].metadata["redirect_url"]
        == "https://client.visichek.app/app/billing/return"
    )


async def test_reconcile_completes_session_on_paystack_success() -> None:
    """Poll fallback: a still-PENDING Paystack session that verifies as
    'success' is completed via the same webhook completion path."""
    from services import checkout_service

    session = _session_stub(
        provider=CheckoutProvider.PAYSTACK,
        provider_reference="chk_poll",
        trial_code="TRIAL-X",
    )
    manager = MagicMock()
    manager.has_provider = lambda name: name == "paystack"
    provider = MagicMock()
    provider.fetch_transaction = MagicMock(
        return_value=MagicMock(
            raw={"data": {"status": "success", "reference": "chk_poll"}}
        )
    )
    manager.get_provider = lambda name: provider
    handle = AsyncMock()

    with (
        patch.object(
            checkout_service.PaymentManager, "get_instance", return_value=manager
        ),
        patch(
            "repositories.checkout_repo.list_pending_checkouts_for_poll",
            new=AsyncMock(return_value=[session]),
        ),
        patch.object(
            checkout_service, "get_checkout_by_id", new=AsyncMock(return_value=session)
        ),
        patch.object(checkout_service, "update_checkout", new=AsyncMock()),
        patch("services.paystack_webhook_service._handle_charge_success", new=handle),
    ):
        result = await checkout_service.reconcile_pending_paystack_checkouts()

    assert result["completed"] == 1
    handle.assert_awaited_once()


async def test_reconcile_records_attempt_when_still_pending() -> None:
    """A session that is still 'ongoing' on Paystack just records the attempt."""
    from services import checkout_service

    session = _session_stub(
        provider=CheckoutProvider.PAYSTACK,
        provider_reference="chk_poll",
        poll_attempts=1,
    )
    manager = MagicMock()
    manager.has_provider = lambda name: name == "paystack"
    provider = MagicMock()
    provider.fetch_transaction = MagicMock(
        return_value=MagicMock(raw={"data": {"status": "ongoing"}})
    )
    manager.get_provider = lambda name: provider
    update_mock = AsyncMock()

    with (
        patch.object(
            checkout_service.PaymentManager, "get_instance", return_value=manager
        ),
        patch(
            "repositories.checkout_repo.list_pending_checkouts_for_poll",
            new=AsyncMock(return_value=[session]),
        ),
        patch.object(checkout_service, "update_checkout", new=update_mock),
    ):
        result = await checkout_service.reconcile_pending_paystack_checkouts()

    assert result["completed"] == 0
    update_args = update_mock.await_args
    assert update_args is not None
    assert update_args.args[1].poll_attempts == 2  # 1 + 1


async def test_reconcile_fails_session_and_releases_trial_code() -> None:
    """A terminal Paystack failure marks the session FAILED and frees the
    reserved trial code so the tenant can retry."""
    from services import checkout_service

    session = _session_stub(
        provider=CheckoutProvider.PAYSTACK,
        provider_reference="chk_poll",
        trial_code="TRIAL-X",
    )
    manager = MagicMock()
    manager.has_provider = lambda name: name == "paystack"
    provider = MagicMock()
    provider.fetch_transaction = MagicMock(
        return_value=MagicMock(raw={"data": {"status": "failed"}})
    )
    manager.get_provider = lambda name: provider
    update_mock = AsyncMock()
    release_mock = AsyncMock()

    with (
        patch.object(
            checkout_service.PaymentManager, "get_instance", return_value=manager
        ),
        patch(
            "repositories.checkout_repo.list_pending_checkouts_for_poll",
            new=AsyncMock(return_value=[session]),
        ),
        patch.object(checkout_service, "update_checkout", new=update_mock),
        patch.object(checkout_service, "mark_trial_code_cancelled", new=release_mock),
    ):
        result = await checkout_service.reconcile_pending_paystack_checkouts()

    assert result["failed"] == 1
    update_args = update_mock.await_args
    assert update_args is not None
    assert update_args.args[1].status == CheckoutStatus.FAILED
    release_mock.assert_awaited_once()


async def test_get_checkout_by_reference_returns_session_for_owner() -> None:
    """The payment-return lookup resolves a session by provider reference."""
    from services import checkout_service

    mine = _session_stub(tenant_id="tenant_1", provider_reference="chk_ref")

    with patch.object(
        checkout_service,
        "get_checkout_by_reference",
        new=AsyncMock(return_value=mine),
    ):
        result = await checkout_service.get_tenant_checkout_by_reference(
            tenant_id="tenant_1", reference="chk_ref"
        )

    assert result.provider_reference == "chk_ref"


async def test_get_checkout_by_reference_does_not_leak_across_tenants() -> None:
    """A reference belonging to another tenant must 404, not leak."""
    from core.errors import AppException
    from services import checkout_service

    other = _session_stub(tenant_id="other_tenant", provider_reference="chk_ref")

    with patch.object(
        checkout_service,
        "get_checkout_by_reference",
        new=AsyncMock(return_value=other),
    ):
        with pytest.raises(AppException) as err:
            await checkout_service.get_tenant_checkout_by_reference(
                tenant_id="tenant_1", reference="chk_ref"
            )

    assert err.value.status_code == 404


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
