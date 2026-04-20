from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from core.errors import auth_permission_denied, auth_role_mismatch
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.subscription_schema import (
    BillingCycle,
    SubscriptionOut,
    SubscriptionStatus,
)
from services.subscription_service import (
    retrieve_subscription_by_id,
    retrieve_subscriptions_with_details,
    retrieve_tenant_active_subscription,
)
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_any_token
from security.principal import AuthPrincipal

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


# --- Request bodies ---


class SubscribeTenantRequest(BaseModel):
    tenant_id: str
    plan_id: str
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    discount_ids: list[str] = Field(default_factory=list)
    trial_days: int = 0
    admin_notes: Optional[str] = None
    feature_overrides: Optional[dict] = None
    crud_limit_overrides: Optional[dict] = None
    retrieval_quota_overrides: Optional[dict] = None
    tenant_cap_overrides: Optional[dict] = None


class ChangePlanRequest(BaseModel):
    tenant_id: str
    new_plan_id: str
    billing_cycle: Optional[BillingCycle] = None


class CancelSubscriptionRequest(BaseModel):
    tenant_id: str
    reason: Optional[str] = None
    immediate: bool = False


class UpdateOverridesRequest(BaseModel):
    feature_overrides: Optional[dict] = None
    crud_limit_overrides: Optional[dict] = None
    retrieval_quota_overrides: Optional[dict] = None
    tenant_cap_overrides: Optional[dict] = None


# --- Endpoints ---


@router.post("")
@document_response(
    message="Subscription creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Enqueue a subscription create. The real subscription id is returned via "
        "``GET /v1/jobs/{job_id}`` once the worker commits — the 202 body's ``id`` "
        "is speculative for subscription writes."
    ),
    summary="Create subscription (async)",
)
async def create_subscription_endpoint(
    payload: SubscribeTenantRequest,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="subscription.create",
        payload=payload.model_dump(exclude_none=True),
        resource_type="subscription",
        tenant_id=payload.tenant_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Subscriptions retrieved successfully",
    description="Unfiltered first page served from the global precompute cache.",
    summary="List subscriptions",
    include_meta=True,
)
async def list_subscriptions_endpoint(
    tenant_id: Optional[str] = Query(None, alias="tenantId"),
    status_filter: Optional[SubscriptionStatus] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    unfiltered = not tenant_id and not status_filter
    if unfiltered and skip == 0 and limit in (50, 100):
        cached: List[Any] = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="subscriptions.list",
            ttl=60,
            loader=_load_subscriptions,
        )
        return cached[:limit]
    return await retrieve_subscriptions_with_details(
        tenant_id=tenant_id,
        status_filter=status_filter,
        start=skip,
        stop=skip + limit,
    )


async def _load_subscriptions() -> List[Any]:
    subs = await retrieve_subscriptions_with_details(start=0, stop=100)
    return [
        s.model_dump(mode="json", by_alias=True) if hasattr(s, "model_dump") else s
        for s in subs
    ]


@router.get("/tenant/{tenant_id}/active")
@document_response(
    message="Active subscription retrieved",
    description="Served from the per-tenant precompute cache.",
    summary="Get tenant active subscription",
)
async def get_tenant_active_subscription_endpoint(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_any_token),
) -> Any:
    if principal.role == "admin":
        pass
    elif principal.role == "super_admin":
        if principal.tenant_id != tenant_id:
            raise auth_permission_denied(permission_key="subscription.read")
    else:
        raise auth_role_mismatch(required_role="admin", actual_role=principal.role)

    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="subscription.active",
        ttl=60,
        loader=lambda: _load_active_subscription(tenant_id),
    )


async def _load_active_subscription(tenant_id: str) -> Any:
    sub = await retrieve_tenant_active_subscription(tenant_id)
    if not sub:
        return None
    return sub.model_dump(mode="json", by_alias=True) if hasattr(sub, "model_dump") else sub


@router.get("/{subscription_id}")
@document_response(
    message="Subscription retrieved successfully",
    summary="Get subscription",
)
async def get_subscription_endpoint(
    subscription_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> SubscriptionOut | None:
    return await retrieve_subscription_by_id(subscription_id)


@router.post("/change-plan")
@document_response(
    message="Plan change queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Change plan (async)",
)
async def change_plan_endpoint(
    payload: ChangePlanRequest,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="subscription.change_plan",
        payload=payload.model_dump(exclude_none=True),
        resource_type="subscription",
        tenant_id=payload.tenant_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/cancel")
@document_response(
    message="Cancel subscription queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel subscription (async)",
)
async def cancel_subscription_endpoint(
    payload: CancelSubscriptionRequest,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="subscription.cancel",
        payload=payload.model_dump(exclude_none=True),
        resource_type="subscription",
        tenant_id=payload.tenant_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.put("/{subscription_id}/overrides")
@document_response(
    message="Subscription override update queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update subscription overrides (async)",
)
async def update_overrides_endpoint(
    subscription_id: str,
    payload: UpdateOverridesRequest,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="subscription.update_overrides",
        payload=payload.model_dump(exclude_none=True),
        resource_type="subscription",
        resource_id=subscription_id,
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )
