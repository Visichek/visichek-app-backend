from __future__ import annotations

"""
Platform-wide dashboard service for application admins.

Aggregates everything across all tenants into a single comprehensive
``AdminDashboardStats`` payload — counts, growth deltas, pie-chart
distributions, top-N rollups, daily/hourly time series, and recent
activity. Wired up to ``GET /v1/admins/dashboard/stats``.

The shape mirrors ``schemas/dashboard_stats_schema.TenantDashboardStats``
(reuses the same chart primitives) so the frontend can render either
dashboard with the same chart components.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId

from core.database import db
from schemas.admin_dashboard_schema import (
    AdminDashboardStats,
    InvoiceStatusBreakdown,
    PlanDistribution,
    SubscriptionStatusBreakdown,
    SystemUserRoleBreakdown,
    TenantBriefRow,
    TopTenantByActivity,
    TopTenantByIncidents,
    TopTenantByRevenue,
    TopTenantBySupport,
    TopTenantByVisitors,
)
from schemas.dashboard_stats_schema import (
    DayOfWeekBucket,
    DistributionSlice,
    GrowthMetric,
    HourlyBucket,
    TimeSeriesPoint,
)

# ─── Time helpers ─────────────────────────────────────────────────────


def _start_of_today_ts(now: Optional[int] = None) -> int:
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _start_of_day(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _start_of_week_ts(now: Optional[int] = None) -> int:
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    monday = dt - timedelta(days=dt.weekday())
    return int(
        monday.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )


def _start_of_month_ts(now: Optional[int] = None) -> int:
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    return int(
        dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    )


def _shift_month(ts: int, months: int) -> int:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    return int(dt.replace(year=year, month=month).timestamp())


def _start_of_quarter_ts(now: Optional[int] = None) -> int:
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    q_first_month = ((dt.month - 1) // 3) * 3 + 1
    return int(
        dt.replace(
            month=q_first_month, day=1, hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
    )


def _start_of_year_ts(now: Optional[int] = None) -> int:
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    return int(
        dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    )


def _safe_objectid(val: Any) -> Any:
    if isinstance(val, ObjectId):
        return val
    try:
        return ObjectId(val)
    except Exception:
        return val


def _growth(current: int, previous: int) -> GrowthMetric:
    change = current - previous
    if previous == 0:
        pct = 100.0 if current > 0 else 0.0
    else:
        pct = round((change / previous) * 100, 1)
    return GrowthMetric(
        current=current,
        previous=previous,
        change=change,
        change_percent=pct,
    )


def _build_distribution(
    counts: Dict[str, int],
    label_map: Optional[Dict[str, str]] = None,
) -> List[DistributionSlice]:
    total = sum(counts.values())
    out: List[DistributionSlice] = []
    for key, value in counts.items():
        if value == 0:
            continue
        label = (label_map or {}).get(
            key, key.replace("_", " ").title() if key else "Unknown"
        )
        pct = round((value / total) * 100, 1) if total else 0.0
        out.append(
            DistributionSlice(
                key=key or "unknown", label=label, value=value, percentage=pct
            )
        )
    out.sort(key=lambda s: s.value, reverse=True)
    return out


# ─── Mongo aggregation helpers ────────────────────────────────────────


async def _group_count(
    collection: str, match: Dict[str, Any], field: str
) -> Dict[str, int]:
    pipeline: List[Dict[str, Any]] = [
        {"$match": match},
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
    ]
    out: Dict[str, int] = {}
    async for doc in db[collection].aggregate(pipeline):
        key = doc.get("_id")
        if key is None:
            key = ""
        out[str(key)] = int(doc.get("count", 0))
    return out


async def _daily_series(
    collection: str,
    match: Dict[str, Any],
    *,
    timestamp_field: str,
    days: int,
    now: int,
    sum_field: Optional[str] = None,
) -> List[TimeSeriesPoint]:
    """Daily-bucketed time series. ``sum_field`` aggregates a numeric
    column instead of counting documents (used for revenue)."""
    start = _start_of_day(now) - (days - 1) * 86400
    accumulator: Dict[str, Any]
    if sum_field:
        accumulator = {"value": {"$sum": f"${sum_field}"}}
    else:
        accumulator = {"value": {"$sum": 1}}
    pipeline = [
        {"$match": {**match, timestamp_field: {"$gte": start}}},
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": {
                            "$toDate": {"$multiply": [f"${timestamp_field}", 1000]}
                        },
                        "timezone": "UTC",
                    }
                },
                **accumulator,
            }
        },
    ]
    counts: Dict[str, int] = {}
    async for doc in db[collection].aggregate(pipeline):
        counts[str(doc["_id"])] = int(doc.get("value", 0) or 0)

    out: List[TimeSeriesPoint] = []
    for i in range(days):
        ts = start + i * 86400
        label = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append(
            TimeSeriesPoint(timestamp=ts, label=label, value=counts.get(label, 0))
        )
    return out


# ─── Section loaders ──────────────────────────────────────────────────


async def _tenant_overview(*, now: int) -> Dict[str, Any]:
    start_today = _start_of_day(now)
    start_7d = start_today - 6 * 86400
    start_30d = start_today - 29 * 86400
    start_week = _start_of_week_ts(now)
    start_last_week = start_week - 7 * 86400
    start_month = _start_of_month_ts(now)
    start_last_month = _shift_month(start_month, -1)

    async def _count(filter_dict: Dict[str, Any]) -> int:
        return await db.tenant_companies.count_documents(filter_dict)

    (
        total,
        active,
        today,
        seven_d,
        thirty_d,
        this_week,
        last_week,
        this_month,
        last_month,
    ) = await asyncio.gather(
        _count({}),
        _count({"is_active": True}),
        _count({"date_created": {"$gte": start_today}}),
        _count({"date_created": {"$gte": start_7d}}),
        _count({"date_created": {"$gte": start_30d}}),
        _count({"date_created": {"$gte": start_week}}),
        _count({"date_created": {"$gte": start_last_week, "$lt": start_week}}),
        _count({"date_created": {"$gte": start_month}}),
        _count({"date_created": {"$gte": start_last_month, "$lt": start_month}}),
    )

    return {
        "total_tenants": total,
        "active_tenants": active,
        "inactive_tenants": max(total - active, 0),
        "new_tenants_today": today,
        "new_tenants_7d": seven_d,
        "new_tenants_30d": thirty_d,
        "new_tenants_this_month": this_month,
        "new_tenants_last_month": last_month,
        "tenants_growth_wow": _growth(this_week, last_week),
        "tenants_growth_mom": _growth(this_month, last_month),
    }


async def _user_overview(*, now: int) -> Dict[str, Any]:
    start_today = _start_of_day(now)
    start_7d = start_today - 6 * 86400
    start_month = _start_of_month_ts(now)
    start_last_month = _shift_month(start_month, -1)

    (
        total_tenant_users,
        total_app_users,
        total_app_admins,
        total_visitors,
        visitors_today,
        visitors_7d,
        visitors_month,
        visitors_last_month,
    ) = await asyncio.gather(
        db.system_users.count_documents({}),
        db.users.count_documents({}),
        db.admins.count_documents({}),
        db.visitor_profiles.count_documents({"deleted_at": None}),
        db.visitor_profiles.count_documents(
            {"deleted_at": None, "date_created": {"$gte": start_today}}
        ),
        db.visitor_profiles.count_documents(
            {"deleted_at": None, "date_created": {"$gte": start_7d}}
        ),
        db.visitor_profiles.count_documents(
            {"deleted_at": None, "date_created": {"$gte": start_month}}
        ),
        db.visitor_profiles.count_documents(
            {
                "deleted_at": None,
                "date_created": {"$gte": start_last_month, "$lt": start_month},
            }
        ),
    )

    role_counts = await _group_count("system_users", {}, "role")
    breakdown = SystemUserRoleBreakdown()
    for role_key, count in role_counts.items():
        if hasattr(breakdown, role_key):
            setattr(breakdown, role_key, count)

    return {
        "total_tenant_users": total_tenant_users,
        "total_application_users": total_app_users,
        "total_application_admins": total_app_admins,
        "total_visitors_all_time": total_visitors,
        "visitors_today": visitors_today,
        "visitors_7d": visitors_7d,
        "visitors_this_month": visitors_month,
        "visitors_last_month": visitors_last_month,
        "visitors_growth_mom": _growth(visitors_month, visitors_last_month),
        "system_user_role_breakdown": breakdown,
    }


async def _subscription_overview(*, now: int) -> Dict[str, Any]:
    start_30d = _start_of_day(now) - 29 * 86400
    start_month = _start_of_month_ts(now)
    start_last_month = _shift_month(start_month, -1)

    breakdown = SubscriptionStatusBreakdown()
    status_counts = await _group_count("subscriptions", {}, "status")
    for k, v in status_counts.items():
        if hasattr(breakdown, k):
            setattr(breakdown, k, v)

    total = sum(status_counts.values())

    cancelled_30d = await db.subscriptions.count_documents(
        {"cancelled_at": {"$gte": start_30d}}
    )
    new_subs_30d = await db.subscriptions.count_documents(
        {"date_created": {"$gte": start_30d}}
    )
    new_subs_this_month = await db.subscriptions.count_documents(
        {"date_created": {"$gte": start_month}}
    )
    new_subs_last_month = await db.subscriptions.count_documents(
        {"date_created": {"$gte": start_last_month, "$lt": start_month}}
    )

    active_at_period_start = (
        breakdown.active + breakdown.trialing + cancelled_30d
    )
    churn_rate = (
        round((cancelled_30d / active_at_period_start) * 100, 1)
        if active_at_period_start
        else 0.0
    )

    # Trial conversion rate: trials ended in the last 30d that are now active.
    # Approximated as active subs that had a trial_ends_at < now in last 30d
    # divided by all subs whose trial ended in that window.
    trials_ended_pipeline = [
        {
            "$match": {
                "trial_ends_at": {"$gte": start_30d, "$lt": now},
            }
        },
        {
            "$group": {
                "_id": "$status",
                "count": {"$sum": 1},
            }
        },
    ]
    converted = 0
    trials_ended = 0
    async for doc in db.subscriptions.aggregate(trials_ended_pipeline):
        c = int(doc["count"])
        trials_ended += c
        if doc["_id"] in ("active", "past_due"):
            converted += c
    trial_conversion = (
        round((converted / trials_ended) * 100, 1) if trials_ended else 0.0
    )

    return {
        "total_subscriptions": total,
        "active_subscriptions": breakdown.active,
        "trialing_subscriptions": breakdown.trialing,
        "past_due_subscriptions": breakdown.past_due,
        "suspended_subscriptions": breakdown.suspended,
        "cancelled_30d": cancelled_30d,
        "new_subscriptions_30d": new_subs_30d,
        "subscriptions_growth_mom": _growth(new_subs_this_month, new_subs_last_month),
        "churn_rate_30d": churn_rate,
        "trial_conversion_rate": trial_conversion,
        "subscription_breakdown": breakdown,
    }


async def _plan_analytics() -> Dict[str, Any]:
    total = await db.plans.count_documents({})
    active = await db.plans.count_documents({"status": "active"})
    archived = await db.plans.count_documents({"status": "archived"})
    draft = await db.plans.count_documents({"status": "draft"})

    # Subscribers per plan, with revenue contribution.
    pipeline = [
        {"$match": {"status": {"$in": ["active", "trialing"]}}},
        {
            "$group": {
                "_id": {"plan_id": "$plan_id", "billing_cycle": "$billing_cycle"},
                "count": {"$sum": 1},
                "total_price": {"$sum": "$effective_price"},
            }
        },
    ]
    plan_rollup: Dict[str, Dict[str, Any]] = {}
    async for doc in db.subscriptions.aggregate(pipeline):
        plan_id = str(doc["_id"]["plan_id"])
        cycle = doc["_id"].get("billing_cycle") or "monthly"
        entry = plan_rollup.setdefault(
            plan_id,
            {"count": 0, "monthly_revenue": 0.0, "yearly_revenue": 0.0},
        )
        entry["count"] += int(doc["count"])
        if cycle == "yearly":
            entry["yearly_revenue"] += float(doc["total_price"] or 0)
        else:
            entry["monthly_revenue"] += float(doc["total_price"] or 0)

    total_subs_in_plans = sum(e["count"] for e in plan_rollup.values()) or 0
    plan_distribution: List[PlanDistribution] = []
    plan_tier_counts: Dict[str, int] = {}
    for plan_id, entry in plan_rollup.items():
        plan_doc = await db.plans.find_one({"_id": _safe_objectid(plan_id)})
        plan_name = plan_doc.get("display_name") if plan_doc else "Unknown plan"
        plan_tier = plan_doc.get("tier", "unknown") if plan_doc else "unknown"
        pct = (
            round((entry["count"] / total_subs_in_plans) * 100, 1)
            if total_subs_in_plans
            else 0.0
        )
        plan_distribution.append(
            PlanDistribution(
                plan_id=plan_id,
                plan_name=plan_name or "Unknown plan",
                plan_tier=plan_tier,
                subscriber_count=entry["count"],
                monthly_revenue=round(entry["monthly_revenue"], 2),
                yearly_revenue=round(entry["yearly_revenue"], 2),
                percentage=pct,
            )
        )
        plan_tier_counts[plan_tier] = plan_tier_counts.get(plan_tier, 0) + entry["count"]
    plan_distribution.sort(key=lambda p: p.subscriber_count, reverse=True)

    billing_cycle_counts = await _group_count(
        "subscriptions",
        {"status": {"$in": ["active", "trialing"]}},
        "billing_cycle",
    )
    payment_provider_counts = await _group_count(
        "tenant_companies",
        {"is_active": True, "default_payment_provider": {"$ne": None}},
        "default_payment_provider",
    )

    return {
        "total_plans": total,
        "active_plans": active,
        "archived_plans": archived,
        "draft_plans": draft,
        "plan_distribution": plan_distribution[:20],
        "plan_tier_distribution": _build_distribution(plan_tier_counts),
        "billing_cycle_distribution": _build_distribution(
            billing_cycle_counts,
            {"monthly": "Monthly", "yearly": "Yearly"},
        ),
        "payment_provider_distribution": _build_distribution(
            payment_provider_counts,
            {"stripe": "Stripe", "flutterwave": "Flutterwave"},
        ),
    }


async def _revenue_billing(*, now: int) -> Dict[str, Any]:
    # MRR / ARR from active+trialing subscriptions.
    revenue_pipeline = [
        {"$match": {"status": {"$in": ["active", "trialing"]}}},
        {
            "$group": {
                "_id": "$billing_cycle",
                "total": {"$sum": "$effective_price"},
            }
        },
    ]
    monthly_rev = 0.0
    yearly_rev = 0.0
    async for doc in db.subscriptions.aggregate(revenue_pipeline):
        if doc["_id"] == "monthly":
            monthly_rev = round(doc["total"] or 0, 2)
        elif doc["_id"] == "yearly":
            yearly_rev = round(doc["total"] or 0, 2)
    mrr = round(monthly_rev + (yearly_rev / 12.0), 2)
    arr = round(mrr * 12, 2)

    start_today = _start_of_day(now)
    start_7d = start_today - 6 * 86400
    start_30d = start_today - 29 * 86400
    start_week = _start_of_week_ts(now)
    start_last_week = start_week - 7 * 86400
    start_month = _start_of_month_ts(now)
    start_last_month = _shift_month(start_month, -1)

    async def _sum(match: Dict[str, Any]) -> int:
        pipeline: List[Dict[str, Any]] = [
            {"$match": match},
            {"$group": {"_id": None, "total": {"$sum": "$total_minor"}}},
        ]
        async for doc in db.invoices.aggregate(pipeline):
            return int(doc.get("total", 0) or 0)
        return 0

    paid_match = {"status": "paid"}
    (
        rev_today,
        rev_7d,
        rev_30d,
        rev_this_week,
        rev_last_week,
    ) = await asyncio.gather(
        _sum({**paid_match, "paid_at": {"$gte": start_today}}),
        _sum({**paid_match, "paid_at": {"$gte": start_7d}}),
        _sum({**paid_match, "paid_at": {"$gte": start_30d}}),
        _sum({**paid_match, "paid_at": {"$gte": start_week}}),
        _sum(
            {**paid_match, "paid_at": {"$gte": start_last_week, "$lt": start_week}}
        ),
    )
    rev_this_month, rev_last_month = await asyncio.gather(
        _sum({**paid_match, "paid_at": {"$gte": start_month}}),
        _sum(
            {**paid_match, "paid_at": {"$gte": start_last_month, "$lt": start_month}}
        ),
    )

    invoice_status_counts = await _group_count("invoices", {}, "status")
    invoice_breakdown = InvoiceStatusBreakdown()
    for k, v in invoice_status_counts.items():
        if hasattr(invoice_breakdown, k):
            setattr(invoice_breakdown, k, v)

    invoice_count_30d = await db.invoices.count_documents(
        {"date_created": {"$gte": start_30d}}
    )
    paid_invoice_count_30d = await db.invoices.count_documents(
        {"status": "paid", "paid_at": {"$gte": start_30d}}
    )
    failed_invoice_count_30d = await db.invoices.count_documents(
        {"status": "void", "date_created": {"$gte": start_30d}}
    )

    avg_invoice_value = (
        int(rev_30d / paid_invoice_count_30d) if paid_invoice_count_30d else 0
    )

    # Payment success rate (last 30 days).
    payment_status_counts = await _group_count(
        "payment_transactions",
        {"created_at": {"$gte": start_30d}},
        "status",
    )
    payments_succeeded = (
        payment_status_counts.get("succeeded", 0)
        + payment_status_counts.get("success", 0)
        + payment_status_counts.get("paid", 0)
    )
    payments_failed = (
        payment_status_counts.get("failed", 0)
        + payment_status_counts.get("error", 0)
    )
    total_attempts = payments_succeeded + payments_failed
    payment_success = (
        round((payments_succeeded / total_attempts) * 100, 1)
        if total_attempts
        else 0.0
    )

    dunning_queue = await db.subscriptions.count_documents({"status": "past_due"})

    return {
        "total_monthly_revenue": monthly_rev,
        "total_yearly_revenue": yearly_rev,
        "mrr": mrr,
        "arr": arr,
        "revenue_30d_minor": rev_30d,
        "revenue_7d_minor": rev_7d,
        "revenue_today_minor": rev_today,
        "avg_invoice_value_minor": avg_invoice_value,
        "invoice_count_30d": invoice_count_30d,
        "paid_invoice_count_30d": paid_invoice_count_30d,
        "failed_invoice_count_30d": failed_invoice_count_30d,
        "invoice_status_breakdown": invoice_breakdown,
        "revenue_growth_wow": _growth(rev_this_week, rev_last_week),
        "revenue_growth_mom": _growth(rev_this_month, rev_last_month),
        "payments_succeeded_30d": payments_succeeded,
        "payments_failed_30d": payments_failed,
        "payment_success_rate": payment_success,
        "dunning_queue_size": dunning_queue,
        # Side channel for the daily revenue series — avoids re-running
        # a 30-day sum aggregation in the time-series block.
        "_period_starts": {
            "start_30d": start_30d,
        },
    }


async def _incident_overview(*, now: int) -> Dict[str, Any]:
    start_today = _start_of_day(now)
    start_30d = start_today - 29 * 86400

    total = await db.incident_logs.count_documents({})
    open_ = await db.incident_logs.count_documents(
        {"status": {"$nin": ["closed", "resolved"]}}
    )
    critical = await db.incident_logs.count_documents({"risk_level": "critical"})
    today = await db.incident_logs.count_documents(
        {"date_created": {"$gte": start_today}}
    )
    thirty_d = await db.incident_logs.count_documents(
        {"date_created": {"$gte": start_30d}}
    )

    type_counts = await _group_count("incident_logs", {}, "incident_type")
    status_counts = await _group_count("incident_logs", {}, "status")

    # Top tenants by incident count.
    pipeline = [
        {"$group": {"_id": "$tenant_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    rows: List[TopTenantByIncidents] = []
    async for doc in db.incident_logs.aggregate(pipeline):
        tenant_id = str(doc["_id"])
        if not tenant_id:
            continue
        tenant_doc = await db.tenant_companies.find_one(
            {"_id": _safe_objectid(tenant_id)}
        )
        rows.append(
            TopTenantByIncidents(
                tenant_id=tenant_id,
                company_name=(tenant_doc or {}).get("company_name", "Unknown"),
                incident_count=int(doc["count"]),
            )
        )

    return {
        "total_incidents": total,
        "open_incidents": open_,
        "critical_incidents": critical,
        "incidents_today": today,
        "incidents_30d": thirty_d,
        "incident_type_distribution": _build_distribution(type_counts),
        "incident_status_distribution": _build_distribution(status_counts),
        "top_tenants_by_incidents": rows,
    }


async def _visitor_cross_tenant(*, now: int) -> Dict[str, Any]:
    start_today = _start_of_day(now)
    start_7d = start_today - 6 * 86400
    start_30d = start_today - 29 * 86400

    check_ins_today = await db.visit_sessions.count_documents(
        {"check_in_time": {"$gte": start_today}}
    )
    check_ins_7d = await db.visit_sessions.count_documents(
        {"check_in_time": {"$gte": start_7d}}
    )
    check_ins_30d = await db.visit_sessions.count_documents(
        {"check_in_time": {"$gte": start_30d}}
    )

    # Top tenants by visitor profile count (lifetime).
    visitor_pipeline = [
        {"$match": {"deleted_at": None}},
        {"$group": {"_id": "$tenant_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    top_visitor_rows: List[TopTenantByVisitors] = []
    async for doc in db.visitor_profiles.aggregate(visitor_pipeline):
        tenant_id = str(doc["_id"])
        if not tenant_id:
            continue
        tenant_doc = await db.tenant_companies.find_one(
            {"_id": _safe_objectid(tenant_id)}
        )
        top_visitor_rows.append(
            TopTenantByVisitors(
                tenant_id=tenant_id,
                company_name=(tenant_doc or {}).get("company_name", "Unknown"),
                visitor_count=int(doc["count"]),
            )
        )

    # Top tenants by check-in volume (last 30 days).
    activity_pipeline = [
        {"$match": {"check_in_time": {"$gte": start_30d}}},
        {"$group": {"_id": "$tenant_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    top_activity_rows: List[TopTenantByActivity] = []
    async for doc in db.visit_sessions.aggregate(activity_pipeline):
        tenant_id = str(doc["_id"])
        if not tenant_id:
            continue
        tenant_doc = await db.tenant_companies.find_one(
            {"_id": _safe_objectid(tenant_id)}
        )
        total_sessions = await db.visit_sessions.count_documents(
            {"tenant_id": tenant_id}
        )
        top_activity_rows.append(
            TopTenantByActivity(
                tenant_id=tenant_id,
                company_name=(tenant_doc or {}).get("company_name", "Unknown"),
                check_ins_30d=int(doc["count"]),
                total_visit_sessions=total_sessions,
            )
        )

    return {
        "visitor_check_ins_today": check_ins_today,
        "visitor_check_ins_7d": check_ins_7d,
        "visitor_check_ins_30d": check_ins_30d,
        "top_tenants_by_visitors": top_visitor_rows,
        "top_tenants_by_activity": top_activity_rows,
    }


async def _top_tenants_by_revenue() -> List[TopTenantByRevenue]:
    pipeline = [
        {"$match": {"status": {"$in": ["active", "trialing"]}}},
        {
            "$group": {
                "_id": "$tenant_id",
                "monthly": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$billing_cycle", "monthly"]},
                            "$effective_price",
                            0,
                        ]
                    }
                },
                "yearly": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$billing_cycle", "yearly"]},
                            "$effective_price",
                            0,
                        ]
                    }
                },
                "first_sub": {"$first": "$$ROOT"},
            }
        },
        {
            "$addFields": {
                "rank_value": {"$add": ["$monthly", {"$divide": ["$yearly", 12]}]}
            }
        },
        {"$sort": {"rank_value": -1}},
        {"$limit": 10},
    ]
    rows: List[TopTenantByRevenue] = []
    async for doc in db.subscriptions.aggregate(pipeline):
        tenant_id = str(doc["_id"])
        if not tenant_id:
            continue
        tenant_doc = await db.tenant_companies.find_one(
            {"_id": _safe_objectid(tenant_id)}
        )
        sub = doc.get("first_sub") or {}
        plan_doc = (
            await db.plans.find_one({"_id": _safe_objectid(sub.get("plan_id"))})
            if sub.get("plan_id")
            else None
        )
        rows.append(
            TopTenantByRevenue(
                tenant_id=tenant_id,
                company_name=(tenant_doc or {}).get("company_name", "Unknown"),
                monthly_revenue=round(doc.get("monthly", 0) or 0, 2),
                yearly_revenue=round(doc.get("yearly", 0) or 0, 2),
                plan_name=(plan_doc or {}).get("display_name"),
                plan_tier=(plan_doc or {}).get("tier"),
                status=sub.get("status"),
            )
        )
    return rows


async def _geography() -> Dict[str, Any]:
    counts = await _group_count(
        "tenant_companies",
        {"is_active": True, "country_of_hosting": {"$ne": None}},
        "country_of_hosting",
    )
    return {"tenants_by_country": _build_distribution(counts)}


async def _onboarding_pipeline(*, now: int) -> Dict[str, Any]:
    start_30d = _start_of_day(now) - 29 * 86400
    total = await db.onboarding_submissions.count_documents({})
    new = await db.onboarding_submissions.count_documents({"status": "new"})
    accepted_30d = await db.onboarding_submissions.count_documents(
        {
            "status": {"$in": ["accepted", "partial_accepted"]},
            "date_created": {"$gte": start_30d},
        }
    )
    rejected_30d = await db.onboarding_submissions.count_documents(
        {"status": "rejected", "date_created": {"$gte": start_30d}}
    )
    completed_30d = await db.onboarding_submissions.count_documents(
        {"status": "completed", "date_created": {"$gte": start_30d}}
    )
    status_counts = await _group_count("onboarding_submissions", {}, "status")
    decided = accepted_30d + rejected_30d
    acceptance = (
        round((accepted_30d / decided) * 100, 1) if decided else 0.0
    )
    return {
        "onboarding_total": total,
        "onboarding_new": new,
        "onboarding_accepted_30d": accepted_30d,
        "onboarding_rejected_30d": rejected_30d,
        "onboarding_completed_30d": completed_30d,
        "onboarding_status_distribution": _build_distribution(status_counts),
        "onboarding_acceptance_rate": acceptance,
    }


async def _support_overview(*, now: int) -> Dict[str, Any]:
    start_30d = _start_of_day(now) - 29 * 86400
    total = await db.support_cases.count_documents({})
    open_ = await db.support_cases.count_documents(
        {"status": {"$nin": ["resolved", "closed"]}}
    )
    last_30d = await db.support_cases.count_documents(
        {"date_created": {"$gte": start_30d}}
    )
    status_counts = await _group_count("support_cases", {}, "status")
    priority_counts = await _group_count("support_cases", {}, "priority")
    category_counts = await _group_count("support_cases", {}, "category")

    # Top tenants by support volume.
    pipeline = [
        {"$group": {"_id": "$tenant_id", "total": {"$sum": 1}, "open": {"$sum": {"$cond": [{"$nin": ["$status", ["resolved", "closed"]]}, 1, 0]}}}},
        {"$sort": {"total": -1}},
        {"$limit": 10},
    ]
    top_rows: List[TopTenantBySupport] = []
    async for doc in db.support_cases.aggregate(pipeline):
        tenant_id = str(doc["_id"])
        if not tenant_id:
            continue
        tenant_doc = await db.tenant_companies.find_one(
            {"_id": _safe_objectid(tenant_id)}
        )
        top_rows.append(
            TopTenantBySupport(
                tenant_id=tenant_id,
                company_name=(tenant_doc or {}).get("company_name", "Unknown"),
                open_cases=int(doc.get("open", 0)),
                total_cases=int(doc.get("total", 0)),
            )
        )

    return {
        "support_cases_total": total,
        "support_cases_open": open_,
        "support_cases_30d": last_30d,
        "support_status_distribution": _build_distribution(status_counts),
        "support_priority_distribution": _build_distribution(priority_counts),
        "support_category_distribution": _build_distribution(category_counts),
        "top_tenants_by_support": top_rows,
    }


async def _compliance_cross_tenant(*, now: int) -> Dict[str, Any]:
    start_30d = _start_of_day(now) - 29 * 86400
    open_dsr = await db.data_subject_requests.count_documents(
        {"status": {"$nin": ["completed", "rejected"]}}
    )
    total_dsr = await db.data_subject_requests.count_documents({})
    dsr_30d = await db.data_subject_requests.count_documents(
        {"date_created": {"$gte": start_30d}}
    )
    dsr_status_counts = await _group_count(
        "data_subject_requests", {}, "status"
    )

    deadline_in_24h = now + 24 * 3600
    incidents_approaching = await db.incident_logs.count_documents(
        {
            "notification_deadline": {"$lte": deadline_in_24h, "$gte": now},
            "notification_sent_at": None,
        }
    )

    return {
        "dsr_open": open_dsr,
        "dsr_total": total_dsr,
        "dsr_30d": dsr_30d,
        "dsr_status_distribution": _build_distribution(dsr_status_counts),
        "incidents_approaching_deadline": incidents_approaching,
    }


async def _time_series_block(*, now: int) -> Dict[str, Any]:
    start_30d = _start_of_day(now) - 29 * 86400

    (
        tenant_signups,
        sub_signups,
        visitor_signups,
        check_ins,
        revenue,
        incidents,
    ) = await asyncio.gather(
        _daily_series(
            "tenant_companies", {}, timestamp_field="date_created", days=30, now=now
        ),
        _daily_series(
            "subscriptions", {}, timestamp_field="date_created", days=30, now=now
        ),
        _daily_series(
            "visitor_profiles",
            {"deleted_at": None},
            timestamp_field="date_created",
            days=30,
            now=now,
        ),
        _daily_series(
            "visit_sessions",
            {},
            timestamp_field="check_in_time",
            days=30,
            now=now,
        ),
        _daily_series(
            "invoices",
            {"status": "paid", "paid_at": {"$gte": start_30d}},
            timestamp_field="paid_at",
            days=30,
            now=now,
            sum_field="total_minor",
        ),
        _daily_series(
            "incident_logs", {}, timestamp_field="date_created", days=30, now=now
        ),
    )

    return {
        "tenant_signups_last_30_days": tenant_signups,
        "subscription_signups_last_30_days": sub_signups,
        "visitor_signups_last_30_days": visitor_signups,
        "visit_check_ins_last_30_days": check_ins,
        "revenue_last_30_days_minor": revenue,
        "incidents_last_30_days": incidents,
    }


async def _heatmaps_block(*, now: int) -> Dict[str, Any]:
    start_30d = _start_of_day(now) - 29 * 86400
    match = {"check_in_time": {"$gte": start_30d}}

    hour_pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {
                    "$hour": {
                        "$toDate": {"$multiply": ["$check_in_time", 1000]}
                    }
                },
                "count": {"$sum": 1},
            }
        },
    ]
    hour_counts: Dict[int, int] = {}
    async for doc in db.visit_sessions.aggregate(hour_pipeline):
        hour_counts[int(doc["_id"] or 0)] = int(doc["count"])
    hourly = [
        HourlyBucket(hour=h, label=f"{h:02d}:00", value=hour_counts.get(h, 0))
        for h in range(24)
    ]

    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {
                    "$isoDayOfWeek": {
                        "$toDate": {"$multiply": ["$check_in_time", 1000]}
                    }
                },
                "count": {"$sum": 1},
            }
        },
    ]
    dow_counts: Dict[int, int] = {}
    async for doc in db.visit_sessions.aggregate(dow_pipeline):
        iso = int(doc["_id"] or 1)
        dow_counts[iso - 1] = int(doc["count"])
    dow = [
        DayOfWeekBucket(day=d, label=dow_labels[d], value=dow_counts.get(d, 0))
        for d in range(7)
    ]

    return {"hourly_distribution": hourly, "day_of_week_distribution": dow}


async def _recent_activity(*, now: int) -> Dict[str, Any]:
    start_30d = _start_of_day(now) - 29 * 86400

    # Recent signups: 10 newest tenants.
    recent_cursor = (
        db.tenant_companies.find({}).sort("date_created", -1).limit(10)
    )
    recent_signups: List[TenantBriefRow] = []
    async for doc in recent_cursor:
        sub = await db.subscriptions.find_one({"tenant_id": str(doc["_id"])})
        plan_doc = None
        if sub and sub.get("plan_id"):
            plan_doc = await db.plans.find_one(
                {"_id": _safe_objectid(sub["plan_id"])}
            )
        recent_signups.append(
            TenantBriefRow(
                id=str(doc["_id"]),
                company_name=doc.get("company_name", "Unknown"),
                is_active=bool(doc.get("is_active", True)),
                country_of_hosting=doc.get("country_of_hosting"),
                date_created=doc.get("date_created"),
                plan_name=(plan_doc or {}).get("display_name"),
                plan_tier=(plan_doc or {}).get("tier"),
                subscription_status=(sub or {}).get("status"),
            )
        )

    # Recently active tenants: top 10 by visit-session count in the last 30d.
    pipeline = [
        {"$match": {"check_in_time": {"$gte": start_30d}}},
        {"$group": {"_id": "$tenant_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    recently_active: List[TenantBriefRow] = []
    async for doc in db.visit_sessions.aggregate(pipeline):
        tenant_id = str(doc["_id"])
        if not tenant_id:
            continue
        tenant_doc = await db.tenant_companies.find_one(
            {"_id": _safe_objectid(tenant_id)}
        )
        if not tenant_doc:
            continue
        sub = await db.subscriptions.find_one({"tenant_id": tenant_id})
        plan_doc = None
        if sub and sub.get("plan_id"):
            plan_doc = await db.plans.find_one(
                {"_id": _safe_objectid(sub["plan_id"])}
            )
        recently_active.append(
            TenantBriefRow(
                id=tenant_id,
                company_name=tenant_doc.get("company_name", "Unknown"),
                is_active=bool(tenant_doc.get("is_active", True)),
                country_of_hosting=tenant_doc.get("country_of_hosting"),
                date_created=tenant_doc.get("date_created"),
                plan_name=(plan_doc or {}).get("display_name"),
                plan_tier=(plan_doc or {}).get("tier"),
                subscription_status=(sub or {}).get("status"),
                visitors_count=int(doc["count"]),
            )
        )

    return {
        "recent_tenant_signups": recent_signups,
        "recently_active_tenants": recently_active,
    }


# ─── Public entrypoint ────────────────────────────────────────────────


async def get_admin_dashboard_stats() -> AdminDashboardStats:
    """Aggregate platform-wide stats for the application admin dashboard."""
    now = int(time.time())

    # Three batched gathers — each ≤6 args so asyncio.gather's typed
    # overloads propagate the per-call return types.
    tenants, users, subs, plans, revenue, incidents = await asyncio.gather(
        _tenant_overview(now=now),
        _user_overview(now=now),
        _subscription_overview(now=now),
        _plan_analytics(),
        _revenue_billing(now=now),
        _incident_overview(now=now),
    )
    visitors, top_revenue, geography, onboarding, support, compliance = (
        await asyncio.gather(
            _visitor_cross_tenant(now=now),
            _top_tenants_by_revenue(),
            _geography(),
            _onboarding_pipeline(now=now),
            _support_overview(now=now),
            _compliance_cross_tenant(now=now),
        )
    )
    time_series, heatmaps, recent = await asyncio.gather(
        _time_series_block(now=now),
        _heatmaps_block(now=now),
        _recent_activity(now=now),
    )

    revenue.pop("_period_starts", None)
    visitors_payload = {
        **visitors,
        "top_tenants_by_revenue": top_revenue,
    }

    payload: Dict[str, Any] = {
        **tenants,
        **users,
        **subs,
        **plans,
        **revenue,
        **incidents,
        **visitors_payload,
        **geography,
        **onboarding,
        **support,
        **compliance,
        **time_series,
        **heatmaps,
        **recent,
        # Legacy alias retained so older UIs don't break.
        "recent_signups_30d": tenants["new_tenants_30d"],
        "period": {
            "now": now,
            "start_today": _start_of_today_ts(now),
            "start_week": _start_of_week_ts(now),
            "start_month": _start_of_month_ts(now),
            "start_quarter": _start_of_quarter_ts(now),
            "start_year": _start_of_year_ts(now),
        },
        "last_updated": now,
    }
    return AdminDashboardStats(**payload)
