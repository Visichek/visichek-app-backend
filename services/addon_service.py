"""Addon catalog management + tenant purchase flow.

This is the only place that knows how an addon's catalog row gets
turned into:

  1. A pending ``tenant_addons`` row with a checkout URL,
  2. An active ``tenant_addons`` row once payment confirms,
  3. An expired / cancelled ``tenant_addons`` row when refunded or
     time-bound validity runs out.

Provider selection mirrors checkout_service: prefer Stripe → fall back
to Flutterwave → fall back to the in-app ``app`` provider.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import List, Optional, Tuple

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

_PROVIDER_PREFERENCE: Tuple[str, ...] = ("stripe", "flutterwave", "app")


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


async def list_public_addons(
    *, kind: Optional[AddonKind] = None
) -> List[AddonOut]:
    """Return active addons available for purchase.

    Filters by ``kind`` when supplied so a UI page rendering "buy
    more storage" only sees ``storage_extension`` rows.
    """
    filt: dict = {"status": AddonStatus.ACTIVE.value}
    if kind is not None:
        filt["kind"] = kind.value
    return await list_addons(filt, skip=0, limit=200)


async def get_public_addon(addon_id: str) -> AddonOut:
    addon = await get_addon_by_id(addon_id)
    if addon is None or addon.status != AddonStatus.ACTIVE:
        raise resource_not_found(resource="Addon", resource_id=addon_id)
    return addon


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
            logger.warning(
                "addon: provider '%s' create_intent failed: %s", name, err
            )
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

    if quantity < 1:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="quantity must be >= 1",
        )
    if addon.max_units_per_purchase is not None and quantity > addon.max_units_per_purchase:
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
        unit_price_snapshot=addon.unit_price,
        currency_snapshot=addon.currency,
        benefit_snapshot=addon.benefit_per_unit,
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
    addon = await get_addon_by_id(row.addon_id)
    validity_days = (
        addon.validity_days if addon else None
    )  # None = perpetual
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
        raise resource_not_found(
            resource="TenantAddon", resource_id=tenant_addon_id
        )
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
    return updated


# ─── Read APIs ──────────────────────────────────────────────────────


async def list_tenant_addon_history(
    tenant_id: str, *, skip: int = 0, limit: int = 200
) -> List[TenantAddonOut]:
    return await list_tenant_addons(
        {"tenant_id": tenant_id}, skip=skip, limit=limit
    )


async def get_tenant_addon_detail(
    tenant_id: str, tenant_addon_id: str
) -> TenantAddonOut:
    row = await get_tenant_addon_by_id(tenant_addon_id)
    if row is None or row.tenant_id != tenant_id:
        raise resource_not_found(
            resource="TenantAddon", resource_id=tenant_addon_id
        )
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
    from repositories.tenant_addon_repo import expire_due_tenant_addons

    return await expire_due_tenant_addons()
