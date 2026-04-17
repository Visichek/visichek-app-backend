from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.plan_schema import (
    PlanCreate,
    PlanUpdate,
    PlanOut,
    PlanStatus,
    PlanTier,
)
from services.plan_service import (
    add_plan,
    retrieve_plan_by_id,
    retrieve_plans,
    update_plan_by_id,
    archive_plan,
    activate_plan,
    clone_plan,
    remove_plan,
)
from security.account_status_check import check_admin_account_status_and_permissions

router = APIRouter(prefix="/plans", tags=["Plans"])


@router.post("")
@document_response(
    message="Plan created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Create a new subscription plan (application admin only)",
    summary="Create plan",
)
async def create_plan_endpoint(
    payload: PlanCreate,
    admin=Depends(check_admin_account_status_and_permissions),
) -> PlanOut:
    """Create a new subscription plan. Only application admins can manage plans."""
    return await add_plan(payload)


@router.get("")
@document_response(
    message="Plans retrieved successfully",
    description="List all subscription plans with optional filters",
    summary="List plans",
    include_meta=True,
)
async def list_plans_endpoint(
    status_filter: Optional[PlanStatus] = Query(None, alias="status"),
    tier: Optional[PlanTier] = Query(None),
    public_only: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[PlanOut]:
    """List all plans. Public endpoint for plan catalog; admin sees all."""
    return await retrieve_plans(
        status_filter=status_filter,
        tier_filter=tier,
        public_only=public_only,
        start=skip,
        stop=skip + limit,
    )


@router.get("/{plan_id}")
@document_response(
    message="Plan retrieved successfully",
    description="Get a specific plan by ID",
    summary="Get plan",
)
async def get_plan_endpoint(plan_id: str) -> PlanOut | None:
    """Retrieve a specific plan by ID."""
    return await retrieve_plan_by_id(plan_id)


@router.put("/{plan_id}")
@document_response(
    message="Plan updated successfully",
    description="Update a subscription plan (application admin only)",
    summary="Update plan",
)
async def update_plan_endpoint(
    plan_id: str,
    payload: PlanUpdate,
    admin=Depends(check_admin_account_status_and_permissions),
) -> PlanOut | None:
    """Update a plan's configuration. Changes take effect immediately for all subscribers."""
    return await update_plan_by_id(plan_id, payload)


@router.post("/{plan_id}/activate")
@document_response(
    message="Plan activated successfully",
    description="Publish a draft plan (application admin only)",
    summary="Activate plan",
)
async def activate_plan_endpoint(
    plan_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> PlanOut:
    """Activate a draft plan, making it available for subscription."""
    return await activate_plan(plan_id)


@router.post("/{plan_id}/archive")
@document_response(
    message="Plan archived successfully",
    description="Archive a plan (soft delete). Existing subscriptions continue. (Application admin only)",
    summary="Archive plan",
)
async def archive_plan_endpoint(
    plan_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> PlanOut | None:
    """Archive a plan. Existing subscriptions remain active but no new subscriptions allowed."""
    return await archive_plan(plan_id)


@router.post("/{source_plan_id}/clone")
@document_response(
    message="Plan cloned successfully",
    status_code=status.HTTP_201_CREATED,
    description="Clone an existing plan with a new name (application admin only)",
    summary="Clone plan",
)
async def clone_plan_endpoint(
    source_plan_id: str,
    new_name: str = Query(...),
    new_display_name: str = Query(...),
    admin=Depends(check_admin_account_status_and_permissions),
) -> PlanOut:
    """Clone a plan to create a variant."""
    return await clone_plan(source_plan_id, new_name, new_display_name)


@router.delete("/{plan_id}")
@document_response(
    message="Plan deleted successfully",
    description="Permanently delete a draft plan with no subscriptions (application admin only)",
    summary="Delete plan",
)
async def delete_plan_endpoint(
    plan_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> dict:
    """Permanently delete a draft plan. Only works for plans with no subscriptions."""
    await remove_plan(plan_id)
    return {"deleted": True}
