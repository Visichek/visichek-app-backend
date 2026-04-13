from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException, status

from repositories.plan_repo import (
    create_plan,
    get_plan,
    get_plans,
    update_plan,
    delete_plan,
)
from schemas.plan_schema import (
    PlanCreate,
    PlanUpdate,
    PlanOut,
    PlanStatus,
    PlanTier,
)
from services.audit_service import record_audit_event


async def add_plan(plan_data: PlanCreate) -> PlanOut:
    """Create a new subscription plan. Only application admins can do this."""
    # Check for duplicate plan name
    existing = await get_plan({"name": plan_data.name})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plan with name '{plan_data.name}' already exists",
        )
    plan = await create_plan(plan_data)

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="plan.created",
            resource_type="plan",
            resource_id=str(plan.id),
            details={
                "name": plan_data.name,
                "tier": plan_data.tier.value if hasattr(plan_data.tier, 'value') else plan_data.tier,
                "status": plan.status.value if hasattr(plan.status, 'value') else plan.status,
                "base_price_monthly": plan_data.base_price_monthly,
                "base_price_yearly": plan_data.base_price_yearly,
            },
        )
    except Exception:
        pass

    return plan


async def retrieve_plan_by_id(plan_id: str) -> Optional[PlanOut]:
    if not ObjectId.is_valid(plan_id):
        return None
    return await get_plan({"_id": ObjectId(plan_id)})


async def retrieve_plan_by_name(name: str) -> Optional[PlanOut]:
    return await get_plan({"name": name})


async def retrieve_plans(
    status_filter: Optional[PlanStatus] = None,
    tier_filter: Optional[PlanTier] = None,
    public_only: bool = False,
    start: int = 0,
    stop: int = 100,
) -> List[PlanOut]:
    """List plans with optional filters."""
    filter_dict: dict = {}
    if status_filter:
        filter_dict["status"] = status_filter.value
    if tier_filter:
        filter_dict["tier"] = tier_filter.value
    if public_only:
        filter_dict["is_public"] = True
        # Archived plans should never appear in public listings
        filter_dict["status"] = {"$ne": PlanStatus.ARCHIVED.value}
    return await get_plans(filter_dict, start=start, stop=stop)


async def update_plan_by_id(plan_id: str, plan_data: PlanUpdate) -> Optional[PlanOut]:
    if not ObjectId.is_valid(plan_id):
        return None
    return await update_plan({"_id": ObjectId(plan_id)}, plan_data)


async def archive_plan(plan_id: str) -> Optional[PlanOut]:
    """Soft-delete: set status to archived and hide from public.

    Existing subscriptions still work until they expire — only new
    subscriptions to this plan are blocked (handled by subscribe_tenant).
    """
    archived = await update_plan_by_id(
        plan_id,
        PlanUpdate(status=PlanStatus.ARCHIVED, is_public=False),
    )

    # Record audit event (fire-and-forget)
    if archived:
        try:
            await record_audit_event(
                actor_id="system",
                actor_role="admin",
                action="plan.archived",
                resource_type="plan",
                resource_id=plan_id,
                details={
                    "name": archived.name,
                    "tier": archived.tier.value if hasattr(archived.tier, 'value') else archived.tier,
                },
            )
        except Exception:
            pass

    return archived


async def activate_plan(plan_id: str) -> Optional[PlanOut]:
    """Publish a draft plan."""
    plan = await retrieve_plan_by_id(plan_id)
    if not plan:
        return None
    if plan.status == PlanStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot activate an archived plan. Create a new plan instead.",
        )
    activated = await update_plan_by_id(
        plan_id,
        PlanUpdate(status=PlanStatus.ACTIVE),
    )

    # Record audit event (fire-and-forget)
    if activated:
        try:
            await record_audit_event(
                actor_id="system",
                actor_role="admin",
                action="plan.activated",
                resource_type="plan",
                resource_id=plan_id,
                details={
                    "name": activated.name,
                    "tier": activated.tier.value if hasattr(activated.tier, 'value') else activated.tier,
                },
            )
        except Exception:
            pass

    return activated


async def clone_plan(source_plan_id: str, new_name: str, new_display_name: str) -> PlanOut:
    """Clone an existing plan with a new name (for creating variants)."""
    source = await retrieve_plan_by_id(source_plan_id)
    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source plan not found",
        )

    # Check new name doesn't conflict
    existing = await get_plan({"name": new_name})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plan with name '{new_name}' already exists",
        )

    clone_data = PlanCreate(
        name=new_name,
        display_name=new_display_name,
        tier=source.tier,
        description=f"Cloned from {source.display_name}",
        status=PlanStatus.DRAFT,
        base_price_monthly=source.base_price_monthly,
        base_price_yearly=source.base_price_yearly,
        currency=source.currency,
        feature_rules=source.feature_rules,
        crud_limits=source.crud_limits,
        retrieval_quotas=source.retrieval_quotas,
        storage_limits=source.storage_limits,
        tenant_caps=source.tenant_caps,
        priority_support=source.priority_support,
        sla_response_hours=source.sla_response_hours,
        custom_branding=source.custom_branding,
        api_access=source.api_access,
        is_public=False,  # Draft clone starts private
        sort_order=source.sort_order,
    )
    return await create_plan(clone_data)


async def remove_plan(plan_id: str) -> bool:
    """Hard delete a plan. Only allowed for DRAFT plans with no subscriptions."""
    from repositories.subscription_repo import get_subscription

    plan = await retrieve_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found",
        )
    if plan.status != PlanStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft plans can be permanently deleted. Archive active plans instead.",
        )
    # Check no subscriptions reference this plan
    sub = await get_subscription({"plan_id": plan_id})
    if sub:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete plan with existing subscriptions",
        )
    await delete_plan({"_id": ObjectId(plan_id)})

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="plan.deleted",
            resource_type="plan",
            resource_id=plan_id,
            details={
                "name": plan.name,
                "tier": plan.tier.value if hasattr(plan.tier, 'value') else plan.tier,
            },
        )
    except Exception:
        pass

    return True
