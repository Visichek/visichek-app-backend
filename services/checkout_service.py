"""Provider-agnostic checkout session service.

Owns:
- Creating a checkout session for a (tenant, plan, discounts) tuple with
  lazy provider fallback (stripe → flutterwave → paystack → app).
- Completing a checkout when payment is confirmed (via webhook for external
  providers or via the in-app simulator for ``app`` mode). On success the
  tenant's subscription is provisioned through ``subscribe_tenant``.
- Exposing read APIs (list / get) for the tenant super_admin history view.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import List, Optional, Tuple

from bson import ObjectId
from fastapi import HTTPException, status

from core.errors import AppException, ErrorCode, resource_not_found
from core.payments import PaymentIntentRequest, PaymentManager
from core.payments.types import PaymentProviderName
from core.settings import get_settings
from repositories.checkout_repo import (
    count_checkouts_for_tenant,
    create_checkout,
    get_checkout_by_id,
    get_checkout_by_reference,
    list_checkouts_for_tenant,
    update_checkout,
)
from repositories.plan_repo import get_plan
from schemas.checkout_schema import (
    CheckoutProvider,
    CheckoutSessionCreate,
    CheckoutSessionOut,
    CheckoutSessionUpdate,
    CheckoutStatus,
    PriceBreakdown,
)
from schemas.discount_schema import DiscountOut, DiscountType
from schemas.plan_schema import PlanOut, PlanStatus
from schemas.subscription_schema import BillingCycle, SubscriptionOut
from services.audit_service import record_audit_event
from services.subscription_service import (
    _calculate_effective_price,
    _validate_and_collect_discounts,
    provision_plan_change_from_checkout,
    retrieve_tenant_active_subscription,
    subscribe_tenant,
)
from services.trial_code_service import (
    mark_trial_code_cancelled,
    mark_trial_code_used,
    validate_trial_code_for_checkout,
)

logger = logging.getLogger(__name__)

# Provider selection order when no preferred provider is supplied.
_PROVIDER_PREFERENCE: Tuple[str, ...] = ("stripe", "flutterwave", "paystack", "app")


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------


def _to_minor(amount: float) -> int:
    """Convert a decimal currency amount to minor units (e.g. 10.50 → 1050)."""
    return int(round(amount * 100))


async def _build_breakdown(
    plan: PlanOut,
    billing_cycle: BillingCycle,
    valid_discounts: List[DiscountOut],
) -> PriceBreakdown:
    base = (
        plan.base_price_monthly
        if billing_cycle == BillingCycle.MONTHLY
        else plan.base_price_yearly
    )
    final = _calculate_effective_price(plan, billing_cycle, valid_discounts)
    percent = sum(
        d.value for d in valid_discounts if d.discount_type == DiscountType.PERCENTAGE
    )
    fixed = sum(
        d.value for d in valid_discounts if d.discount_type == DiscountType.FIXED
    )
    return PriceBreakdown(
        base_price=base,
        billing_cycle=billing_cycle,
        currency=plan.currency,
        applied_discount_ids=[d.id for d in valid_discounts if d.id],
        total_percentage_off=min(percent, 100.0),
        total_fixed_off=fixed,
        final_price=final,
        amount_minor=_to_minor(final),
    )


# ---------------------------------------------------------------------------
# Provider selection with lazy fallback
# ---------------------------------------------------------------------------


def _select_provider(preferred: Optional[CheckoutProvider]) -> str:
    """Return the provider key that should actually handle this checkout.

    Lazy strategy — the decision is made at call time, not at startup. If
    the caller asked for a provider that isn't configured we warn and fall
    through the preference chain.
    """
    manager = PaymentManager.get_instance()
    if preferred and manager.has_provider(preferred.value):
        return preferred.value
    for candidate in _PROVIDER_PREFERENCE:
        if manager.has_provider(candidate):
            return candidate
    # ``app`` provider is always registered; this line is defensive.
    return PaymentProviderName.APP.value


def _create_intent_with_fallback(
    initial_provider: str,
    intent_request: PaymentIntentRequest,
):
    """Try providers in order and return the first that succeeds."""
    manager = PaymentManager.get_instance()
    chain: List[str] = [initial_provider]
    for fallback in _PROVIDER_PREFERENCE:
        if fallback not in chain and manager.has_provider(fallback):
            chain.append(fallback)

    last_error: Optional[Exception] = None
    for name in chain:
        try:
            provider = manager.get_provider(name)
            intent = provider.create_intent(intent_request)
            if name != initial_provider:
                logger.warning(
                    "Payment provider '%s' unavailable; fell back to '%s'",
                    initial_provider,
                    name,
                )
            return name, intent
        except Exception as err:
            logger.warning(
                "Provider '%s' create_intent failed, trying next: %s", name, err
            )
            last_error = err

    raise AppException(
        status_code=502,
        code=ErrorCode.PAYMENT_PROVIDER_ERROR,
        message="All payment providers failed to create a checkout intent",
        details=str(last_error) if last_error else None,
    )


# ---------------------------------------------------------------------------
# Public service API
# ---------------------------------------------------------------------------


async def create_checkout_session(
    *,
    tenant_id: str,
    created_by_user_id: str,
    plan_id: str,
    billing_cycle: BillingCycle = BillingCycle.MONTHLY,
    discount_ids: Optional[List[str]] = None,
    preferred_provider: Optional[CheckoutProvider] = None,
    trial_days: int = 0,
    trial_code: Optional[str] = None,
    metadata: Optional[dict] = None,
    customer_email: Optional[str] = None,
) -> CheckoutSessionOut:
    """Create a provider-agnostic checkout session for a tenant.

    When ``trial_code`` is set, the checkout is forced to a $0 amount and
    snapshots the trial length onto the session. The trial code is only
    consumed (marked USED) once the matching $0 payment completes — see
    ``complete_checkout``. Discount codes follow the same delayed
    redemption contract: validated here, but ``current_redemptions`` is
    incremented only at completion via ``subscribe_tenant`` /
    ``provision_plan_change_from_checkout``.
    """
    if not ObjectId.is_valid(plan_id):
        raise HTTPException(status_code=400, detail="Invalid plan_id")

    plan = await get_plan({"_id": ObjectId(plan_id)})
    if not plan:
        raise resource_not_found(resource="Plan", resource_id=plan_id)
    if plan.status != PlanStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Plan is not active")

    # ── Trial-code path ────────────────────────────────────────────────
    # A valid trial code overrides the price (forced to 0), forbids
    # combining with discount codes (already enforced at the request
    # layer), and overrides ``trial_days`` with the snapshot from the
    # trial row so a later admin edit to ``plan.trial_days`` does not
    # retroactively change an outstanding trial.
    resolved_trial_code: Optional[str] = None
    if trial_code:
        trial = await validate_trial_code_for_checkout(
            code=trial_code,
            tenant_id=tenant_id,
            plan_id=plan_id,
        )
        trial_days = trial.trial_days_snapshot
        resolved_trial_code = trial.code
        # Discounts are mutually exclusive with trials.
        discount_ids = None

    base_price = (
        plan.base_price_monthly
        if billing_cycle == BillingCycle.MONTHLY
        else plan.base_price_yearly
    )
    valid_discounts: List[DiscountOut] = []
    if discount_ids:
        valid_discounts = await _validate_and_collect_discounts(
            discount_ids, tenant_id, plan, base_price
        )

    breakdown = await _build_breakdown(plan, billing_cycle, valid_discounts)
    # Trial overrides the price entirely.
    if resolved_trial_code:
        breakdown = PriceBreakdown(
            base_price=breakdown.base_price,
            billing_cycle=breakdown.billing_cycle,
            currency=breakdown.currency,
            applied_discount_ids=[],
            total_percentage_off=100.0,
            total_fixed_off=0.0,
            final_price=0.0,
            amount_minor=0,
        )

    # One reference value used both as the provider's correlation key and as
    # the {id} path segment of the app-mode checkout URL.
    reference = f"chk_{uuid.uuid4().hex}"

    chosen = _select_provider(preferred_provider)

    intent_metadata: dict = {
        "tenant_id": tenant_id,
        "plan_id": plan_id,
        "billing_cycle": billing_cycle.value,
        **({"trial_code": resolved_trial_code} if resolved_trial_code else {}),
        **(metadata or {}),
    }
    # For Stripe, ensure a customer and save the card off-session on this first
    # payment so the renewal scheduler can charge it later. The provider's
    # create_intent reads ``stripe_customer_id`` from metadata. Best-effort:
    # if customer creation fails we still issue a (non-recurring) checkout.
    if chosen == PaymentProviderName.STRIPE.value and customer_email:
        try:
            from services.stripe_customer_service import create_or_get_stripe_customer

            stripe_customer_id = await create_or_get_stripe_customer(
                tenant_id=tenant_id,
                email=customer_email,
                name="",
            )
            intent_metadata["stripe_customer_id"] = stripe_customer_id
        except Exception:
            logger.warning(
                "Could not provision Stripe customer for checkout; proceeding "
                "without off-session card capture (tenant=%s)",
                tenant_id,
                exc_info=True,
            )

    provider_name, intent = _create_intent_with_fallback(
        chosen,
        PaymentIntentRequest(
            amount_minor=breakdown.amount_minor,
            currency=breakdown.currency,
            reference=reference,
            customer_email=customer_email,
            metadata=intent_metadata,
        ),
    )

    now = int(time.time())
    ttl = get_settings().checkout_session_ttl_seconds
    session_in = CheckoutSessionCreate(
        tenant_id=tenant_id,
        plan_id=plan_id,
        billing_cycle=billing_cycle,
        currency=breakdown.currency,
        amount_minor=breakdown.amount_minor,
        provider=CheckoutProvider(provider_name),
        status=CheckoutStatus.PENDING,
        checkout_url=intent.checkout_url or "",
        provider_reference=intent.reference,
        provider_payload=intent.provider_payload or {},
        breakdown=breakdown,
        applied_discount_ids=breakdown.applied_discount_ids,
        created_by_user_id=created_by_user_id,
        customer_email=customer_email,
        expires_at=now + ttl,
        trial_days=trial_days,
        trial_code=resolved_trial_code,
        metadata=metadata,
    )

    session = await create_checkout(session_in)

    try:
        await record_audit_event(
            actor_id=created_by_user_id,
            actor_role="super_admin",
            action="checkout.created",
            resource_type="checkout_session",
            resource_id=str(session.id),
            tenant_id=tenant_id,
            details={
                "plan_id": plan_id,
                "provider": provider_name,
                "amount_minor": breakdown.amount_minor,
                "currency": breakdown.currency,
                "discount_ids": breakdown.applied_discount_ids,
                "trial_code": resolved_trial_code,
                "trial_days": trial_days,
            },
        )
    except Exception:
        pass

    return session


async def list_tenant_checkouts(
    tenant_id: str,
    status: Optional[CheckoutStatus] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[CheckoutSessionOut], int]:
    items = await list_checkouts_for_tenant(
        tenant_id=tenant_id, status=status, skip=skip, limit=limit
    )
    total = await count_checkouts_for_tenant(tenant_id=tenant_id, status=status)
    return items, total


async def get_tenant_checkout(tenant_id: str, checkout_id: str) -> CheckoutSessionOut:
    session = await get_checkout_by_id(checkout_id)
    if not session:
        raise resource_not_found(resource="CheckoutSession", resource_id=checkout_id)
    if session.tenant_id != tenant_id:
        # Do not leak existence to other tenants.
        raise resource_not_found(resource="CheckoutSession", resource_id=checkout_id)
    return session


async def cancel_checkout(
    *,
    tenant_id: str,
    checkout_id: str,
    cancelled_by_user_id: str,
) -> CheckoutSessionOut:
    session = await get_tenant_checkout(tenant_id, checkout_id)
    if session.status != CheckoutStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel checkout in status '{session.status.value}'",
        )
    now = int(time.time())
    updated = await update_checkout(
        checkout_id,
        CheckoutSessionUpdate(
            status=CheckoutStatus.CANCELLED,
            completed_at=now,
            failure_reason="Cancelled by tenant super_admin",
        ),
    )
    if not updated:
        raise resource_not_found(resource="CheckoutSession", resource_id=checkout_id)
    # If this checkout was holding a trial code, release it so the tenant
    # can claim again without burning their one-shot entitlement.
    if session.trial_code:
        try:
            await mark_trial_code_cancelled(
                code=session.trial_code, tenant_id=tenant_id
            )
        except Exception:
            pass
    try:
        await record_audit_event(
            actor_id=cancelled_by_user_id,
            actor_role="super_admin",
            action="checkout.cancelled",
            resource_type="checkout_session",
            resource_id=checkout_id,
            tenant_id=tenant_id,
        )
    except Exception:
        pass
    return updated


# ---------------------------------------------------------------------------
# Completion (called from webhook handlers and the app-mode simulator)
# ---------------------------------------------------------------------------


async def _mark_failed(
    session: CheckoutSessionOut, reason: str
) -> Optional[CheckoutSessionOut]:
    assert session.id is not None
    now = int(time.time())
    return await update_checkout(
        session.id,
        CheckoutSessionUpdate(
            status=CheckoutStatus.FAILED,
            completed_at=now,
            failure_reason=reason,
        ),
    )


async def _mark_succeeded(
    session: CheckoutSessionOut, subscription_id: str
) -> Optional[CheckoutSessionOut]:
    assert session.id is not None
    now = int(time.time())
    return await update_checkout(
        session.id,
        CheckoutSessionUpdate(
            status=CheckoutStatus.SUCCEEDED,
            completed_at=now,
            subscription_id=subscription_id,
        ),
    )


async def _persist_tenant_billing_provider(session: CheckoutSessionOut) -> None:
    """Persist the provider that handled this checkout as the tenant's default
    for recurring billing, and ensure the provider-specific customer record
    exists so renewals/dunning can charge the saved customer.

    Fire-and-forget: any failure here is logged and swallowed — it must never
    turn a genuinely successful checkout into a failure. The renewal service
    (`_get_provider_for_tenant`) re-validates the customer id and falls back to
    the global default provider if it is missing, so a partial result is safe.
    """
    provider = session.provider.value
    # App-mode is the in-app simulator, not a real recurring-billing provider.
    if provider == PaymentProviderName.APP.value:
        return

    try:
        from bson import ObjectId as _ObjectId

        from repositories.tenant_repo import get_tenant, update_tenant
        from schemas.tenant_schema import TenantUpdate

        if not _ObjectId.is_valid(session.tenant_id):
            return
        tenant = await get_tenant({"_id": _ObjectId(session.tenant_id)})
        if not tenant:
            return

        # Create/look up the provider customer (sets the *_customer_id on the
        # tenant) for providers that support tokenized recurring billing.
        email = session.customer_email
        name = getattr(tenant, "company_name", "") or ""
        if email:
            if provider == PaymentProviderName.PAYSTACK.value:
                from services.paystack_customer_service import (
                    create_or_get_paystack_customer,
                )

                await create_or_get_paystack_customer(
                    tenant_id=session.tenant_id, email=email, name=name
                )
            elif provider == PaymentProviderName.FLUTTERWAVE.value:
                from services.flutterwave_customer_service import (
                    create_or_get_flutterwave_customer,
                )

                await create_or_get_flutterwave_customer(
                    tenant_id=session.tenant_id, email=email, name=name
                )

        # Record the chosen provider as the tenant's default so the renewal
        # scheduler charges it rather than the platform default.
        if tenant.default_payment_provider != provider:
            await update_tenant(
                filter_dict={"_id": _ObjectId(session.tenant_id)},
                tenant_data=TenantUpdate(default_payment_provider=provider),
            )
    except Exception:
        logger.warning(
            "Failed to persist billing provider '%s' for tenant %s after checkout",
            provider,
            session.tenant_id,
            exc_info=True,
        )


async def complete_checkout(
    *,
    checkout_id: str,
    outcome: str,
    acting_user_id: Optional[str] = None,
) -> CheckoutSessionOut:
    """Complete a PENDING checkout as either success or failure.

    On success, provisions the tenant's subscription via ``subscribe_tenant``
    and records a ``checkout.succeeded`` audit event. On failure, marks the
    session as FAILED. Idempotent for already-terminal sessions.
    """
    session = await get_checkout_by_id(checkout_id)
    if not session:
        raise resource_not_found(resource="CheckoutSession", resource_id=checkout_id)

    if session.status != CheckoutStatus.PENDING:
        return session  # idempotent

    now = int(time.time())
    if session.expires_at and now > session.expires_at:
        expired = await update_checkout(
            checkout_id,
            CheckoutSessionUpdate(
                status=CheckoutStatus.EXPIRED,
                completed_at=now,
                failure_reason="Session expired before completion",
            ),
        )
        if expired:
            return expired
        raise resource_not_found(resource="CheckoutSession", resource_id=checkout_id)

    if outcome == "failure":
        updated = await _mark_failed(session, "Payment declined")
        try:
            await record_audit_event(
                actor_id=acting_user_id or "system",
                actor_role="super_admin" if acting_user_id else "system",
                action="checkout.failed",
                resource_type="checkout_session",
                resource_id=checkout_id,
                tenant_id=session.tenant_id,
                details={"provider": session.provider.value},
            )
        except Exception:
            pass
        if not updated:
            raise resource_not_found(
                resource="CheckoutSession", resource_id=checkout_id
            )
        return updated

    if outcome != "success":
        raise HTTPException(
            status_code=400, detail="outcome must be 'success' or 'failure'"
        )

    # --- Success path: provision subscription ------------------------------
    # If the tenant already has an active/trialing subscription this is a
    # plan change paid for via checkout (upgrade/downgrade). Switch plans
    # in place rather than failing with a 409.
    existing_sub = await retrieve_tenant_active_subscription(session.tenant_id)
    try:
        if existing_sub and existing_sub.id:
            switched = await provision_plan_change_from_checkout(
                existing_sub_id=existing_sub.id,
                tenant_id=session.tenant_id,
                new_plan_id=session.plan_id,
                billing_cycle=session.billing_cycle,
                discount_ids=session.applied_discount_ids,
            )
            if switched is None:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to switch plan after checkout",
                )
            subscription: SubscriptionOut = switched
        else:
            subscription = await subscribe_tenant(
                tenant_id=session.tenant_id,
                plan_id=session.plan_id,
                billing_cycle=session.billing_cycle,
                discount_ids=session.applied_discount_ids,
                trial_days=session.trial_days,
            )
    except HTTPException as http_exc:
        # Provisioning failed for a non-409 reason (invalid plan,
        # archived plan, internal error). Record as failed so the admin
        # can see what happened, but do not re-raise a 500.
        await _mark_failed(
            session, f"Subscription provisioning failed: {http_exc.detail}"
        )
        raise

    # Once the underlying subscription is provisioned, redeem the trial
    # code (if any). This is the moment the trial actually counts against
    # the tenant's one-time entitlement — creating the checkout earlier
    # only RESERVED the code. Fire-and-forget: a failure here must not
    # block reporting the checkout as succeeded.
    if session.trial_code and subscription.id:
        try:
            await mark_trial_code_used(
                code=session.trial_code,
                subscription_id=subscription.id,
                tenant_id=session.tenant_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to mark trial code USED after successful checkout %s: %s",
                checkout_id,
                e,
            )

    # Persist the chosen provider + provider customer so recurring renewals
    # charge it. Best-effort: never let this fail a successful checkout.
    await _persist_tenant_billing_provider(session)

    updated = await _mark_succeeded(session, subscription.id or "")
    try:
        await record_audit_event(
            actor_id=acting_user_id or "system",
            actor_role="super_admin" if acting_user_id else "system",
            action="checkout.succeeded",
            resource_type="checkout_session",
            resource_id=checkout_id,
            tenant_id=session.tenant_id,
            details={
                "provider": session.provider.value,
                "subscription_id": subscription.id,
                "amount_minor": session.amount_minor,
                "trial_code": session.trial_code,
            },
        )
    except Exception:
        pass
    if not updated:
        raise resource_not_found(resource="CheckoutSession", resource_id=checkout_id)
    return updated


async def maybe_complete_checkout_from_reference(
    reference: str, outcome: str = "success"
) -> Optional[CheckoutSessionOut]:
    """Called from external-provider webhook handlers. Returns ``None`` when
    no checkout matches the reference (e.g. non-subscription payments).

    Falls through to addon activation when the reference matches a
    ``tenant_addons`` row instead — this is the single entry point
    every payment provider's webhook plumbing calls, so addon
    purchases activate automatically without per-provider plumbing
    duplication.
    """
    if not reference:
        return None
    session = await get_checkout_by_reference(reference)
    if session and session.id is not None:
        return await complete_checkout(checkout_id=session.id, outcome=outcome)

    # Addon purchase fallback. The reference matches the
    # ``payment_reference`` we minted in
    # ``services.addon_service.initiate_addon_purchase`` (always
    # prefixed ``addon_``). Activate on success; cancel on failure.
    if reference.startswith("addon_"):
        try:
            from services.addon_service import (
                activate_tenant_addon_by_reference,
                cancel_tenant_addon,
            )
            from repositories.tenant_addon_repo import (
                get_tenant_addon_by_reference,
            )

            if outcome == "success":
                await activate_tenant_addon_by_reference(payment_reference=reference)
            else:
                row = await get_tenant_addon_by_reference(reference)
                if row and row.id:
                    await cancel_tenant_addon(
                        row.id,
                        actor_id=row.created_by_user_id or "system",
                        reason=f"payment {outcome}",
                    )
        except Exception:
            logger.warning(
                "addon webhook fallback failed for reference=%s",
                reference,
                exc_info=True,
            )
    return None
