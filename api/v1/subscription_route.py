from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from core.response_envelope import document_response
from schemas.subscription_schema import (
    SubscriptionOut,
    SubscriptionWithDetailsOut,
    SubscriptionStatus,
    BillingCycle,
)
from services.subscription_service import (
    subscribe_tenant,
    retrieve_subscription_by_id,
    retrieve_tenant_active_subscription,
    retrieve_subscriptions_with_details,
    change_plan,
    cancel_subscription,
    update_subscription_overrides,
)
from security.account_status_check import check_admin_account_status_and_permissions

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
    message="Subscription created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Subscribe a tenant to a plan (application admin only)",
    summary="Create subscription",
)
async def create_subscription_endpoint(
    payload: SubscribeTenantRequest,
    admin=Depends(check_admin_account_status_and_permissions),
) -> SubscriptionOut:
    """Subscribe a tenant to a plan. Calculates pricing with any discount codes."""
    return await subscribe_tenant(
        tenant_id=payload.tenant_id,
        plan_id=payload.plan_id,
        billing_cycle=payload.billing_cycle,
        discount_ids=payload.discount_ids,
        trial_days=payload.trial_days,
        admin_notes=payload.admin_notes,
        feature_overrides=payload.feature_overrides,
        crud_limit_overrides=payload.crud_limit_overrides,
        retrieval_quota_overrides=payload.retrieval_quota_overrides,
        tenant_cap_overrides=payload.tenant_cap_overrides,
    )


@router.get("")
@document_response(
    message="Subscriptions retrieved successfully",
    description="List subscriptions with optional filters (application admin only). Includes full tenant and plan details.",
    summary="List subscriptions",
    include_meta=True,
)
async def list_subscriptions_endpoint(
    tenant_id: Optional[str] = Query(None, alias="tenantId"),
    status_filter: Optional[SubscriptionStatus] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin=Depends(check_admin_account_status_and_permissions),
) -> list[SubscriptionWithDetailsOut]:
    """List subscriptions enriched with tenant and plan info. Filter by tenantId or status."""
    return await retrieve_subscriptions_with_details(
        tenant_id=tenant_id,
        status_filter=status_filter,
        start=skip,
        stop=skip + limit,
    )


@router.get("/tenant/{tenant_id}/active")
@document_response(
    message="Active subscription retrieved",
    description="Get a tenant's current active subscription",
    summary="Get tenant active subscription",
)
async def get_tenant_active_subscription_endpoint(
    tenant_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> SubscriptionOut | None:
    """Get the active subscription for a specific tenant."""
    return await retrieve_tenant_active_subscription(tenant_id)


@router.get("/{subscription_id}")
@document_response(
    message="Subscription retrieved successfully",
    description="Get a specific subscription by ID",
    summary="Get subscription",
)
async def get_subscription_endpoint(
    subscription_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> SubscriptionOut | None:
    """Retrieve a specific subscription by ID."""
    return await retrieve_subscription_by_id(subscription_id)


@router.post("/change-plan")
@document_response(
    message="Plan changed successfully",
    description="Switch a tenant to a different plan (takes effect immediately)",
    summary="Change plan",
)
async def change_plan_endpoint(
    payload: ChangePlanRequest,
    admin=Depends(check_admin_account_status_and_permissions),
) -> SubscriptionOut | None:
    """Immediately switch a tenant to a different plan."""
    return await change_plan(
        tenant_id=payload.tenant_id,
        new_plan_id=payload.new_plan_id,
        billing_cycle=payload.billing_cycle,
    )


@router.post("/cancel")
@document_response(
    message="Subscription cancelled successfully",
    description="Cancel a tenant's subscription",
    summary="Cancel subscription",
)
async def cancel_subscription_endpoint(
    payload: CancelSubscriptionRequest,
    admin=Depends(check_admin_account_status_and_permissions),
) -> SubscriptionOut | None:
    """Cancel a tenant's subscription. Can be immediate or at period end."""
    return await cancel_subscription(
        tenant_id=payload.tenant_id,
        reason=payload.reason,
        immediate=payload.immediate,
    )


@router.put("/{subscription_id}/overrides")
@document_response(
    message="Subscription overrides updated",
    description="Update tenant-specific overrides on a subscription (application admin only)",
    summary="Update subscription overrides",
)
async def update_overrides_endpoint(
    subscription_id: str,
    payload: UpdateOverridesRequest,
    admin=Depends(check_admin_account_status_and_permissions),
) -> SubscriptionOut | None:
    """Update custom overrides for a tenant's subscription (feature flags, limits, etc.)."""
    return await update_subscription_overrides(
        sub_id=subscription_id,
        feature_overrides=payload.feature_overrides,
        crud_limit_overrides=payload.crud_limit_overrides,
        retrieval_quota_overrides=payload.retrieval_quota_overrides,
        tenant_cap_overrides=payload.tenant_cap_overrides,
    )
