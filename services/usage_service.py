from __future__ import annotations

import time
from datetime import datetime
from typing import Optional


from repositories.usage_repo import (
    create_usage_record,
    get_current_count,
    increment_usage_aggregate,
    reset_usage_aggregates,
    delete_usage_records,
)
from schemas.usage_schema import (
    UsageRecordCreate,
    UsageRecordOut,
    OperationType,
    TenantUsageSummary,
)
from schemas.plan_schema import QuotaResetInterval


def get_period_key(reset_interval: QuotaResetInterval) -> str:
    """Generate the current period key based on interval type."""
    now = datetime.utcnow()
    if reset_interval == QuotaResetInterval.DAILY:
        return now.strftime("%Y-%m-%d")
    elif reset_interval == QuotaResetInterval.WEEKLY:
        return f"{now.year}-W{now.isocalendar()[1]:02d}"
    elif reset_interval == QuotaResetInterval.MONTHLY:
        return now.strftime("%Y-%m")
    else:  # NEVER
        return "lifetime"


async def record_usage(
    tenant_id: str,
    subscription_id: str,
    collection: str,
    operation: OperationType,
    endpoint: Optional[str] = None,
    user_id: Optional[str] = None,
    user_role: Optional[str] = None,
    period_key: Optional[str] = None,
) -> UsageRecordOut:
    """Record a usage event and increment the aggregate counter.
    This is called by the enforcement middleware after allowing a request through.
    """
    # Write raw event log
    record = await create_usage_record(
        UsageRecordCreate(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            collection=collection,
            operation=operation,
            endpoint=endpoint,
            user_id=user_id,
            user_role=user_role,
        )
    )

    # Increment aggregate counter (the key lookup structure for quota checks)
    if period_key:
        await increment_usage_aggregate(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            collection=collection,
            operation=operation.value,
            period_key=period_key,
        )

    return record


async def check_quota(
    tenant_id: str,
    subscription_id: str,
    collection: str,
    operation: str,
    limit: Optional[int],
    reset_interval: QuotaResetInterval,
) -> tuple[bool, int, Optional[int]]:
    """Check if a usage quota allows this operation.
    Returns (allowed, current_count, limit).
    """
    if limit is None:
        return True, 0, None  # unlimited

    period_key = get_period_key(reset_interval)
    current = await get_current_count(
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        collection=collection,
        operation=operation,
        period_key=period_key,
    )
    return current < limit, current, limit


