"""Hourly renewal sweep for recurring add-ons (e.g. ``additional-branch``).

Mirrors ``services.renewal_service`` (subscription renewal + dunning) but
for ``tenant_addons`` rows with ``recurring_snapshot=True``. Reuses the same
payment-provider selection (``renewal_service._get_provider_for_tenant``)
and the same tokenized "charge the card on file" path
(``PaymentProvider.charge_recurring`` — Paystack ``charge_authorization``
or Stripe off-session PaymentIntent).

CRITICAL — do NOT repeat the pre-existing dunning/renewal fake-success bug
(see visichek-plan-verification-notes.md "Pre-existing bugs"): a renewal is
a success ONLY when a real charge succeeds. There is no ``create_intent``
fallback here. If the tenant has no saved chargeable instrument for their
provider, that is a FAILED renewal — it goes through the same
grace-then-expire path as a declined charge, never a synthetic success.

Grace behaviour: on a failed renewal, the row stays ``active`` (still
granting benefit) through a dunning-style retry schedule (reusing
``settings.dunning_retry_days`` / ``max_dunning_attempts``) — ``expires_at``
is pushed out to the next retry point so the read-time expiry filter in
``tenant_addon_repo.list_active_for_tenant`` doesn't cut the benefit before
the grace window elapses. Once attempts are exhausted, the row is marked
``expired`` and caches (+ the branch lock walk) are invalidated via
``addon_service._invalidate_tenant_addon_caches``.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from bson import ObjectId

from core.payments import PaymentManager
from core.payments.types import PaymentProviderName, PaymentStatus
from core.settings import get_settings
from repositories.addon_repo import get_addon_by_id
from repositories.tenant_addon_repo import (
    list_due_recurring_tenant_addons,
    update_tenant_addon,
)
from repositories.tenant_repo import get_tenant
from schemas.addon_schema import TenantAddonOut, TenantAddonStatus, TenantAddonUpdate
from services.addon_service import _invalidate_tenant_addon_caches, resolve_addon_unit_price
from services.audit_service import record_audit_event
from services.invoice_service import generate_invoice
from services.renewal_service import _get_provider_for_tenant

logger = logging.getLogger(__name__)

_CYCLE_DAYS = {"monthly": 30, "yearly": 365}


async def _charge_addon_recurring(
    *,
    provider,
    provider_name: str,
    tenant_id: str,
    amount_minor: int,
    currency: str,
    reference: str,
    metadata: dict,
) -> Optional[bool]:
    """Charge the tenant's saved instrument for an addon renewal.

    Same tri-state contract as
    ``renewal_service._charge_recurring_if_possible``: ``True`` = charged
    successfully, ``False`` = attempted and declined/errored, ``None`` = no
    saved instrument for this provider (nothing to charge). The caller
    treats ``None`` exactly like ``False`` — there is no intent-creation
    fallback for addon renewals.
    """
    if not ObjectId.is_valid(tenant_id):
        return None
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        return None

    instrument_ref: Optional[str] = None
    customer_ref: Optional[str] = None
    email: str = ""
    if provider_name == PaymentProviderName.PAYSTACK.value:
        instrument_ref = getattr(tenant, "paystack_authorization_code", None)
        email = getattr(tenant, "paystack_auth_email", None) or ""
        if not (isinstance(instrument_ref, str) and instrument_ref and email):
            return None
    elif provider_name == PaymentProviderName.STRIPE.value:
        instrument_ref = getattr(tenant, "stripe_payment_method_id", None)
        customer_ref = getattr(tenant, "stripe_customer_id", None)
        if not (
            isinstance(instrument_ref, str)
            and instrument_ref
            and isinstance(customer_ref, str)
            and customer_ref
        ):
            return None
    else:
        # Flutterwave (and any other provider without tokenized recurring
        # charging) has no saved-instrument path — charge_recurring raises
        # 501 there. Nothing to charge.
        return None

    try:
        tx = provider.charge_recurring(
            instrument_ref=instrument_ref,
            email=email,
            amount_minor=amount_minor,
            currency=currency,
            reference=reference,
            customer_ref=customer_ref,
            metadata=metadata,
        )
    except Exception as e:
        logger.warning(
            "addon renewal: recurring charge errored for tenant %s: %s", tenant_id, e
        )
        return False
    return tx.status == PaymentStatus.SUCCEEDED


async def renew_due_addons() -> dict:
    """Top-level function called by APScheduler hourly.

    Processes every recurring ``tenant_addons`` row whose ``expires_at``
    (which doubles as "next action due at" for recurring rows — either the
    normal renewal date or a pending grace-retry date) has passed.
    """
    now = int(time.time())
    logger.info("Starting addon renewal check")

    renewed = 0
    grace = 0
    expired = 0
    errors = 0

    try:
        rows = await list_due_recurring_tenant_addons(now)
    except Exception:
        logger.exception("addon renewal: failed to list due rows")
        return {"renewed": 0, "grace": 0, "expired": 0, "errors": 0, "total_processed": 0}

    for row in rows:
        try:
            outcome = await _attempt_addon_renewal(row, now=now)
        except Exception:
            logger.exception("addon renewal: error for tenant_addon %s", row.id)
            errors += 1
            continue
        if outcome == "renewed":
            renewed += 1
        elif outcome == "grace":
            grace += 1
        elif outcome == "expired":
            expired += 1

    logger.info(
        "Addon renewal check complete: %s renewed, %s grace, %s expired, %s errors",
        renewed,
        grace,
        expired,
        errors,
    )
    return {
        "renewed": renewed,
        "grace": grace,
        "expired": expired,
        "errors": errors,
        "total_processed": len(rows),
    }


async def _attempt_addon_renewal(row: TenantAddonOut, *, now: int) -> str:
    settings = get_settings()
    max_attempts = settings.max_dunning_attempts
    retry_days = settings.dunning_retry_days

    # D2: re-resolve the derived price from the CURRENT Premium plan so an
    # admin price change flows through at renewal, not just at purchase.
    addon = await get_addon_by_id(row.addon_id)
    resolved_price = (
        await resolve_addon_unit_price(addon) if addon is not None else row.unit_price_snapshot
    )

    try:
        payment_manager = PaymentManager.get_instance()
    except RuntimeError as e:
        logger.error("addon renewal: payment manager not configured: %s", e)
        return await _fail_renewal(row, now=now, max_attempts=max_attempts, retry_days=retry_days)

    try:
        provider_name = await _get_provider_for_tenant(row.tenant_id)
        provider = payment_manager.get_provider(provider_name)
    except Exception as e:
        logger.error("addon renewal: failed to resolve provider for tenant %s: %s", row.tenant_id, e)
        return await _fail_renewal(row, now=now, max_attempts=max_attempts, retry_days=retry_days)

    amount_minor = int(round(resolved_price * row.quantity * 100))
    reference = f"addon-renewal-{row.id}-{now}"
    metadata = {
        "tenant_addon_id": row.id or "",
        "tenant_id": row.tenant_id,
        "addon_id": row.addon_id,
        "renewal": True,
    }

    charged = await _charge_addon_recurring(
        provider=provider,
        provider_name=provider_name,
        tenant_id=row.tenant_id,
        amount_minor=amount_minor,
        currency=row.currency_snapshot,
        reference=reference,
        metadata=metadata,
    )

    # A renewal is a success ONLY when a real charge succeeded. `charged`
    # is `None` when there's no saved instrument to charge at all — that
    # is a FAILED renewal (grace -> expired), never treated as success.
    if charged is True:
        return await _succeed_renewal(row, now=now, resolved_price=resolved_price)
    return await _fail_renewal(row, now=now, max_attempts=max_attempts, retry_days=retry_days)


async def _succeed_renewal(row: TenantAddonOut, *, now: int, resolved_price: float) -> str:
    cycle_days = _CYCLE_DAYS.get(row.billing_cycle_snapshot or "monthly", 30)
    new_expires_at = now + cycle_days * 86400

    updated = await update_tenant_addon(
        row.id or "",
        TenantAddonUpdate(
            status=TenantAddonStatus.ACTIVE,
            expires_at=new_expires_at,
            unit_price_snapshot=resolved_price,
            renewal_attempts=0,
            next_retry_at=None,
            last_renewal_attempt_at=now,
            completed_at=now,
        ),
    )
    if updated is None:
        # Persisting the update failed — do not report success upstream.
        logger.error("addon renewal: failed to persist renewal for %s", row.id)
        return "grace"

    amount_minor = int(round(resolved_price * row.quantity * 100))
    try:
        await generate_invoice(
            tenant_id=row.tenant_id,
            subscription_id=f"addon:{row.id}",
            plan_name="Add-on renewal",
            billing_cycle=row.billing_cycle_snapshot or "monthly",
            currency=row.currency_snapshot,
            base_price_minor=amount_minor,
            discount_minor=0,
            period_start=now,
            period_end=new_expires_at,
        )
    except Exception as e:
        logger.warning("addon renewal: invoice generation failed for %s: %s", row.id, e)

    try:
        await record_audit_event(
            actor_id="system",
            actor_role="super_admin",
            action="addon.renewed",
            resource_type="tenant_addon",
            resource_id=row.id or "",
            tenant_id=row.tenant_id,
            details={
                "expires_at": new_expires_at,
                "unit_price": resolved_price,
                "quantity": row.quantity,
            },
        )
    except Exception:
        pass

    await _invalidate_tenant_addon_caches(row.tenant_id)
    logger.info("Addon renewal successful for tenant_addon %s", row.id)
    return "renewed"


async def _fail_renewal(
    row: TenantAddonOut, *, now: int, max_attempts: int, retry_days: tuple
) -> str:
    attempts = row.renewal_attempts + 1

    if attempts > max_attempts:
        await update_tenant_addon(
            row.id or "",
            TenantAddonUpdate(
                status=TenantAddonStatus.EXPIRED,
                renewal_attempts=attempts,
                last_renewal_attempt_at=now,
                next_retry_at=None,
            ),
        )
        try:
            await record_audit_event(
                actor_id="system",
                actor_role="super_admin",
                action="addon.renewal_failed_expired",
                resource_type="tenant_addon",
                resource_id=row.id or "",
                tenant_id=row.tenant_id,
                details={"attempts": attempts},
            )
        except Exception:
            pass
        await _invalidate_tenant_addon_caches(row.tenant_id)
        logger.warning(
            "Addon renewal exhausted retries for tenant_addon %s — expired", row.id
        )
        return "expired"

    idx = min(attempts - 1, len(retry_days) - 1)
    next_retry = now + retry_days[idx] * 86400
    # Keep the row ACTIVE (still granting benefit) through the grace
    # window: push expires_at out to the next retry point so the
    # read-time filter in list_active_for_tenant doesn't cut the benefit
    # before the grace window elapses.
    await update_tenant_addon(
        row.id or "",
        TenantAddonUpdate(
            renewal_attempts=attempts,
            last_renewal_attempt_at=now,
            next_retry_at=next_retry,
            expires_at=max(next_retry, now + 1),
        ),
    )
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="super_admin",
            action="addon.renewal_failed_grace",
            resource_type="tenant_addon",
            resource_id=row.id or "",
            tenant_id=row.tenant_id,
            details={"attempts": attempts, "next_retry_at": next_retry},
        )
    except Exception:
        pass
    logger.warning(
        "Addon renewal declined for tenant_addon %s — grace attempt %s/%s",
        row.id,
        attempts,
        max_attempts,
    )
    return "grace"
