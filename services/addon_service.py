"""Addon catalog management + tenant purchase flow.

This is the only place that knows how an addon's catalog row gets
turned into:

  1. A pending ``tenant_addons`` row with a checkout URL,
  2. An active ``tenant_addons`` row once payment confirms,
  3. An expired / cancelled ``tenant_addons`` row when refunded or
     time-bound validity runs out.

Provider selection mirrors checkout_service: prefer Stripe → fall back
to Flutterwave → Paystack → fall back to the in-app ``app`` provider.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, List, Optional, Tuple

from bson import ObjectId

from core.errors import AppException, ErrorCode, resource_not_found
from core.payments import PaymentIntentRequest, PaymentManager
from core.payments.types import PaymentProviderName
from repositories.addon_repo import (
    create_addon,
    delete_addon,
    get_addon_by_id,
    list_addons,
    update_addon,
)
from repositories.tenant_addon_repo import (
    create_tenant_addon,
    get_tenant_addon_by_id,
    get_tenant_addon_by_reference,
    list_active_for_tenant,
    list_tenant_addons,
    update_tenant_addon,
)
from schemas.addon_schema import (
    AddonCreate,
    AddonKind,
    AddonOut,
    AddonStatus,
    AddonUpdate,
    TenantAddonCreate,
    TenantAddonOut,
    TenantAddonStatus,
    TenantAddonUpdate,
)
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)

_PROVIDER_PREFERENCE: Tuple[str, ...] = ("stripe", "flutterwave", "paystack", "app")

# Slug of the derived branch-quota addon (see services/addon_bootstrap.py).
# Purchasable only by tenants on the Premium tier.
ADDITIONAL_BRANCH_SLUG = "additional-branch"


# ─── Derived pricing ─────────────────────────────────────────────────


async def resolve_derived_addon_price(derived_from: dict[str, Any]) -> Optional[float]:
    """Resolve a ``derived_from`` spec against the CURRENT live plan.

    ``derived_from`` shape: ``{"plan": "premium", "field":
    "base_price_monthly", "multiplier": 0.8}``. Returns ``None`` (never
    raises) when the referenced plan or field can't be resolved so
    callers can fall back to the addon's cached ``unit_price``.
    """
    plan_slug = derived_from.get("plan")
    field = derived_from.get("field")
    multiplier = derived_from.get("multiplier", 1.0)
    if not plan_slug or not field:
        return None
    try:
        from repositories.plan_repo import get_plan

        plan = await get_plan({"name": plan_slug})
    except Exception:
        logger.exception("addon: derived price plan lookup failed for %s", plan_slug)
        return None
    if plan is None:
        return None
    base_value = getattr(plan, field, None)
    if base_value is None:
        return None
    try:
        return float(round(float(base_value) * float(multiplier)))
    except (TypeError, ValueError):
        return None


async def resolve_addon_unit_price(addon: AddonOut) -> float:
    """The addon's effective per-unit price right now.

    ``fixed`` addons: the stored ``unit_price`` is authoritative.
    ``derived`` addons: live-resolve from ``derived_from`` against the
    referenced plan's current price, rounded to whole naira; falls
    back to the cached ``unit_price`` display value if the referenced
    plan/field can't be resolved.

    Called at catalog read, purchase, and (Task 8) renewal — never
    baked into a long-lived cache, so admin price changes on the
    referenced plan take effect on the very next call.
    """
    if addon.pricing_mode != "derived" or not addon.derived_from:
        return addon.unit_price
    resolved = await resolve_derived_addon_price(addon.derived_from)
    return resolved if resolved is not None else addon.unit_price


def _addon_period_days(addon: AddonOut) -> Optional[int]:
    """Validity length in days for one purchase/renewal cycle.

    Recurring addons derive their period from ``billing_cycle``
    (``validity_days`` is ignored for them); one-time addons use
    ``validity_days`` directly (``None`` = perpetual).
    """
    if addon.recurring:
        return 365 if addon.billing_cycle == "yearly" else 30
    return addon.validity_days


async def _require_premium_tier(tenant_id: str) -> None:
    """Raise 403 unless the tenant's effective plan tier is Premium.

    Gates purchase of the ``additional-branch`` addon — buying more
    branch capacity only makes sense once on Premium.
    """
    from services.plan_cache_service import resolve_tenant_plan

    resolved = await resolve_tenant_plan(tenant_id)
    tier_value: Any = resolved.get("tier") if resolved else None
    tier = (
        tier_value.value
        if tier_value is not None and hasattr(tier_value, "value")
        else str(tier_value or "")
    )
    if tier != "premium":
        raise AppException(
            status_code=403,
            code=ErrorCode.FEATURE_DISABLED,
            message=(
                "Additional branches are only available on the Premium plan. "
                "Upgrade to Premium to purchase more branches."
            ),
        )


async def resync_derived_addon_prices(plan_name: str) -> int:
    """Recompute + persist ``unit_price`` for every addon derived from
    ``plan_name``.

    Called as a post-write hook whenever that plan is updated (see
    ``services.plan_service.update_plan_by_id``) so the catalog's
    display-cache price never drifts far from the live derived value.
    Best-effort per row — one bad row never blocks the others.
    """
    rows = await list_addons({"pricing_mode": "derived", "derived_from.plan": plan_name})
    updated = 0
    for row in rows:
        if not row.id or not row.derived_from:
            continue
        resolved = await resolve_derived_addon_price(row.derived_from)
        if resolved is None or resolved == row.unit_price:
            continue
        try:
            await update_addon(row.id, AddonUpdate(unit_price=resolved))
            updated += 1
        except Exception:
            logger.exception(
                "addon: failed to resync derived price for addon %s", row.id
            )
    return updated


async def _invalidate_tenant_addon_caches(tenant_id: Optional[str]) -> None:
    """Drop every cache that could be serving stale addon-derived entitlements.

    Called whenever a tenant's active addon set changes (activate, cancel,
    expiry sweep): the resolved-plan cache (WS0.1 bakes addon benefits into
    it), the ``usage.my_usage`` precompute, and the ``tenant_usage`` entity
    cache. Best-effort — a cache miss just means the next read is a little
    slower, never a correctness issue.
    """
    if not tenant_id:
        return
    from core.queue.entity_cache import invalidate_entity
    from core.queue.precompute import delete_precompute
    from services.plan_cache_service import invalidate_tenant_plan_cache

    try:
        await invalidate_tenant_plan_cache(tenant_id)
    except Exception:
        pass
    try:
        delete_precompute("usage.my_usage", tenant_id=tenant_id)
    except Exception:
        pass
    try:
        invalidate_entity("tenant_usage", tenant_id)
    except Exception:
        pass


# ─── Catalog admin ──────────────────────────────────────────────────


async def admin_create_addon(
    payload: AddonCreate,
    *,
    actor_id: Optional[str] = None,
    request_id: Optional[str] = None,
    preassigned_id: Optional[str] = None,
) -> AddonOut:
    saved = await create_addon(payload, preassigned_id=preassigned_id)
    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role="admin",
            action="addon.created",
            resource_type="addon",
            resource_id=saved.id or "",
            tenant_id=None,
            details={
                "kind": saved.kind.value,
                "unit_price": saved.unit_price,
                "currency": saved.currency,
            },
            request_id=request_id,
        )
    except Exception:
        pass
    return saved


async def admin_update_addon(
    addon_id: str,
    payload: AddonUpdate,
    *,
    actor_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> AddonOut:
    saved = await update_addon(addon_id, payload)
    if saved is None:
        raise resource_not_found(resource="Addon", resource_id=addon_id)
    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role="admin",
            action="addon.updated",
            resource_type="addon",
            resource_id=saved.id or "",
            tenant_id=None,
            details=payload.model_dump(exclude_unset=True),
            request_id=request_id,
        )
    except Exception:
        pass
    return saved


async def admin_delete_addon(
    addon_id: str,
    *,
    actor_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    addon = await get_addon_by_id(addon_id)
    if addon is None:
        raise resource_not_found(resource="Addon", resource_id=addon_id)
    deleted = await delete_addon(addon_id)
    if deleted == 0:
        raise resource_not_found(resource="Addon", resource_id=addon_id)
    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role="admin",
            action="addon.deleted",
            resource_type="addon",
            resource_id=addon_id,
            tenant_id=None,
            details={"kind": addon.kind.value},
            request_id=request_id,
        )
    except Exception:
        pass


# ─── Public catalog reads ───────────────────────────────────────────


async def _with_live_price(addon: AddonOut) -> AddonOut:
    """Return ``addon`` with ``unit_price`` overridden to the live
    resolved price for derived addons (does not persist)."""
    if addon.pricing_mode != "derived":
        return addon
    live_price = await resolve_addon_unit_price(addon)
    if live_price == addon.unit_price:
        return addon
    return addon.model_copy(update={"unit_price": live_price})


async def list_public_addons(*, kind: Optional[AddonKind] = None) -> List[AddonOut]:
    """Return active addons available for purchase.

    Filters by ``kind`` when supplied so a UI page rendering "buy
    more storage" only sees ``storage_extension`` rows. Derived-price
    rows are returned with their live-resolved ``unit_price`` so the
    catalog never shows a stale cached number.
    """
    filt: dict = {"status": AddonStatus.ACTIVE.value}
    if kind is not None:
        filt["kind"] = kind.value
    rows = await list_addons(filt, skip=0, limit=200)
    return list(await asyncio.gather(*(_with_live_price(row) for row in rows)))


async def get_public_addon(addon_id: str) -> AddonOut:
    addon = await get_addon_by_id(addon_id)
    if addon is None or addon.status != AddonStatus.ACTIVE:
        raise resource_not_found(resource="Addon", resource_id=addon_id)
    return await _with_live_price(addon)


# ─── Purchase flow ──────────────────────────────────────────────────


def _to_minor(amount: float) -> int:
    return int(round(amount * 100))


def _select_provider(preferred: Optional[str]) -> str:
    manager = PaymentManager.get_instance()
    if preferred and manager.has_provider(preferred):
        return preferred
    for candidate in _PROVIDER_PREFERENCE:
        if manager.has_provider(candidate):
            return candidate
    return PaymentProviderName.APP.value


def _create_intent_with_fallback(
    initial_provider: str, intent_request: PaymentIntentRequest
):
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
            return name, intent
        except Exception as err:
            logger.warning("addon: provider '%s' create_intent failed: %s", name, err)
            last_error = err

    raise AppException(
        status_code=502,
        code=ErrorCode.PAYMENT_PROVIDER_ERROR,
        message="All payment providers failed to create an addon checkout intent",
        details={"error": str(last_error) if last_error else None},
    )


async def initiate_addon_purchase(
    *,
    tenant_id: str,
    addon_id: str,
    quantity: int,
    actor_id: str,
    preferred_provider: Optional[str] = None,
    customer_email: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TenantAddonOut:
    """Create a pending tenant_addons row + checkout URL.

    The row stays in ``status=pending`` until the payment webhook
    fires ``activate_tenant_addon_by_reference``. The frontend
    polls ``GET /v1/addons/me/{tenant_addon_id}`` (or relies on the
    webhook side effect to flip status) before counting the benefit
    toward the tenant's quota.
    """
    if not ObjectId.is_valid(tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)
    addon = await get_public_addon(addon_id)

    if addon.slug == ADDITIONAL_BRANCH_SLUG:
        await _require_premium_tier(tenant_id)

    if quantity < 1:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="quantity must be >= 1",
        )
    if (
        addon.max_units_per_purchase is not None
        and quantity > addon.max_units_per_purchase
    ):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=(
                f"This addon caps purchases at {addon.max_units_per_purchase} "
                "units per checkout."
            ),
            details={"max_units_per_purchase": addon.max_units_per_purchase},
        )

    amount = addon.unit_price * quantity
    reference = f"addon_{uuid.uuid4().hex}"
    chosen = _select_provider(preferred_provider)
    provider_name, intent = _create_intent_with_fallback(
        chosen,
        PaymentIntentRequest(
            amount_minor=_to_minor(amount),
            currency=addon.currency,
            reference=reference,
            customer_email=customer_email,
            metadata={
                "kind": "addon_purchase",
                "tenant_id": tenant_id,
                "addon_id": addon.id or "",
                "addon_kind": addon.kind.value,
                "quantity": str(quantity),
            },
        ),
    )

    payload = TenantAddonCreate(
        tenant_id=tenant_id,
        addon_id=addon.id or "",
        addon_kind=addon.kind,
        quantity=quantity,
        # ``addon.unit_price`` here is already the live-resolved value
        # (get_public_addon overrides it for derived addons) — snapshot
        # it now so a catalog price change between purchase and webhook
        # activation can't change what's charged/renewed.
        unit_price_snapshot=addon.unit_price,
        currency_snapshot=addon.currency,
        benefit_snapshot=addon.benefit_per_unit,
        recurring_snapshot=addon.recurring,
        validity_days_snapshot=_addon_period_days(addon),
        billing_cycle_snapshot=addon.billing_cycle if addon.recurring else None,
        status=TenantAddonStatus.PENDING,
        payment_provider=provider_name,
        payment_reference=reference,
        checkout_url=intent.checkout_url or "",
        created_by_user_id=actor_id,
    )
    saved = await create_tenant_addon(payload)

    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role="super_admin",
            action="addon.purchase_initiated",
            resource_type="tenant_addon",
            resource_id=saved.id or "",
            tenant_id=tenant_id,
            details={
                "addon_id": addon.id,
                "addon_kind": addon.kind.value,
                "quantity": quantity,
                "amount": amount,
                "currency": addon.currency,
                "provider": provider_name,
                "payment_reference": reference,
            },
            request_id=request_id,
        )
    except Exception:
        pass

    return saved


async def activate_tenant_addon_by_reference(
    *,
    payment_reference: str,
    completed_at: Optional[int] = None,
) -> Optional[TenantAddonOut]:
    """Mark the addon row paid and set its expiry.

    Called from the addon payment webhook handler. Idempotent — if
    the row is already ``active`` it's returned untouched; the
    caller may safely retry on duplicate webhook deliveries.
    """
    row = await get_tenant_addon_by_reference(payment_reference)
    if row is None:
        return None
    if row.status == TenantAddonStatus.ACTIVE:
        return row

    now = completed_at or int(time.time())
    if row.recurring_snapshot:
        # Recurring addons ALWAYS derive expires_at from the purchase-time
        # snapshot, never the current catalog row — the catalog row's
        # validity_days is meaningless for recurring addons anyway (their
        # period comes from billing_cycle) and re-reading it here is
        # exactly the quirk this snapshot exists to avoid.
        validity_days = row.validity_days_snapshot
    else:
        addon = await get_addon_by_id(row.addon_id)
        validity_days = addon.validity_days if addon else None  # None = perpetual
    expires_at = now + validity_days * 86400 if validity_days else None

    update = TenantAddonUpdate(
        status=TenantAddonStatus.ACTIVE,
        purchased_at=now,
        expires_at=expires_at,
        completed_at=now,
    )
    updated = await update_tenant_addon(row.id or "", update)

    try:
        await record_audit_event(
            actor_id=row.created_by_user_id or "system",
            actor_role="super_admin",
            action="addon.activated",
            resource_type="tenant_addon",
            resource_id=row.id or "",
            tenant_id=row.tenant_id,
            details={
                "addon_id": row.addon_id,
                "addon_kind": row.addon_kind.value,
                "expires_at": expires_at,
                "quantity": row.quantity,
            },
        )
    except Exception:
        pass

    if updated is not None:
        await _invalidate_tenant_addon_caches(updated.tenant_id)

    return updated


async def cancel_tenant_addon(
    tenant_addon_id: str,
    *,
    actor_id: str,
    reason: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TenantAddonOut:
    """Cancel a tenant addon (refund / chargeback / manual revoke).

    Active addons that are cancelled stop being counted immediately.
    Pending addons that are cancelled abandon the checkout (the
    provider intent times out on its own).
    """
    row = await get_tenant_addon_by_id(tenant_addon_id)
    if row is None:
        raise resource_not_found(resource="TenantAddon", resource_id=tenant_addon_id)
    if row.status in (TenantAddonStatus.CANCELLED, TenantAddonStatus.EXPIRED):
        return row

    now = int(time.time())
    updated = await update_tenant_addon(
        tenant_addon_id,
        TenantAddonUpdate(
            status=TenantAddonStatus.CANCELLED,
            cancelled_at=now,
            cancellation_reason=reason,
        ),
    )
    assert updated is not None

    try:
        await record_audit_event(
            actor_id=actor_id,
            actor_role="super_admin",
            action="addon.cancelled",
            resource_type="tenant_addon",
            resource_id=tenant_addon_id,
            tenant_id=row.tenant_id,
            details={"reason": reason or ""},
            request_id=request_id,
        )
    except Exception:
        pass

    await _invalidate_tenant_addon_caches(updated.tenant_id)

    return updated


# ─── Read APIs ──────────────────────────────────────────────────────


async def list_tenant_addon_history(
    tenant_id: str, *, skip: int = 0, limit: int = 200
) -> List[TenantAddonOut]:
    return await list_tenant_addons({"tenant_id": tenant_id}, skip=skip, limit=limit)


async def get_tenant_addon_detail(
    tenant_id: str, tenant_addon_id: str
) -> TenantAddonOut:
    row = await get_tenant_addon_by_id(tenant_addon_id)
    if row is None or row.tenant_id != tenant_id:
        raise resource_not_found(resource="TenantAddon", resource_id=tenant_addon_id)
    return row


async def list_active_addons_for_tenant(
    tenant_id: str, *, kind: Optional[AddonKind] = None
) -> List[TenantAddonOut]:
    return await list_active_for_tenant(
        tenant_id, addon_kind=kind.value if kind else None
    )


# ─── Scheduler entrypoint ──────────────────────────────────────────


async def expire_due_addons() -> int:
    """Scheduled job: flip every active addon past its expiry to ``expired``.

    Registered as ``services.addon_service:expire_due_addons`` in
    ``main.py`` via APScheduler so a tenant's storage budget shrinks
    automatically when an addon's validity window runs out. Returns
    the count of rows transitioned.
    """
    from repositories.tenant_addon_repo import (
        expire_due_tenant_addons,
        list_due_tenant_ids_for_expiry,
    )

    # Snapshot which tenants are affected BEFORE the flip so we know who to
    # invalidate — the update_many below no longer reports individual rows.
    tenant_ids = await list_due_tenant_ids_for_expiry()
    count = await expire_due_tenant_addons()
    for tenant_id in tenant_ids:
        await _invalidate_tenant_addon_caches(tenant_id)
    return count