async def get_tenant_usage_summary(
    tenant_id: str,
    subscription_id: str,
    plan_data: dict,
) -> TenantUsageSummary:
    """Build a comprehensive usage summary for a tenant.

    The historical implementation only populated ``crud_usage`` /
    ``retrieval_usage`` from the plan's ``crud_limits`` and
    ``retrieval_quotas`` lists. Canonical plans (Free / Starter /
    Premium) carry their numeric limits on ``tenant_caps`` instead, so
    those tenants got back an empty ``crud_usage`` and the frontend had
    nothing to render. This rewrite populates ``entity_counts`` and
    ``storage`` from real DB counts so the FE can render the same
    progress bars regardless of which surface a limit lives on.
    """
    from core.database import db
    from schemas.plan_schema import QuotaResetInterval
    from services.plan_limits import get_month_bounds

    period_monthly = get_period_key(QuotaResetInterval.MONTHLY)

    # ── Legacy crud_limits / retrieval_quotas usage (still populated
    #    when a plan ships them — Enterprise overrides may add them).
    crud_usage: dict = {}
    for cl in plan_data.get("crud_limits", []):
        collection = cl["collection"]
        period = get_period_key(QuotaResetInterval(cl.get("reset_interval", "monthly")))
        crud_usage[collection] = {}
        for op in ["create", "update", "delete"]:
            max_key = f"max_{op}"
            limit = cl.get(max_key)
            current = await get_current_count(
                tenant_id,
                subscription_id,
                collection,
                op,
                period,
            )
            crud_usage[collection][op] = {"used": current, "limit": limit}

    retrieval_usage = {}
    for rq in plan_data.get("retrieval_quotas", []):
        collection = rq["collection"]
        period = get_period_key(QuotaResetInterval(rq.get("reset_interval", "daily")))
        current = await get_current_count(
            tenant_id,
            subscription_id,
            collection,
            "read",
            period,
        )
        retrieval_usage[collection] = {
            "read": {"used": current, "limit": rq.get("max_reads")}
        }

    # ── Live DB counts that EVERY tenant needs (independent of whether
    #    the plan also carries crud_limits). The frontend renders these
    #    as the primary "X of Y used" progress bars on the dashboard.
    month_start, month_end = get_month_bounds()

    branches_count = await db["branches"].count_documents(
        {"tenant_id": tenant_id, "is_active": {"$ne": False}}
    )
    departments_count = await db["departments"].count_documents(
        {"tenant_id": tenant_id, "is_active": {"$ne": False}}
    )
    system_users_count = await db["system_users"].count_documents(
        {"tenant_id": tenant_id, "account_status": "ACTIVE"}
    )

    # Visitors-this-month: count visit_sessions created in the current
    # calendar month. This matches the cap definition
    # (``max_visitors_per_month``) which is enforced at session creation
    # time. We fall back to the ``visitors`` collection if a tenant has
    # legacy data without sessions yet.
    visitors_this_month = await db["visit_sessions"].count_documents(
        {
            "tenant_id": tenant_id,
            "date_created": {"$gte": month_start, "$lt": month_end},
        }
    )
    if visitors_this_month == 0:
        visitors_this_month = await db["visitors"].count_documents(
            {
                "tenant_id": tenant_id,
                "date_created": {"$gte": month_start, "$lt": month_end},
            }
        )

    appointments_this_month = await db["expected_appointments"].count_documents(
        {
            "tenant_id": tenant_id,
            "date_created": {"$gte": month_start, "$lt": month_end},
        }
    )

    entity_counts: dict = {
        "branches": branches_count,
        "departments": departments_count,
        "system_users": system_users_count,
        "visitors_this_month": visitors_this_month,
        "appointments_this_month": appointments_this_month,
        # Keep month boundary on the response so the FE can render
        # "resets in N days" without recomputing client-side.
        "period_start": month_start,
        "period_end": month_end,
    }

    # ── Storage (best-effort). We only count documents we know about
    #    in MongoDB; bytes-on-disk comes from the storage provider and
    #    is not always available in test envs.
    documents_count = await db["documents"].count_documents({"tenant_id": tenant_id})
    storage_caps = plan_data.get("storage_limits", {}) or {}
    storage: dict = {
        "documents_used": documents_count,
        "documents_limit": storage_caps.get("max_documents"),
        "storage_mb_used": None,  # populated by the storage manager when wired
        "storage_mb_limit": storage_caps.get("max_storage_mb"),
        "max_file_size_mb": storage_caps.get("max_file_size_mb"),
    }

    return TenantUsageSummary(
        tenant_id=tenant_id,
        plan_name=plan_data.get("plan_name", "unknown"),
        plan_tier=plan_data.get("tier", "unknown"),
        subscription_status=plan_data.get("subscription_status", "unknown"),
        period=period_monthly,
        crud_usage=crud_usage,
        retrieval_usage=retrieval_usage,
        entity_counts=entity_counts,
        entity_caps=plan_data.get("tenant_caps", {}) or {},
        storage=storage,
    )


async def cleanup_old_usage_records(days_to_keep: int = 90) -> int:
    """Remove usage records older than N days. Run as background task."""
    cutoff = int(time.time()) - (days_to_keep * 86400)
    result = await delete_usage_records({"timestamp": {"$lt": cutoff}})
    return result.deleted_count if result else 0


async def reset_period_aggregates(period_key: str) -> int:
    """Reset all aggregates for a given period. Called by scheduler at period boundaries."""
    result = await reset_usage_aggregates({"period_key": period_key})
    return result.modified_count if result else 0
