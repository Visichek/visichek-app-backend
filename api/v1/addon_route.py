"""Addon catalog (admin) + tenant purchase / history (super_admin)
+ webhook activation hook (called from the payment webhook plumbing).

Three router groups in one module:

* ``admin_router``   — application admin manages the catalog under
                       ``/v1/admins/addons``.
* ``public_router``  — anyone can list available addons under
                       ``/v1/addons`` (no auth required so the
                       marketing site can render pricing).
* ``tenant_router``  — tenant super_admin purchases + reviews their
                       own addons under ``/v1/tenants/me/addons``.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, Query, Request, status

from core.errors import auth_permission_denied, resource_not_found
from core.response_envelope import document_response
from repositories.system_user_repo import get_system_user
from bson import ObjectId
from schemas.addon_schema import (
    AddonCreate,
    AddonKind,
    AddonOut,
    AddonPurchaseRequest,
    AddonPurchaseResponse,
    AddonUpdate,
    TenantAddonOut,
)
from security.auth import (
    verify_admin_token,
    verify_super_admin_token,
)
from security.principal import AuthPrincipal
from services.addon_service import (
    activate_tenant_addon_by_reference,
    admin_create_addon,
    admin_delete_addon,
    admin_update_addon,
    cancel_tenant_addon,
    get_public_addon,
    get_tenant_addon_detail,
    initiate_addon_purchase,
    list_active_addons_for_tenant,
    list_public_addons,
    list_tenant_addon_history,
)


admin_router = APIRouter(prefix="/admins/addons", tags=["Addons (Admin)"])
public_router = APIRouter(prefix="/addons", tags=["Addons"])
tenant_router = APIRouter(prefix="/tenants/me/addons", tags=["Addons (Tenant)"])


# ─── Admin catalog management ──────────────────────────────────────


@admin_router.post("", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Addon created",
    status_code=status.HTTP_201_CREATED,
    description="Create a new addon SKU. Application admin only.",
    summary="Create addon",
)
async def admin_create_addon_endpoint(
    payload: AddonCreate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_admin_token),
) -> AddonOut:
    return await admin_create_addon(
        payload,
        actor_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@admin_router.patch("/{addon_id}")
@document_response(
    message="Addon updated",
    description="Partial update to an addon SKU. Application admin only.",
    summary="Update addon",
)
async def admin_update_addon_endpoint(
    addon_id: str,
    payload: AddonUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_admin_token),
) -> AddonOut:
    return await admin_update_addon(
        addon_id,
        payload,
        actor_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@admin_router.delete("/{addon_id}")
@document_response(
    message="Addon deleted",
    description="Delete an addon SKU. Existing tenant_addons rows are NOT affected — only the catalog row goes away. Application admin only.",
    summary="Delete addon",
)
async def admin_delete_addon_endpoint(
    addon_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    await admin_delete_addon(
        addon_id,
        actor_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return {"deleted": True}


@admin_router.get("")
@document_response(
    message="Addons fetched",
    description="List all addon SKUs (catalog). Application admin only.",
    summary="List addons (admin view)",
    include_meta=True,
)
async def admin_list_addons_endpoint(
    kind: Annotated[Optional[AddonKind], Query()] = None,
    _principal: AuthPrincipal = Depends(verify_admin_token),
):
    from repositories.addon_repo import list_addons

    filt: dict = {}
    if kind is not None:
        filt["kind"] = kind.value
    items = await list_addons(filt, skip=0, limit=500)
    return items, {"total": len(items), "skip": 0, "limit": 500}


# ─── Public catalog reads ──────────────────────────────────────────


@public_router.get("")
@document_response(
    message="Available addons retrieved",
    description=(
        "List addon SKUs currently available for purchase. Public — the "
        "marketing site uses this to render a pricing grid. Filter by "
        "``kind`` (e.g. ``storage_extension``) to narrow the result."
    ),
    summary="List public addon catalog",
)
async def list_public_addons_endpoint(
    kind: Annotated[Optional[AddonKind], Query()] = None,
):
    return await list_public_addons(kind=kind)


@public_router.get("/{addon_id}")
@document_response(
    message="Addon retrieved",
    description="Fetch one addon SKU by id. Public.",
    summary="Get addon",
)
async def get_public_addon_endpoint(addon_id: str):
    return await get_public_addon(addon_id)


# ─── Tenant purchase + history ─────────────────────────────────────


@tenant_router.post("/purchase", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Addon purchase initiated",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Initiate an addon purchase. Returns a ``checkout_url`` the "
        "frontend opens for the user to complete payment. The "
        "``tenant_addon_id`` stays in ``status=pending`` until the "
        "payment webhook flips it to ``active`` and the benefit "
        "starts counting toward the tenant's storage / quota budget."
    ),
    summary="Purchase an addon (super_admin)",
)
async def purchase_addon_endpoint(
    payload: AddonPurchaseRequest,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> AddonPurchaseResponse:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        raise auth_permission_denied("POST:/v1/tenants/me/addons/purchase")

    su = (
        await get_system_user({"_id": ObjectId(principal.user_id)})
        if ObjectId.is_valid(principal.user_id)
        else None
    )
    email = getattr(su, "email", None) if su else None

    instance = await initiate_addon_purchase(
        tenant_id=tenant_id,
        addon_id=payload.addon_id,
        quantity=payload.quantity,
        actor_id=principal.user_id,
        preferred_provider=payload.preferred_provider,
        customer_email=email,
        request_id=getattr(request.state, "request_id", None),
    )
    addon = await get_public_addon(payload.addon_id)
    return AddonPurchaseResponse(
        tenant_addon_id=instance.id or "",
        addon_id=addon.id or "",
        addon_name=addon.name,
        addon_kind=addon.kind,
        quantity=payload.quantity,
        amount_total=addon.unit_price * payload.quantity,
        currency=addon.currency,
        payment_provider=instance.payment_provider or "",
        checkout_url=instance.checkout_url or "",
        payment_reference=instance.payment_reference or "",
        status=instance.status,
    )


@tenant_router.get("")
@document_response(
    message="Tenant addons fetched",
    description=(
        "List the calling tenant's addon history (active, pending, "
        "expired, cancelled). Sorted newest-first. Super_admin only."
    ),
    summary="List my tenant's addons",
    include_meta=True,
)
async def list_my_tenant_addons_endpoint(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    items = await list_tenant_addon_history(tenant_id, skip=skip, limit=limit)
    return items, {"total": len(items), "skip": skip, "limit": limit}


@tenant_router.get("/active")
@document_response(
    message="Active addons fetched",
    description=(
        "List only currently-active (paid, not yet expired) addons. "
        "Used by the storage / quota UI to show 'you have N active "
        "addons granting X GB'."
    ),
    summary="List my tenant's active addons",
)
async def list_my_active_tenant_addons_endpoint(
    kind: Annotated[Optional[AddonKind], Query()] = None,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await list_active_addons_for_tenant(tenant_id, kind=kind)


@tenant_router.get("/{tenant_addon_id}")
@document_response(
    message="Tenant addon retrieved",
    description="Fetch one tenant addon instance. Super_admin only.",
    summary="Get my tenant addon",
)
async def get_my_tenant_addon_endpoint(
    tenant_addon_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> TenantAddonOut:
    tenant_id = principal.tenant_id or ""
    return await get_tenant_addon_detail(tenant_id, tenant_addon_id)


@tenant_router.post("/{tenant_addon_id}/cancel")
@document_response(
    message="Tenant addon cancelled",
    description=(
        "Cancel a tenant addon. Active addons stop being counted "
        "immediately; pending addons abandon the checkout. Refunds "
        "must be handled separately via ``/v1/payments/{id}/refund``."
    ),
    summary="Cancel my tenant addon",
)
async def cancel_my_tenant_addon_endpoint(
    tenant_addon_id: str,
    request: Request,
    payload: dict = Body(default_factory=dict),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> TenantAddonOut:
    tenant_id = principal.tenant_id or ""
    # Defence-in-depth: verify the row belongs to this tenant.
    await get_tenant_addon_detail(tenant_id, tenant_addon_id)
    reason = str(payload.get("reason") or "")[:500] if isinstance(payload, dict) else ""
    return await cancel_tenant_addon(
        tenant_addon_id,
        actor_id=principal.user_id,
        reason=reason,
        request_id=getattr(request.state, "request_id", None),
    )


# ─── Webhook callback (called from payment webhook handlers) ───────


@admin_router.post("/webhooks/activate/{payment_reference}")
@document_response(
    message="Addon activation processed",
    description=(
        "Internal callback used by the payment webhook plumbing once "
        "an addon checkout completes. Flips the tenant_addons row "
        "from ``pending`` to ``active`` and sets ``expires_at``. "
        "Idempotent on repeat webhook deliveries. Application admin "
        "only — production webhooks call the underlying service "
        "function directly; this endpoint exists for manual replay "
        "and ops tooling."
    ),
    summary="Activate addon by payment_reference (replay hook)",
)
async def replay_activate_addon_endpoint(
    payment_reference: str,
    _principal: AuthPrincipal = Depends(verify_admin_token),
):
    row = await activate_tenant_addon_by_reference(payment_reference=payment_reference)
    if row is None:
        raise resource_not_found(resource="TenantAddon", resource_id=payment_reference)
    return row
