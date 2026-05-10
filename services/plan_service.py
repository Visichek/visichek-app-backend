from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from fastapi import HTTPException, status

from core.errors import resource_not_found
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


async def add_plan(
    plan_data: PlanCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> PlanOut:
    """Create a new subscription plan. Only application admins can do this.

    Singleton enforcement:
        * Free / Starter / Premium are SINGLETON tiers — at most one row
          per tier may exist. Trying to create a second one returns 409.
        * Enterprise is bespoke — many enterprise plans may coexist, each
          with a unique slug ``name``. Use a stable slug like
          ``"enterprise-acme"`` so it can be paired with a FastAPI sub-app
          mounted at ``/v1/enterprise/<slug>/*`` (see
          ``core.enterprise_apps``).
    """
    from config.plan_tiers import (
        SINGLETON_PLAN_NAMES,
        is_singleton_plan_name,
    )

    # Singleton tier guard — only one Free / Starter / Premium row.
    plan_tier_value = (
        plan_data.tier.value
        if hasattr(plan_data.tier, "value")
        else str(plan_data.tier)
    )
    if plan_tier_value in {"free", "starter", "premium"}:
        existing_tier = await get_plan({"tier": plan_tier_value})
        if existing_tier and existing_tier.name != plan_data.name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"There can only be ONE {plan_tier_value} plan. "
                    f"Existing plan: '{existing_tier.name}'. Edit it instead "
                    f"of creating a new one."
                ),
            )

    # Singleton slug guard — only the canonical name may take a singleton slot.
    if plan_data.name in SINGLETON_PLAN_NAMES and (
        plan_tier_value not in {"free", "starter", "premium"}
        or plan_data.name != plan_tier_value
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"The slug '{plan_data.name}' is reserved for the {plan_data.name} "
                f"singleton plan. Pick a different slug for non-singleton plans."
            ),
        )

    # Enterprise slug rules — must NOT collide with the legacy template
    # slug or any singleton slug. ``"enterprise"`` itself is reserved as
    # the template name.
    if plan_tier_value == "enterprise" and is_singleton_plan_name(plan_data.name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Enterprise plans must use a customer-specific slug, not a "
                "singleton plan name. Try 'enterprise-<customer>' instead."
            ),
        )

    # Check for duplicate plan name
    existing = await get_plan({"name": plan_data.name})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plan with name '{plan_data.name}' already exists",
        )
    plan = await create_plan(plan_data, preassigned_id=preassigned_id)

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
                "tier": plan_data.tier.value
                if hasattr(plan_data.tier, "value")
                else plan_data.tier,
                "status": plan.status.value
                if hasattr(plan.status, "value")
                else plan.status,
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

    # Per-tier editability lockdown for canonical plans. Free / Starter /
    # Premium are intentionally narrow — admins may tune cap numerics
    # (e.g. ``max_visitors_per_month``) but not flip features that the
    # tier is supposed to deny. Enterprise is bespoke so anything goes.
    existing = await get_plan({"_id": ObjectId(plan_id)})
    if existing and existing.name:
        from config.plan_tiers import get_canonical_plan

        canonical = get_canonical_plan(existing.name)
        if canonical is not None:
            disallowed: list[str] = []
            sent = plan_data.model_dump(exclude_unset=True, exclude_none=True)

            # tenant_caps is a nested dict — only the fields listed in
            # ``adjustable_cap_fields`` may be sent through.
            tenant_caps = sent.pop("tenant_caps", None)
            if tenant_caps:
                bad_caps = [
                    k
                    for k in tenant_caps.keys()
                    if k not in canonical.adjustable_cap_fields
                ]
                if bad_caps:
                    disallowed.extend(f"tenant_caps.{k}" for k in bad_caps)

            # Top-level fields outside the per-tier allowlist are blocked.
            # ``last_updated`` is allowed implicitly because the schema
            # always sets it.
            allow_top_level = (
                {"last_updated", "tenant_caps"}
                | canonical.adjustable_plan_fields
            )
            for key in sent.keys():
                if key not in allow_top_level:
                    disallowed.append(key)

            if disallowed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "These fields are tier-locked on the "
                        f"{existing.display_name or existing.name} plan and cannot "
                        "be edited: " + ", ".join(sorted(set(disallowed)))
                    ),
                )

    return await update_plan({"_id": ObjectId(plan_id)}, plan_data)


async def archive_plan(plan_id: str) -> Optional[PlanOut]:
    """Soft-delete: set status to archived and hide from public.

    Canonical plans (Free / Starter / Premium / Enterprise) cannot be
    archived — archiving Free in particular would break every tenant.
    Existing subscriptions still work until they expire — only new
    subscriptions to this plan are blocked (handled by subscribe_tenant).
    """
    from config.plan_tiers import is_singleton_plan_name

    existing = await retrieve_plan_by_id(plan_id)
    if existing and existing.name and is_singleton_plan_name(existing.name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"The {existing.display_name or existing.name} plan is a "
                "singleton and cannot be archived. Tenants depend on Free as "
                "the default fallback; Starter and Premium are platform "
                "catalogue offerings."
            ),
        )

    # Bypass the tier-editability check below by writing the status flip
    # directly through the repo — canonical plans were already rejected
    # above, so this only fires for legacy plans.
    archived = await update_plan(
        {"_id": ObjectId(plan_id)},
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
                    "tier": archived.tier.value
                    if hasattr(archived.tier, "value")
                    else archived.tier,
                },
            )
        except Exception:
            pass

    return archived


async def activate_plan(plan_id: str) -> PlanOut:
    """Publish a draft plan.

    After the update we re-read the plan from the DB to confirm the new
    ``status`` was actually persisted. A silent persistence failure (the
    update returned a doc but ``status`` is still ``"draft"``) raises so
    the queued-write dispatcher records the job as ``failed`` and the
    actor admin gets an in-app failure notification via
    ``notify_job_failure`` instead of being told it succeeded.
    """
    plan = await retrieve_plan_by_id(plan_id)
    if not plan:
        raise resource_not_found(resource="Plan", resource_id=plan_id)
    if plan.status == PlanStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot activate an archived plan. Create a new plan instead.",
        )
    activated = await update_plan_by_id(
        plan_id,
        PlanUpdate(status=PlanStatus.ACTIVE),
    )
    if not activated:
        raise resource_not_found(resource="Plan", resource_id=plan_id)
    if activated.status != PlanStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Plan activation did not persist (status is still "
                f"{activated.status.value if hasattr(activated.status, 'value') else activated.status})."
            ),
        )
    verified = await retrieve_plan_by_id(plan_id)
    if not verified or verified.status != PlanStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Plan activation did not persist on re-read; database may be lagging or another writer reverted it.",
        )

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="plan.activated",
            resource_type="plan",
            resource_id=plan_id,
            details={
                "name": verified.name,
                "tier": verified.tier.value
                if hasattr(verified.tier, "value")
                else verified.tier,
            },
        )
    except Exception:
        pass

    return verified


async def clone_plan(
    source_plan_id: str, new_name: str, new_display_name: str
) -> PlanOut:
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
                "tier": plan.tier.value if hasattr(plan.tier, "value") else plan.tier,
            },
        )
    except Exception:
        pass

    return True
