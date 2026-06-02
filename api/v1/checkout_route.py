"""Tenant super_admin endpoints for the provider-agnostic checkout flow.

- POST   /v1/checkout/sessions              — create a new checkout link
- GET    /v1/checkout/sessions              — list history (filter by status)
- GET    /v1/checkout/sessions/{id}         — detail
- POST   /v1/checkout/sessions/{id}/cancel  — cancel a pending session
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status

from core.errors import auth_permission_denied
from core.queue.entity_cache import get_or_compute_entity
from core.response_envelope import document_response
from bson import ObjectId

from repositories.system_user_repo import get_system_user
from schemas.checkout_schema import (
    CheckoutCreateRequest,
    CheckoutSessionOut,
    CheckoutStatus,
    TrialCardCaptureOut,
    TrialCardCaptureRequest,
)
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.checkout_service import (
    cancel_checkout,
    create_checkout_session,
    get_tenant_checkout,
    get_tenant_checkout_by_reference,
    list_tenant_checkouts,
    resolve_full_checkout_url,
    with_full_checkout_url,
)
from services.paystack_billing_service import initiate_trial_card_capture

router = APIRouter(prefix="/checkout", tags=["Checkout"])


def _require_tenant_scope(principal: AuthPrincipal) -> str:
    tenant_id = getattr(principal, "tenant_id", None)
    if not tenant_id:
        raise auth_permission_denied(permission_key="checkout.manage")
    return tenant_id


@router.post("/sessions")
@document_response(
    message="Checkout session created",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Create a provider-agnostic checkout link for the tenant. The server "
        "picks the best available provider (stripe → flutterwave → paystack → "
        "app-mode fallback). Tenant super_admin only."
    ),
    summary="Create a checkout session",
)
async def create_checkout_endpoint(
    payload: CheckoutCreateRequest,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> CheckoutSessionOut:
    tenant_id = _require_tenant_scope(principal)
    # Look up the super_admin's email for the customer_email field sent to
    # the external provider (improves receipt emails on Stripe/Flutterwave).
    su = (
        await get_system_user({"_id": ObjectId(principal.user_id)})
        if ObjectId.is_valid(principal.user_id)
        else None
    )
    email = getattr(su, "email", None) if su else None
    session = await create_checkout_session(
        tenant_id=tenant_id,
        created_by_user_id=principal.user_id,
        plan_id=payload.plan_id,
        billing_cycle=payload.billing_cycle,
        discount_ids=payload.discount_ids or [],
        preferred_provider=payload.preferred_provider,
        trial_days=payload.trial_days,
        trial_code=payload.trial_code,
        metadata=payload.metadata,
        customer_email=email,
    )
    return with_full_checkout_url(session, request)


@router.post("/trial-card-capture")
@document_response(
    message="Trial card-capture link created",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Start a free trial that auto-charges at the end. Tokenizes the card "
        "via a small Paystack charge (refunded once captured) and returns a "
        "hosted authorization_url to open — the same redirect as checkout, so "
        "no new UI. The trial subscription is provisioned only once the "
        "tokenization charge clears. Tenant super_admin only."
    ),
    summary="Start a free trial with card capture",
)
async def trial_card_capture_endpoint(
    payload: TrialCardCaptureRequest,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> TrialCardCaptureOut:
    tenant_id = _require_tenant_scope(principal)
    su = (
        await get_system_user({"_id": ObjectId(principal.user_id)})
        if ObjectId.is_valid(principal.user_id)
        else None
    )
    email = getattr(su, "email", None) if su else None
    if not email:
        raise auth_permission_denied(permission_key="checkout.manage")
    result = await initiate_trial_card_capture(
        tenant_id=tenant_id,
        plan_id=payload.plan_id,
        billing_cycle=payload.billing_cycle,
        trial_days=payload.trial_days,
        email=email,
        redirect_url=payload.redirect_url,
    )
    result["authorization_url"] = resolve_full_checkout_url(
        result.get("authorization_url"), request
    )
    return TrialCardCaptureOut(**result)


@router.get("/sessions")
@document_response(
    message="Checkout sessions fetched",
    description=(
        "List this tenant's checkout sessions, newest first. Filter by "
        "status=pending|succeeded|failed|expired|cancelled. "
        "Tenant super_admin only."
    ),
    summary="List checkout sessions",
    include_meta=True,
)
async def list_checkout_sessions_endpoint(
    request: Request,
    status_filter: Optional[CheckoutStatus] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = _require_tenant_scope(principal)
    items, total = await list_tenant_checkouts(
        tenant_id=tenant_id,
        status=status_filter,
        skip=skip,
        limit=limit,
    )
    return {
        "items": [with_full_checkout_url(item, request) for item in items],
        "meta": {"total": total, "skip": skip, "limit": limit},
    }


@router.get("/sessions/by-reference/{reference}")
@document_response(
    message="Checkout session fetched",
    description=(
        "Resolve a checkout session by its provider reference — the value "
        "providers append to the post-payment callback URL "
        "(?reference=...&trxref=...). Used by the frontend payment-return page. "
        "Tenant super_admin only."
    ),
    summary="Get checkout session by provider reference",
)
async def get_checkout_by_reference_endpoint(
    reference: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = _require_tenant_scope(principal)
    session = await get_tenant_checkout_by_reference(
        tenant_id=tenant_id, reference=reference
    )
    return with_full_checkout_url(session, request)


@router.get("/sessions/{checkout_id}")
@document_response(
    message="Checkout session fetched",
    description="Retrieve a single checkout session. Tenant super_admin only.",
    summary="Get checkout session",
)
async def get_checkout_endpoint(
    checkout_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = _require_tenant_scope(principal)
    session = await get_or_compute_entity(
        entity_type="checkout_session",
        entity_id=checkout_id,
        loader=lambda: get_tenant_checkout(
            tenant_id=tenant_id, checkout_id=checkout_id
        ),
    )
    return with_full_checkout_url(session, request)


@router.post("/sessions/{checkout_id}/cancel")
@document_response(
    message="Checkout session cancelled",
    description=(
        "Cancel a pending checkout session. Does not refund — use "
        "/v1/payments/{id}/refund for completed payments. "
        "Tenant super_admin only."
    ),
    summary="Cancel checkout session",
)
async def cancel_checkout_endpoint(
    checkout_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> CheckoutSessionOut:
    tenant_id = _require_tenant_scope(principal)
    session = await cancel_checkout(
        tenant_id=tenant_id,
        checkout_id=checkout_id,
        cancelled_by_user_id=principal.user_id,
    )
    return with_full_checkout_url(session, request)
