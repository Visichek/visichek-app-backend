"""Range-aware, tabbed analytics for the PLATFORM-ADMIN dashboard.

Backs ``GET /v1/admins/dashboard/insights`` — the admin-shell counterpart to
the tenant ``GET /v1/dashboard/insights``. Same engine (caller-chosen window,
auto-granularity, zero-filled series, preceding-window KPI trends) but:

* the lower bound is the PLATFORM (first tenant), not one tenant's creation;
* there is NO plan gating — an application admin sees everything;
* it keeps the existing 5 admin tabs (overview / tenants / billing / activity /
  risk), each recomposed as range-aware sections;
* it adds a ``table`` section type for the real tenant rows the admin UI shows.

Documented end-to-end in ``stats.txt`` (ADMIN INSIGHTS section).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from core.database import db
from schemas.insights_schema import (
    AdminInsightsMeta,
    AdminInsightsResponse,
    HourlyBucket,
    InsightsSection,
    Kpi,
    Trend,
)
from services.admin_dashboard_service import (
    _build_distribution,
    _group_count,
    _safe_objectid,
    _start_of_today_ts,
    _top_tenants_by_revenue,
)
from services.dashboard_service import _build_top_items
from services.insights_service import (
    _auto_granularity,
    _bucket_boundaries,
    _fold_counts,
    _raw_unit_counts,
    _resolve_range,
    _trend,
)

_DAY = 86400

# Section ids referenced by each tab. Only the requested tab's sections are
# computed.
_TAB_SECTIONS: Dict[str, List[str]] = {
    "overview": ["tenantSignups", "revenue", "planTier", "topRevenue"],
    "tenants": ["tenantSignups", "planTier", "geography", "roleBreakdown", "recentSignups"],
    "billing": [
        "revenue",
        "newSubscriptions",
        "invoiceStatus",
        "billingCycle",
        "paymentProvider",
        "topRevenue",
    ],
    "activity": [
        "visitorCheckIns",
        "visitorSignups",
        "hourly",
        "topVisitors",
        "topActivity",
        "onboarding",
    ],
    "risk": [
        "incidents",
        "incidentType",
        "incidentStatus",
        "dsrStatus",
        "supportStatus",
        "supportPriority",
        "topIncidents",
        "topSupport",
    ],
}


class _Filters:
    """All admin filter params; each section applies the ones relevant to it."""

    def __init__(self, **kw: Optional[str]) -> None:
        self.plan_tier = kw.get("plan_tier")
        self.subscription_status = kw.get("subscription_status")
        self.billing_cycle = kw.get("billing_cycle")
        self.payment_provider = kw.get("payment_provider")
        self.country = kw.get("country")
        self.tenant_id = kw.get("tenant_id")
        self.incident_type = kw.get("incident_type")
        self.incident_status = kw.get("incident_status")
        self.support_status = kw.get("support_status")
        self.support_priority = kw.get("support_priority")
        self.onboarding_status = kw.get("onboarding_status")


# ─── Platform bounds ──────────────────────────────────────────────────


async def _platform_launch_at() -> int:
    """Creation date of the first tenant — the 'no data before this' point."""
    async for doc in db["tenant_companies"].aggregate(
        [
            {"$match": {"date_created": {"$ne": None}}},
            {"$group": {"_id": None, "min": {"$min": "$date_created"}}},
        ]
    ):
        return int(doc.get("min") or 0)
    return 0


async def _earliest_data(fallback: int) -> int:
    async def _min(collection: str, field: str) -> Optional[int]:
        async for doc in db[collection].aggregate(
            [
                {"$match": {field: {"$ne": None}}},
                {"$group": {"_id": None, "min": {"$min": f"${field}"}}},
            ]
        ):
            return int(doc["min"]) if doc.get("min") else None
        return None

    tenants_min, visits_min = await asyncio.gather(
        _min("tenant_companies", "date_created"),
        _min("visit_sessions", "check_in_time"),
    )
    candidates = [v for v in (tenants_min, visits_min) if v]
    if not candidates:
        return fallback
    return max(min(candidates), fallback)


# ─── Sum-bucketing (revenue) — counts variant lives in insights_service ──


async def _raw_unit_sums(
    collection: str,
    match: Dict[str, Any],
    ts_field: str,
    sum_field: str,
    *,
    start: int,
    stop: int,
    granularity: str,
) -> Dict[int, int]:
    unit = "hour" if granularity == "hour" else "day"
    fmt = "%Y-%m-%dT%H:00:00Z" if unit == "hour" else "%Y-%m-%dT00:00:00Z"
    pipeline: List[Dict[str, Any]] = [
        {"$match": {**match, ts_field: {"$gte": start, "$lte": stop}}},
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": fmt,
                        "date": {"$toDate": {"$multiply": [f"${ts_field}", 1000]}},
                        "timezone": "UTC",
                    }
                },
                "value": {"$sum": f"${sum_field}"},
            }
        },
    ]
    out: Dict[int, int] = {}
    async for doc in db[collection].aggregate(pipeline):
        key = str(doc.get("_id") or "")
        if not key:
            continue
        try:
            dt = datetime.strptime(key, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out[int(dt.timestamp())] = out.get(int(dt.timestamp()), 0) + int(doc.get("value", 0) or 0)
    return out


# ─── Filter -> Mongo match helpers ────────────────────────────────────


async def _plan_ids_for_tier(tier: str) -> List[str]:
    cursor = db["plans"].find({"tier": tier}, projection={"_id": 1})
    return [str(doc["_id"]) async for doc in cursor]


async def _tenant_match(f: _Filters) -> Dict[str, Any]:
    match: Dict[str, Any] = {}
    if f.country:
        match["country_of_hosting"] = f.country
    if f.payment_provider:
        match["default_payment_provider"] = f.payment_provider
    if f.tenant_id and ObjectId.is_valid(f.tenant_id):
        match["_id"] = ObjectId(f.tenant_id)
    return match


async def _subscription_match(f: _Filters) -> Dict[str, Any]:
    match: Dict[str, Any] = {}
    if f.subscription_status:
        match["status"] = f.subscription_status
    if f.billing_cycle:
        match["billing_cycle"] = f.billing_cycle
    if f.tenant_id:
        match["tenant_id"] = f.tenant_id
    if f.plan_tier:
        match["plan_id"] = {"$in": await _plan_ids_for_tier(f.plan_tier)}
    return match


def _incident_match(f: _Filters) -> Dict[str, Any]:
    match: Dict[str, Any] = {}
    if f.incident_type:
        match["incident_type"] = f.incident_type
    if f.incident_status:
        match["status"] = f.incident_status
    if f.tenant_id:
        match["tenant_id"] = f.tenant_id
    return match


def _support_match(f: _Filters) -> Dict[str, Any]:
    match: Dict[str, Any] = {}
    if f.support_status:
        match["status"] = f.support_status
    if f.support_priority:
        match["priority"] = f.support_priority
    if f.tenant_id:
        match["tenant_id"] = f.tenant_id
    return match


# ─── Generic count / sum + trend helpers ──────────────────────────────


async def _count_in(
    collection: str, base: Dict[str, Any], ts_field: str, start: int, stop: int
) -> int:
    return int(
        await db[collection].count_documents(
            {**base, ts_field: {"$gte": start, "$lte": stop}}
        )
    )


async def _sum_minor_in(
    collection: str, base: Dict[str, Any], ts_field: str, start: int, stop: int
) -> int:
    pipeline = [
        {"$match": {**base, ts_field: {"$gte": start, "$lte": stop}}},
        {"$group": {"_id": None, "total": {"$sum": "$total_minor"}}},
    ]
    async for doc in db[collection].aggregate(pipeline):
        return int(doc.get("total", 0) or 0)
    return 0


def _maybe_trend(cur: float, prev: float, *, good_when_up: bool = True) -> Trend:
    return _trend(cur, prev, good_when_up=good_when_up)


# ─── Top-tenant ranking (topList sections) ────────────────────────────


async def _top_tenants(
    collection: str,
    match: Dict[str, Any],
    *,
    limit: int = 10,
) -> List[Any]:
    """Rank tenants by document count, resolving company names for labels."""
    pipeline: List[Dict[str, Any]] = [
        {"$match": match},
        {"$group": {"_id": "$tenant_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    counts: Dict[str, int] = {}
    async for doc in db[collection].aggregate(pipeline):
        tid = doc.get("_id")
        if tid:
            counts[str(tid)] = int(doc.get("count", 0))

    label_map: Dict[str, str] = {}
    ids = [ObjectId(t) for t in counts if ObjectId.is_valid(t)]
    if ids:
        async for tdoc in db["tenant_companies"].find({"_id": {"$in": ids}}):
            label_map[str(tdoc["_id"])] = tdoc.get("company_name") or "Unknown"
    return _build_top_items(counts, label_map=label_map, limit=limit)


# ─── Section builders ─────────────────────────────────────────────────


async def _ts_section(
    collection: str,
    match: Dict[str, Any],
    ts_field: str,
    *,
    title: str,
    value_label: str,
    start: int,
    stop: int,
    granularity: str,
    sum_field: Optional[str] = None,
) -> InsightsSection:
    boundaries = _bucket_boundaries(start, stop, granularity)
    if sum_field:
        raw = await _raw_unit_sums(
            collection, match, ts_field, sum_field, start=start, stop=stop, granularity=granularity
        )
    else:
        raw = await _raw_unit_counts(
            collection, match, ts_field, start=start, stop=stop, granularity=granularity
        )
    return InsightsSection(
        type="timeSeries",
        title=title,
        points=_fold_counts(raw, boundaries),
        value_label=value_label,
    )


def _table_section(title: str, rows: List[Dict[str, Any]], columns: List[str]) -> InsightsSection:
    return InsightsSection(type="table", title=title, rows=rows, columns=columns)


async def _recent_signup_rows(f: _Filters, limit: int = 10) -> List[Dict[str, Any]]:
    match = await _tenant_match(f)
    cursor = db["tenant_companies"].find(match).sort("date_created", -1).limit(limit)
    rows: List[Dict[str, Any]] = []
    async for doc in cursor:
        tid = str(doc["_id"])
        sub = await db["subscriptions"].find_one({"tenant_id": tid})
        plan_doc = None
        if sub and sub.get("plan_id"):
            plan_doc = await db["plans"].find_one({"_id": _safe_objectid(sub["plan_id"])})
        rows.append(
            {
                "id": tid,
                "companyName": doc.get("company_name", "Unknown"),
                "isActive": bool(doc.get("is_active", True)),
                "countryOfHosting": doc.get("country_of_hosting"),
                "dateCreated": doc.get("date_created"),
                "planName": (plan_doc or {}).get("display_name"),
                "planTier": (plan_doc or {}).get("tier"),
                "subscriptionStatus": (sub or {}).get("status"),
            }
        )
    return rows


async def _top_revenue_rows() -> List[Dict[str, Any]]:
    items = await _top_tenants_by_revenue()
    return [
        {
            "tenantId": i.tenant_id,
            "companyName": i.company_name,
            "planName": i.plan_name,
            "status": i.status,
            "monthlyRevenue": i.monthly_revenue,
            "yearlyRevenue": i.yearly_revenue,
        }
        for i in items
    ]


# ─── KPI builders per tab ─────────────────────────────────────────────


async def _mrr_arr() -> Tuple[float, float]:
    monthly = 0.0
    yearly = 0.0
    async for doc in db["subscriptions"].aggregate(
        [
            {"$match": {"status": {"$in": ["active", "trialing"]}}},
            {"$group": {"_id": "$billing_cycle", "total": {"$sum": "$effective_price"}}},
        ]
    ):
        if doc["_id"] == "monthly":
            monthly = round(doc["total"] or 0, 2)
        elif doc["_id"] == "yearly":
            yearly = round(doc["total"] or 0, 2)
    mrr = round(monthly + (yearly / 12.0), 2)
    return mrr, round(mrr * 12, 2)


async def _payment_success_rate(start: int, stop: int) -> float:
    counts = await _group_count(
        "payment_transactions", {"created_at": {"$gte": start, "$lte": stop}}, "status"
    )
    ok = counts.get("succeeded", 0) + counts.get("success", 0) + counts.get("paid", 0)
    bad = counts.get("failed", 0) + counts.get("error", 0)
    total = ok + bad
    return round((ok / total) * 100, 1) if total else 0.0


async def _onboarding_acceptance_rate(start: int, stop: int) -> float:
    accepted = await db["onboarding_submissions"].count_documents(
        {"status": {"$in": ["accepted", "partial_accepted"]}, "date_created": {"$gte": start, "$lte": stop}}
    )
    rejected = await db["onboarding_submissions"].count_documents(
        {"status": "rejected", "date_created": {"$gte": start, "$lte": stop}}
    )
    decided = accepted + rejected
    return round((accepted / decided) * 100, 1) if decided else 0.0


async def _build_kpis(
    tab: str, f: _Filters, start: int, stop: int, *, now: int
) -> List[Kpi]:
    span = stop - start
    prev_start, prev_stop = start - span, start
    tenant_base = await _tenant_match(f)

    if tab == "overview":
        total, active, new_cur, new_prev = await asyncio.gather(
            db["tenant_companies"].count_documents(tenant_base),
            db["tenant_companies"].count_documents({**tenant_base, "is_active": True}),
            _count_in("tenant_companies", tenant_base, "date_created", start, stop),
            _count_in("tenant_companies", tenant_base, "date_created", prev_start, prev_stop),
        )
        mrr, _arr = await _mrr_arr()
        (active_subs, trialing, open_inc, crit, open_dsr, visitors_today, checkins_today,
         rev_cur, rev_prev, pay_rate, dunning) = await asyncio.gather(
            db["subscriptions"].count_documents({"status": "active"}),
            db["subscriptions"].count_documents({"status": "trialing"}),
            db["incident_logs"].count_documents({"status": {"$nin": ["closed", "resolved"]}}),
            db["incident_logs"].count_documents({"risk_level": "critical"}),
            db["data_subject_requests"].count_documents({"status": {"$nin": ["completed", "rejected"]}}),
            db["visitor_profiles"].count_documents({"deleted_at": None, "date_created": {"$gte": _start_of_today_ts(now)}}),
            db["visit_sessions"].count_documents({"check_in_time": {"$gte": _start_of_today_ts(now)}}),
            _sum_minor_in("invoices", {"status": "paid"}, "paid_at", start, stop),
            _sum_minor_in("invoices", {"status": "paid"}, "paid_at", prev_start, prev_stop),
            _payment_success_rate(start, stop),
            db["subscriptions"].count_documents({"status": "past_due"}),
        )
        return [
            Kpi(key="totalTenants", label="Total tenants", value=int(total), description=f"{int(active)} active"),
            Kpi(key="mrr", label="MRR", value=mrr, unit="₦", description="Monthly recurring revenue (major units)"),
            Kpi(key="activeSubscriptions", label="Active subscriptions", value=int(active_subs), description=f"{int(trialing)} trialing"),
            Kpi(key="openIncidents", label="Open incidents", value=int(open_inc), description=f"{int(crit)} critical"),
            Kpi(key="openDsr", label="Open DSRs", value=int(open_dsr), description="Pending data-subject requests"),
            Kpi(key="visitorsToday", label="Visitors today", value=int(visitors_today), description=f"{int(checkins_today)} check-ins"),
            Kpi(key="revenueInRangeMinor", label="Revenue (range)", value=int(rev_cur), unit="minor", trend=_maybe_trend(rev_cur, rev_prev), description="Paid-invoice revenue in range (minor units)"),
            Kpi(key="newTenantsInRange", label="New tenants", value=int(new_cur), trend=_maybe_trend(new_cur, new_prev), description="Signups in range"),
            Kpi(key="paymentSuccessRate", label="Payment success", value=pay_rate, unit="%", description="Succeeded / attempted in range"),
            Kpi(key="dunningQueueSize", label="Dunning queue", value=int(dunning), description="Past-due subscriptions"),
        ]

    if tab == "tenants":
        total, active, new_cur, new_prev = await asyncio.gather(
            db["tenant_companies"].count_documents(tenant_base),
            db["tenant_companies"].count_documents({**tenant_base, "is_active": True}),
            _count_in("tenant_companies", tenant_base, "date_created", start, stop),
            _count_in("tenant_companies", tenant_base, "date_created", prev_start, prev_stop),
        )
        return [
            Kpi(key="totalTenants", label="Total tenants", value=int(total)),
            Kpi(key="activeTenants", label="Active tenants", value=int(active)),
            Kpi(key="inactiveTenants", label="Inactive tenants", value=max(int(total) - int(active), 0)),
            Kpi(key="newTenantsInRange", label="New tenants", value=int(new_cur), trend=_maybe_trend(new_cur, new_prev), description="Signups in range"),
        ]

    if tab == "billing":
        mrr, arr = await _mrr_arr()
        rev_cur, rev_prev, pay_rate, paid_count, past_due = await asyncio.gather(
            _sum_minor_in("invoices", {"status": "paid"}, "paid_at", start, stop),
            _sum_minor_in("invoices", {"status": "paid"}, "paid_at", prev_start, prev_stop),
            _payment_success_rate(start, stop),
            _count_in("invoices", {"status": "paid"}, "paid_at", start, stop),
            db["subscriptions"].count_documents({"status": "past_due"}),
        )
        avg_invoice = int(rev_cur / paid_count) if paid_count else 0
        return [
            Kpi(key="mrr", label="MRR", value=mrr, unit="₦", description="Monthly recurring revenue"),
            Kpi(key="arr", label="ARR", value=arr, unit="₦", description="Annual recurring revenue"),
            Kpi(key="revenueInRangeMinor", label="Revenue (range)", value=int(rev_cur), unit="minor", trend=_maybe_trend(rev_cur, rev_prev), description="Paid-invoice revenue in range (minor units)"),
            Kpi(key="paymentSuccessRate", label="Payment success", value=pay_rate, unit="%", description="Succeeded / attempted in range"),
            Kpi(key="avgInvoiceMinor", label="Avg invoice", value=avg_invoice, unit="minor", description="Range revenue / paid invoices"),
            Kpi(key="pastDueSubscriptions", label="Past due", value=int(past_due), description="Subscriptions in dunning"),
        ]

    if tab == "activity":
        start_today = _start_of_today_ts(now)
        visitors_today, ci_cur, ci_prev, su_cur, su_prev, onboard_rate = await asyncio.gather(
            db["visitor_profiles"].count_documents({"deleted_at": None, "date_created": {"$gte": start_today}}),
            _count_in("visit_sessions", {}, "check_in_time", start, stop),
            _count_in("visit_sessions", {}, "check_in_time", prev_start, prev_stop),
            _count_in("visitor_profiles", {"deleted_at": None}, "date_created", start, stop),
            _count_in("visitor_profiles", {"deleted_at": None}, "date_created", prev_start, prev_stop),
            _onboarding_acceptance_rate(start, stop),
        )
        return [
            Kpi(key="visitorsToday", label="Visitors today", value=int(visitors_today), description="New visitor profiles today"),
            Kpi(key="visitorCheckInsInRange", label="Check-ins", value=int(ci_cur), trend=_maybe_trend(ci_cur, ci_prev), description="Cross-tenant check-ins in range"),
            Kpi(key="newSignupsInRange", label="New visitors", value=int(su_cur), trend=_maybe_trend(su_cur, su_prev), description="Visitor signups in range"),
            Kpi(key="onboardingAcceptanceRate", label="Onboarding acceptance", value=onboard_rate, unit="%", description="Accepted / decided in range"),
        ]

    if tab == "risk":
        open_inc, crit, approaching, open_dsr, support_open = await asyncio.gather(
            db["incident_logs"].count_documents({"status": {"$nin": ["closed", "resolved"]}}),
            db["incident_logs"].count_documents({"risk_level": "critical"}),
            db["incident_logs"].count_documents(
                {"notification_deadline": {"$gte": now, "$lte": now + _DAY}, "notification_sent_at": None}
            ),
            db["data_subject_requests"].count_documents({"status": {"$nin": ["completed", "rejected"]}}),
            db["support_cases"].count_documents({"status": {"$nin": ["resolved", "closed"]}}),
        )
        return [
            Kpi(key="openIncidents", label="Open incidents", value=int(open_inc), description="Not yet closed"),
            Kpi(key="criticalIncidents", label="Critical incidents", value=int(crit), description="Critical risk level"),
            Kpi(key="incidentsApproachingDeadline", label="Approaching deadline", value=int(approaching), description="NDPC 72h within 24h"),
            Kpi(key="openDsr", label="Open DSRs", value=int(open_dsr), description="Pending data-subject requests"),
            Kpi(key="supportCasesOpen", label="Open support cases", value=int(support_open), description="Awaiting reply"),
        ]

    return []


# ─── Section dispatch per tab ─────────────────────────────────────────


async def _build_section(
    sid: str, f: _Filters, start: int, stop: int, granularity: str, *, now: int
) -> InsightsSection:
    tenant_base = await _tenant_match(f)
    sub_base = await _subscription_match(f)

    if sid == "tenantSignups":
        return await _ts_section(
            "tenant_companies", tenant_base, "date_created",
            title="Tenant signups", value_label="Signups",
            start=start, stop=stop, granularity=granularity,
        )
    if sid == "revenue":
        return await _ts_section(
            "invoices", {"status": "paid"}, "paid_at",
            title="Revenue", value_label="Revenue (minor)",
            start=start, stop=stop, granularity=granularity, sum_field="total_minor",
        )
    if sid == "newSubscriptions":
        return await _ts_section(
            "subscriptions", sub_base, "date_created",
            title="New subscriptions", value_label="Subscriptions",
            start=start, stop=stop, granularity=granularity,
        )
    if sid == "visitorCheckIns":
        return await _ts_section(
            "visit_sessions", {} if not f.tenant_id else {"tenant_id": f.tenant_id}, "check_in_time",
            title="Visitor check-ins", value_label="Check-ins",
            start=start, stop=stop, granularity=granularity,
        )
    if sid == "visitorSignups":
        return await _ts_section(
            "visitor_profiles", {"deleted_at": None}, "date_created",
            title="Visitor signups", value_label="Signups",
            start=start, stop=stop, granularity=granularity,
        )
    if sid == "incidents":
        section = await _ts_section(
            "incident_logs", _incident_match(f), "date_created",
            title="Incidents", value_label="Incidents",
            start=start, stop=stop, granularity=granularity,
        )
        past_deadline, approaching = await asyncio.gather(
            db["incident_logs"].count_documents(
                {"notification_deadline": {"$lt": now}, "notification_sent_at": None, "status": {"$nin": ["closed", "resolved"]}}
            ),
            db["incident_logs"].count_documents(
                {"notification_deadline": {"$gte": now, "$lte": now + _DAY}, "notification_sent_at": None, "status": {"$nin": ["closed", "resolved"]}}
            ),
        )
        section.meta = {"pastDeadline": int(past_deadline), "approachingDeadline": int(approaching)}
        return section

    if sid == "planTier":
        # Subscriptions (or tenants) by plan tier in range. Resolve each
        # active/trialing sub's plan tier.
        tier_counts: Dict[str, int] = {}
        async for doc in db["subscriptions"].aggregate(
            [
                {"$match": {**sub_base, "status": {"$in": ["active", "trialing"]}}},
                {"$group": {"_id": "$plan_id", "count": {"$sum": 1}}},
            ]
        ):
            plan_doc = await db["plans"].find_one({"_id": _safe_objectid(doc["_id"])})
            tier = (plan_doc or {}).get("tier", "unknown")
            tier_counts[tier] = tier_counts.get(tier, 0) + int(doc["count"])
        return InsightsSection(type="distribution", title="Plan tiers", slices=_build_distribution(tier_counts))

    if sid == "geography":
        counts = await _group_count("tenant_companies", {**tenant_base, "country_of_hosting": {"$ne": None}}, "country_of_hosting")
        return InsightsSection(type="distribution", title="Tenants by country", slices=_build_distribution(counts))
    if sid == "roleBreakdown":
        counts = await _group_count("system_users", {}, "role")
        return InsightsSection(type="distribution", title="System-user roles", slices=_build_distribution(counts))
    if sid == "invoiceStatus":
        counts = await _group_count("invoices", {"date_created": {"$gte": start, "$lte": stop}}, "status")
        return InsightsSection(type="distribution", title="Invoices by status", slices=_build_distribution(counts))
    if sid == "billingCycle":
        counts = await _group_count("subscriptions", {**sub_base, "status": {"$in": ["active", "trialing"]}}, "billing_cycle")
        return InsightsSection(type="distribution", title="Billing cycle", slices=_build_distribution(counts, {"monthly": "Monthly", "yearly": "Yearly"}))
    if sid == "paymentProvider":
        counts = await _group_count("tenant_companies", {"is_active": True, "default_payment_provider": {"$ne": None}}, "default_payment_provider")
        return InsightsSection(type="distribution", title="Payment provider", slices=_build_distribution(counts))
    if sid == "incidentType":
        counts = await _group_count("incident_logs", {**_incident_match(f), "date_created": {"$gte": start, "$lte": stop}}, "incident_type")
        return InsightsSection(type="distribution", title="Incidents by type", slices=_build_distribution(counts))
    if sid == "incidentStatus":
        counts = await _group_count("incident_logs", {**_incident_match(f), "date_created": {"$gte": start, "$lte": stop}}, "status")
        return InsightsSection(type="distribution", title="Incidents by status", slices=_build_distribution(counts))
    if sid == "dsrStatus":
        counts = await _group_count("data_subject_requests", {"date_created": {"$gte": start, "$lte": stop}}, "status")
        return InsightsSection(type="distribution", title="DSRs by status", slices=_build_distribution(counts))
    if sid == "supportStatus":
        counts = await _group_count("support_cases", {**_support_match(f), "date_created": {"$gte": start, "$lte": stop}}, "status")
        return InsightsSection(type="distribution", title="Support by status", slices=_build_distribution(counts))
    if sid == "supportPriority":
        counts = await _group_count("support_cases", {**_support_match(f), "date_created": {"$gte": start, "$lte": stop}}, "priority")
        return InsightsSection(type="distribution", title="Support by priority", slices=_build_distribution(counts))
    if sid == "onboarding":
        onboard_match: Dict[str, Any] = {"date_created": {"$gte": start, "$lte": stop}}
        if f.onboarding_status:
            onboard_match["status"] = f.onboarding_status
        counts = await _group_count("onboarding_submissions", onboard_match, "status")
        return InsightsSection(type="distribution", title="Onboarding pipeline", slices=_build_distribution(counts))

    if sid == "hourly":
        hourly_match: Dict[str, Any] = {} if not f.tenant_id else {"tenant_id": f.tenant_id}
        hour_counts: Dict[int, int] = {}
        async for doc in db["visit_sessions"].aggregate(
            [
                {"$match": {**hourly_match, "check_in_time": {"$gte": start, "$lte": stop}}},
                {"$group": {"_id": {"$hour": {"date": {"$toDate": {"$multiply": ["$check_in_time", 1000]}}, "timezone": "UTC"}}, "count": {"$sum": 1}}},
            ]
        ):
            hour_counts[int(doc.get("_id") or 0)] = int(doc.get("count", 0))
        buckets = [HourlyBucket(hour=h, label=f"{h:02d}:00", value=hour_counts.get(h, 0)) for h in range(24)]
        return InsightsSection(type="hourly", title="Check-ins by hour", buckets=buckets)

    if sid == "topVisitors":
        return InsightsSection(
            type="topList", title="Top tenants by visitors",
            items=await _top_tenants("visit_sessions", {"check_in_time": {"$gte": start, "$lte": stop}}),
        )
    if sid == "topActivity":
        return InsightsSection(
            type="topList", title="Top tenants by activity",
            items=await _top_tenants("visit_sessions", {"check_in_time": {"$gte": start, "$lte": stop}}),
        )
    if sid == "topIncidents":
        return InsightsSection(
            type="topList", title="Top tenants by incidents",
            items=await _top_tenants("incident_logs", {**_incident_match(f), "date_created": {"$gte": start, "$lte": stop}}),
        )
    if sid == "topSupport":
        return InsightsSection(
            type="topList", title="Top tenants by support",
            items=await _top_tenants("support_cases", {**_support_match(f), "status": {"$nin": ["resolved", "closed"]}}),
        )

    if sid == "topRevenue":
        return _table_section(
            "Top tenants by revenue", await _top_revenue_rows(),
            ["companyName", "planName", "status", "monthlyRevenue"],
        )
    if sid == "recentSignups":
        return _table_section(
            "Recent tenant signups", await _recent_signup_rows(f),
            ["companyName", "planName", "planTier", "subscriptionStatus", "countryOfHosting", "dateCreated"],
        )

    # Unknown section id -> empty table (defensive; never happens via _TAB_SECTIONS).
    return InsightsSection(type="table", title=sid, rows=[], columns=[])


# ─── Public entrypoint ────────────────────────────────────────────────


async def get_admin_insights(
    *,
    start: Optional[int] = None,
    stop: Optional[int] = None,
    granularity: Optional[str] = None,
    tab: Optional[str] = None,
    **filters: Optional[str],
) -> AdminInsightsResponse:
    now = int(time.time())
    active_tab = tab if tab in _TAB_SECTIONS else "overview"
    f = _Filters(**filters)

    launch_at = await _platform_launch_at()
    eff_start, eff_stop = _resolve_range(launch_at, start, stop, now=now)
    gran = _auto_granularity(eff_start, eff_stop, granularity)

    section_ids = _TAB_SECTIONS[active_tab]
    kpis, earliest = await asyncio.gather(
        _build_kpis(active_tab, f, eff_start, eff_stop, now=now),
        _earliest_data(launch_at or eff_start),
    )
    section_list: List[InsightsSection] = list(
        await asyncio.gather(
            *[_build_section(sid, f, eff_start, eff_stop, gran, now=now) for sid in section_ids]
        )
    )

    meta = AdminInsightsMeta(
        platform_launch_at=launch_at,
        earliest_data=earliest,
        applied_range={"start": eff_start, "stop": eff_stop},
        granularity=gran,
        tab=active_tab,
        last_updated=now,
    )
    return AdminInsightsResponse(
        meta=meta,
        kpis=kpis,
        sections={sid: sec for sid, sec in zip(section_ids, section_list)},
    )
