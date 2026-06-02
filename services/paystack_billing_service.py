"""Paystack recurring-billing helpers (app-driven model).

Owns the pieces that make Paystack work as a *charging instrument* behind the
app's own plan/subscription/renewal logic:

- ``store_tenant_authorization`` — persist the reusable card authorization
  captured from the first successful charge, so the renewal scheduler can
  charge the tenant server-side later (no frontend).
- ``initiate_trial_card_capture`` — start a free trial that auto-converts: it
  charges the Paystack minimum (e.g. ₦50), which tokenizes the card; the
  charge is refunded once captured (see ``complete_trial_tokenization``).
- ``complete_trial_tokenization`` — called from the webhook when the tiny
  tokenization charge clears: refund it and start the trial subscription.

Paystack cannot charge ``0`` (amount must be a positive integer, minimum
~₦50), which is why a free trial that should auto-charge later needs this
charge-and-refund tokenization step rather than a $0 setup.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from bson import ObjectId

from core.errors import AppException, ErrorCode
from core.payments import PaymentIntentRequest, PaymentManager
from core.payments.types import PaymentProviderName
from repositories.plan_repo import get_plan
from repositories.tenant_repo import update_tenant
from schemas.subscription_schema import BillingCycle
from schemas.tenant_schema import TenantUpdate

logger = logging.getLogger(__name__)

# Minimum first-charge amount per currency, in the minor unit (kobo, pesewa,
# cent). Used for card tokenization. Source: Paystack recurring-charges docs.
_MIN_TOKENIZATION_AMOUNT_MINOR: dict[str, int] = {
    "NGN": 5000,  # ₦50.00
    "GHS": 10,  # GHS 0.10
    "ZAR": 100,  # R1.00
    "KES": 300,  # KES 3.00
    "USD": 200,  # $2.00
}
_DEFAULT_MIN_TOKENIZATION_MINOR = 5000

# Marker stored in transaction metadata so the webhook can recognise a
# tokenization charge (refund + start trial) vs a normal payment.
TRIAL_TOKENIZATION_PURPOSE = "trial_tokenization"


def min_tokenization_amount(currency: str) -> int:
    """Return the minimum tokenization charge for a currency, in minor units."""
    return _MIN_TOKENIZATION_AMOUNT_MINOR.get(
        (currency or "").upper(), _DEFAULT_MIN_TOKENIZATION_MINOR
    )


async def store_tenant_authorization(
    *,
    tenant_id: str,
    authorization: dict,
    email: Optional[str],
) -> bool:
    """Persist a reusable Paystack card authorization on the tenant.

    Best-effort and idempotent: only stores when the authorization is marked
    ``reusable`` and carries an ``authorization_code``. Returns True if stored.
    """
    if not authorization or not isinstance(authorization, dict):
        return False
    code = authorization.get("authorization_code")
    if not code or authorization.get("reusable") is not True:
        # A non-reusable instrument (e.g. some bank channels) cannot be charged
        # again; do not store it as a recurring authorization.
        return False
    if not ObjectId.is_valid(tenant_id):
        return False

    try:
        await update_tenant(
            filter_dict={"_id": ObjectId(tenant_id)},
            tenant_data=TenantUpdate(
                paystack_authorization_code=code,
                paystack_auth_email=email,
                paystack_card_brand=authorization.get("brand"),
                paystack_card_last4=authorization.get("last4"),
            ),
        )
        logger.info(
            "Stored Paystack authorization for tenant %s (brand=%s, last4=%s)",
            tenant_id,
            authorization.get("brand"),
            authorization.get("last4"),
        )
        return True
    except Exception:
        logger.warning(
            "Failed to store Paystack authorization for tenant %s",
            tenant_id,
            exc_info=True,
        )
        return False


async def initiate_trial_card_capture(
    *,
    tenant_id: str,
    plan_id: str,
    billing_cycle: BillingCycle,
    trial_days: int,
    email: str,
    redirect_url: Optional[str] = None,
) -> dict:
    """Start a free trial that will auto-charge at the end.

    Charges the Paystack minimum to tokenize the card (refunded on capture),
    and returns the hosted ``authorization_url`` for the frontend to open —
    the same redirect the normal checkout already uses, so no new UI.

    The trial subscription itself is NOT created here; it is created when the
    tokenization charge clears (see ``complete_trial_tokenization``), so a
    tenant who abandons the card form never gets a dangling trial.
    """
    if not ObjectId.is_valid(plan_id):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Invalid plan_id",
        )
    plan = await get_plan({"_id": ObjectId(plan_id)})
    if not plan:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Plan not found",
            details={"plan_id": plan_id},
        )

    # Fall back to the plan's configured trial length when none is supplied.
    if trial_days <= 0:
        trial_days = int(getattr(plan, "trial_days", 0) or 0)

    manager = PaymentManager.get_instance()
    if not manager.has_provider(PaymentProviderName.PAYSTACK.value):
        raise AppException(
            status_code=503,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Paystack is not configured for trial card capture",
        )
    provider = manager.get_provider(PaymentProviderName.PAYSTACK.value)

    currency = plan.currency
    amount_minor = min_tokenization_amount(currency)
    reference = f"trialcap_{uuid.uuid4().hex}"

    intent = provider.create_intent(
        PaymentIntentRequest(
            amount_minor=amount_minor,
            currency=currency,
            reference=reference,
            customer_email=email,
            metadata={
                "purpose": TRIAL_TOKENIZATION_PURPOSE,
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "billing_cycle": billing_cycle.value,
                "trial_days": trial_days,
                **({"redirect_url": redirect_url} if redirect_url else {}),
            },
        )
    )
    return {
        "authorization_url": intent.checkout_url,
        "reference": reference,
        "tokenization_amount_minor": amount_minor,
        "currency": currency,
    }


async def complete_trial_tokenization(payload: dict) -> dict:
    """Handle a cleared trial-tokenization charge from the webhook.

    Refunds the tiny tokenization charge (best-effort) and provisions the
    trial subscription. The card authorization is captured separately by
    ``store_tenant_authorization`` before this runs.
    """
    data = payload.get("data", {}) or {}
    reference = data.get("reference")
    metadata = data.get("metadata") or {}
    tenant_id = metadata.get("tenant_id")
    plan_id = metadata.get("plan_id")
    billing_cycle_raw = metadata.get("billing_cycle") or BillingCycle.MONTHLY.value
    trial_days_raw = metadata.get("trial_days") or 0

    if not (tenant_id and plan_id):
        logger.warning("Trial tokenization missing tenant_id/plan_id metadata")
        return {"handled": False, "reason": "missing_metadata"}

    # Refund the tokenization charge — best-effort, must not block the trial.
    if reference:
        try:
            manager = PaymentManager.get_instance()
            provider = manager.get_provider(PaymentProviderName.PAYSTACK.value)
            provider.refund(reference=reference)
        except Exception:
            logger.warning(
                "Failed to refund trial tokenization charge %s",
                reference,
                exc_info=True,
            )

    try:
        trial_days = int(trial_days_raw)
    except (TypeError, ValueError):
        trial_days = 0
    try:
        billing_cycle = BillingCycle(billing_cycle_raw)
    except ValueError:
        billing_cycle = BillingCycle.MONTHLY

    from services.subscription_service import subscribe_tenant

    subscription = await subscribe_tenant(
        tenant_id=tenant_id,
        plan_id=plan_id,
        billing_cycle=billing_cycle,
        trial_days=trial_days,
    )
    subscription_id = getattr(subscription, "id", None)
    logger.info(
        "Started trial subscription %s for tenant %s after card capture",
        subscription_id,
        tenant_id,
    )

    # When the trial was started through ``create_checkout_session`` (the normal
    # frontend path), a PENDING checkout session exists and a trial code was
    # reserved. Mark the session SUCCEEDED so the UI's poll resolves, and redeem
    # the code. Both are no-ops for the standalone /checkout/trial-card-capture
    # entry point, which creates no session and carries no trial_code.
    if reference:
        try:
            from repositories.checkout_repo import (
                get_checkout_by_reference,
                update_checkout,
            )
            from schemas.checkout_schema import CheckoutSessionUpdate, CheckoutStatus

            sess = await get_checkout_by_reference(reference)
            if sess and sess.id and sess.status == CheckoutStatus.PENDING:
                await update_checkout(
                    sess.id,
                    CheckoutSessionUpdate(
                        status=CheckoutStatus.SUCCEEDED,
                        completed_at=int(time.time()),
                        subscription_id=subscription_id,
                    ),
                )
        except Exception:
            logger.warning(
                "Failed to reconcile checkout session for trial reference %s",
                reference,
                exc_info=True,
            )

    trial_code = metadata.get("trial_code")
    if trial_code and subscription_id:
        try:
            from services.trial_code_service import mark_trial_code_used

            await mark_trial_code_used(
                code=str(trial_code),
                subscription_id=subscription_id,
                tenant_id=tenant_id,
            )
        except Exception:
            logger.warning(
                "Failed to mark trial code USED after tokenization %s",
                reference,
                exc_info=True,
            )

    return {
        "handled": True,
        "action": "trial_started",
        "tenant_id": tenant_id,
        "subscription_id": getattr(subscription, "id", None),
        "trial_ends_at": int(time.time()) + trial_days * 86400 if trial_days else None,
    }
