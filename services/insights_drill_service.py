"""Drill-down: the records behind a clicked Insights chart element.

When a user clicks a time-series point, a pie slice, a top-list bar, or a
table-implied bucket, the FE opens a "Selection" panel listing the UNDERLYING
records. This module returns those rows, paginated, honouring the SAME range,
filters, and (tenant) scoping as the parent insights call so the records always
match the chart.

Two entrypoints — :func:`drill_tenant` (GET /v1/dashboard/insights/drill) and
:func:`drill_admin` (GET /v1/admins/dashboard/insights/drill). Both return::

    { "columns": [...ordered camelCase keys...], "rows": [ {...}, ... ], "total": N }

``key`` semantics depend on the section:
  * time-series point  -> a bucket-start date label "YYYY-MM-DD" (the bucket
    span follows the range's auto-granularity; clamped to the applied range);
  * distribution slice -> the slice key (status / tier / type / …);
  * hourly bar         -> hour-of-day ("HH:00" or "0".."23");
  * top-list bar       -> the entity id (tenant / department / host).

Unsupported (section, key) combinations return an empty result with
``columns: []`` rather than erroring, so the panel renders cleanly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from bson import ObjectId

from core.database import db
from services.admin_insights_service import _plan_ids_for_tier
from services.dashboard_service import _shift_month
from services.insights_service import (
    _auto_granularity,
    _resolve_range,
    _resolve_scope,
    _visit_match,
)

_DAY = 86400


# ─── shared helpers ───────────────────────────────────────────────────


def _bucket_window(
    key: str, granularity: str, range_start: int, range_stop: int
) -> Optional[Tuple[int, int]]:
    """Resolve a time-series point ``key`` (a "YYYY-MM-DD" bucket start) to a
    [lo, hi) timestamp window, clamped to the applied range. Returns ``None``
    when ``key`` is not a parseable date (e.g. an "HH:00" hour label)."""
    try:
        dt = datetime.strptime(key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    lo = int(dt.timestamp())
    if granularity == "week":
        hi = lo + 7 * _DAY
    elif granularity == "month":
        hi = _shift_month(lo, 1)
    else:
        hi = lo + _DAY
    return max(lo, range_start), min(hi, range_stop + 1)


def _hour_of(key: str) -> Optional[int]:
    raw = key.split(":")[0] if ":" in key else key
    try:
        h = int(raw)
    except ValueError:
        return None
    return h if 0 <= h <= 23 else None


async def _paginate(
    collection: str,
    match: Dict[str, Any],
    sort_field: str,
    skip: int,
    limit: int,
    mapper: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    total = await db[collection].count_documents(match)
    cursor = db[collection].find(match).sort(sort_field, -1).skip(skip).limit(limit)
    rows = [mapper(doc) async for doc in cursor]
    return rows, total


def _empty() -> Dict[str, Any]:
    return {"columns": [], "rows": [], "total": 0}


async def _resolve_tenant_names(tenant_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Batch-resolve tenant company name + country for admin drill rows."""
    ids = [ObjectId(t) for t in set(tenant_ids) if t and ObjectId.is_valid(t)]
    out: Dict[str, Dict[str, Any]] = {}
    if not ids:
        return out
    async for doc in db["tenant_companies"].find({"_id": {"$in": ids}}):
        out[str(doc["_id"])] = {
            "companyName": doc.get("company_name", "Unknown"),
            "country": doc.get("country_of_hosting"),
        }
    return out


# ─── TENANT drill ─────────────────────────────────────────────────────


async def drill_tenant(
    *,
    tenant_id: str,
    caller_user_id: str,
    caller_role: str,
    section: str,
    key: str,
    start: Optional[int],
    stop: Optional[int],
    skip: int = 0,
    limit: int = 25,
    role_view: Optional[str] = None,
    department_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    host_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    operation_type: Optional[str] = None,
    resource_type: Optional[str] = None,
    incident_type: Optional[str] = None,
    incident_status: Optional[str] = None,
    severity: Optional[str] = None,
    dsr_type: Optional[str] = None,
    dsr_status: Optional[str] = None,
    lawful_basis: Optional[str] = None,
    status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    import time

    if not tenant_id:
        return _empty()
    now = int(time.time())
    created_at = await _tenant_created_at(tenant_id)
    eff_start, eff_stop = _resolve_range(created_at, start, stop, now=now)
    gran = _auto_granularity(eff_start, eff_stop, None)
    scope = await _resolve_scope(
        tenant_id=tenant_id,
        caller_user_id=caller_user_id,
        caller_role=caller_role,
        department_id=department_id,
        branch_id=branch_id,
    )
    scope.host_id = host_id

    # ── visit-session-backed sections ──
    # ``match`` is reused across the mutually-exclusive section branches below;
    # annotate once so it stays Dict[str, Any] for every branch.
    match: Dict[str, Any]
    if section in ("traffic", "hourly", "visitStatus", "feed", "topDepartments"):
        match = _visit_match(scope)
        if section == "traffic":
            window = _bucket_window(key, gran, eff_start, eff_stop)
            if window is None:
                return _empty()
            match["check_in_time"] = {"$gte": window[0], "$lt": window[1]}
        elif section == "hourly":
            hour = _hour_of(key)
            if hour is None:
                return _empty()
            match["check_in_time"] = {"$gte": eff_start, "$lte": eff_stop}
            match["$expr"] = {
                "$eq": [{"$hour": {"date": {"$toDate": {"$multiply": ["$check_in_time", 1000]}}, "timezone": "UTC"}}, hour]
            }
        elif section in ("visitStatus", "feed"):
            match["check_in_time"] = {"$gte": eff_start, "$lte": eff_stop}
            status_key = key or status_filter
            if status_key:
                match["status"] = status_key
        elif section == "topDepartments":
            match["check_in_time"] = {"$gte": eff_start, "$lte": eff_stop}
            # dept_admin: key is a host id; everyone else: a department id.
            match["host_id" if caller_role == "dept_admin" else "department_id"] = key

        def _vs_row(d: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "visitorName": d.get("visitor_name_snapshot"),
                "company": d.get("company_snapshot"),
                "department": d.get("department_name_snapshot"),
                "hostName": d.get("host_name_snapshot"),
                "status": d.get("status"),
                "checkInTime": d.get("check_in_time"),
            }

        rows, total = await _paginate("visit_sessions", match, "check_in_time", skip, limit, _vs_row)
        return {
            "columns": ["visitorName", "company", "department", "hostName", "status", "checkInTime"],
            "rows": rows,
            "total": total,
        }

    # ── incidents ──
    if section == "incident":
        match = {"tenant_id": tenant_id, "date_created": {"$gte": eff_start, "$lte": eff_stop}}
        # the slice key is a status (the tenant incident section groups by status)
        if key:
            match["status"] = key
        if incident_type:
            match["incident_type"] = incident_type
        if incident_status:
            match["status"] = incident_status
        if severity:
            match["risk_level"] = severity

        def _inc_row(d: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "title": d.get("description"),
                "type": d.get("incident_type"),
                "status": d.get("status"),
                "riskLevel": d.get("risk_level"),
                "notificationDeadline": d.get("notification_deadline"),
            }

        rows, total = await _paginate("incident_logs", match, "date_created", skip, limit, _inc_row)
        return {"columns": ["title", "type", "status", "riskLevel", "notificationDeadline"], "rows": rows, "total": total}

    # ── DSR ──
    if section == "dsr":
        match = {"tenant_id": tenant_id, "date_created": {"$gte": eff_start, "$lte": eff_stop}}
        if key:
            match["request_type"] = key
        if dsr_type:
            match["request_type"] = dsr_type
        if dsr_status:
            match["status"] = dsr_status
        if lawful_basis:
            match["lawful_basis"] = lawful_basis

        def _dsr_row(d: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "requestType": d.get("request_type"),
                "status": d.get("status"),
                "dateCreated": d.get("date_created"),
            }

        rows, total = await _paginate("data_subject_requests", match, "date_created", skip, limit, _dsr_row)
        return {"columns": ["requestType", "status", "dateCreated"], "rows": rows, "total": total}

    # ── appointments ──
    if section == "appointment":
        match = {"tenant_id": tenant_id, "scheduled_datetime": {"$gte": eff_start, "$lte": eff_stop}}
        if scope.department_ids is not None:
            match["department_id"] = {"$in": scope.department_ids}
        if key:
            match["status"] = key

        def _appt_row(d: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "visitorName": d.get("visitor_name_snapshot"),
                "hostName": d.get("host_name_snapshot"),
                "status": d.get("status"),
                "scheduledDatetime": d.get("scheduled_datetime"),
            }

        rows, total = await _paginate("expected_appointments", match, "scheduled_datetime", skip, limit, _appt_row)
        return {"columns": ["visitorName", "hostName", "status", "scheduledDatetime"], "rows": rows, "total": total}

    # ── audit ──
    if section == "audit":
        match = {"tenant_id": tenant_id}
        window = _bucket_window(key, gran, eff_start, eff_stop)
        if window is not None:
            match["timestamp"] = {"$gte": window[0], "$lt": window[1]}
        else:
            match["timestamp"] = {"$gte": eff_start, "$lte": eff_stop}
        if actor_id:
            match["actor_id"] = actor_id
        if resource_type:
            match["resource_type"] = resource_type
        if operation_type:
            from services.insights_service import _OP_TYPE_ACTION_REGEX

            rx = _OP_TYPE_ACTION_REGEX.get(operation_type)
            if rx:
                match["action"] = {"$regex": rx}

        def _audit_row(d: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "action": d.get("action"),
                "actorId": d.get("actor_id"),
                "resourceType": d.get("resource_type"),
                "timestamp": d.get("timestamp"),
            }

        rows, total = await _paginate("audit_trail", match, "timestamp", skip, limit, _audit_row)
        return {"columns": ["action", "actorId", "resourceType", "timestamp"], "rows": rows, "total": total}

    return _empty()


# ─── ADMIN drill ──────────────────────────────────────────────────────


async def drill_admin(
    *,
    section: str,
    key: str,
    start: Optional[int],
    stop: Optional[int],
    skip: int = 0,
    limit: int = 25,
    plan_tier: Optional[str] = None,
    subscription_status: Optional[str] = None,
    billing_cycle: Optional[str] = None,
    payment_provider: Optional[str] = None,
    country: Optional[str] = None,
    tenant_id: Optional[str] = None,
    incident_type: Optional[str] = None,
    incident_status: Optional[str] = None,
    support_status: Optional[str] = None,
    support_priority: Optional[str] = None,
    onboarding_status: Optional[str] = None,
) -> Dict[str, Any]:
    import time

    now = int(time.time())
    launch_at = await _platform_launch_at()
    eff_start, eff_stop = _resolve_range(launch_at, start, stop, now=now)
    gran = _auto_granularity(eff_start, eff_stop, None)

    # ── tenant signups (point = day) ──
    if section in ("tenantSignups", "recentSignups", "geography"):
        match: Dict[str, Any] = {}
        if section == "tenantSignups":
            window = _bucket_window(key, gran, eff_start, eff_stop)
            if window is None:
                return _empty()
            match["date_created"] = {"$gte": window[0], "$lt": window[1]}
        elif section == "geography":
            match["country_of_hosting"] = key
        if country:
            match["country_of_hosting"] = country
        docs, total = await _paginate_raw("tenant_companies", match, "date_created", skip, limit)
        plan_by_tenant = await _plan_names_for_tenants([str(d["_id"]) for d in docs])
        rows = [
            {
                "companyName": d.get("company_name", "Unknown"),
                "planName": plan_by_tenant.get(str(d["_id"]), {}).get("planName"),
                "country": d.get("country_of_hosting"),
                "signedUpAt": d.get("date_created"),
            }
            for d in docs
        ]
        return {"columns": ["companyName", "planName", "country", "signedUpAt"], "rows": rows, "total": total}

    # ── plan tier (slice = tier) → tenants on that tier ──
    if section in ("planTier", "billingCycle"):
        sub_match: Dict[str, Any] = {"status": {"$in": ["active", "trialing"]}}
        if section == "planTier":
            sub_match["plan_id"] = {"$in": await _plan_ids_for_tier(key)}
        else:  # billingCycle
            sub_match["billing_cycle"] = key
        if subscription_status:
            sub_match["status"] = subscription_status
        if billing_cycle and section != "billingCycle":
            sub_match["billing_cycle"] = billing_cycle
        docs, total = await _paginate_raw("subscriptions", sub_match, "date_created", skip, limit)
        names = await _resolve_tenant_names([str(d.get("tenant_id")) for d in docs])
        rows = [
            {
                "companyName": names.get(str(d.get("tenant_id")), {}).get("companyName", "Unknown"),
                "subscriptionStatus": d.get("status"),
                "country": names.get(str(d.get("tenant_id")), {}).get("country"),
                "monthlyRevenue": d.get("effective_price") if d.get("billing_cycle") == "monthly" else 0,
            }
            for d in docs
        ]
        return {"columns": ["companyName", "subscriptionStatus", "country", "monthlyRevenue"], "rows": rows, "total": total}

    # ── incidents (slice = status / type) ──
    if section in ("incidentStatus", "incidentType", "topIncidents"):
        inc_match: Dict[str, Any] = {"date_created": {"$gte": eff_start, "$lte": eff_stop}}
        if section == "incidentStatus":
            inc_match["status"] = key
        elif section == "incidentType":
            inc_match["incident_type"] = key
        elif section == "topIncidents":
            inc_match["tenant_id"] = key
        if incident_type and section != "incidentType":
            inc_match["incident_type"] = incident_type
        if incident_status and section != "incidentStatus":
            inc_match["status"] = incident_status
        if tenant_id and section != "topIncidents":
            inc_match["tenant_id"] = tenant_id
        docs, total = await _paginate_raw("incident_logs", inc_match, "date_created", skip, limit)
        names = await _resolve_tenant_names([str(d.get("tenant_id")) for d in docs])
        rows = [
            {
                "tenant": names.get(str(d.get("tenant_id")), {}).get("companyName", "Unknown"),
                "type": d.get("incident_type"),
                "severity": d.get("risk_level"),
                "status": d.get("status"),
                "deadline": d.get("notification_deadline"),
            }
            for d in docs
        ]
        return {"columns": ["tenant", "type", "severity", "status", "deadline"], "rows": rows, "total": total}

    # ── support (slice = status / priority) ──
    if section in ("supportStatus", "supportPriority", "topSupport"):
        match = {}
        if section == "supportStatus":
            match["status"] = key
        elif section == "supportPriority":
            match["priority"] = key
        elif section == "topSupport":
            match["tenant_id"] = key
        if support_status and section != "supportStatus":
            match["status"] = support_status
        if support_priority and section != "supportPriority":
            match["priority"] = support_priority
        docs, total = await _paginate_raw("support_cases", match, "date_created", skip, limit)
        names = await _resolve_tenant_names([str(d.get("tenant_id")) for d in docs])
        rows = [
            {
                "tenant": names.get(str(d.get("tenant_id")), {}).get("companyName", "Unknown"),
                "subject": d.get("subject") or d.get("title"),
                "priority": d.get("priority"),
                "status": d.get("status"),
                "category": d.get("category"),
            }
            for d in docs
        ]
        return {"columns": ["tenant", "subject", "priority", "status", "category"], "rows": rows, "total": total}

    # ── onboarding (slice = status) ──
    if section == "onboarding":
        match = {"date_created": {"$gte": eff_start, "$lte": eff_stop}}
        if key:
            match["status"] = key
        if onboarding_status:
            match["status"] = onboarding_status
        docs, total = await _paginate_raw("onboarding_submissions", match, "date_created", skip, limit)
        rows = [
            {
                "companyName": d.get("company_name") or d.get("company"),
                "status": d.get("status"),
                "submittedAt": d.get("date_created"),
            }
            for d in docs
        ]
        return {"columns": ["companyName", "status", "submittedAt"], "rows": rows, "total": total}

    # ── revenue / subscription / visitor time-series (point = day) ──
    if section in ("revenue", "newSubscriptions", "visitorCheckIns", "visitorSignups"):
        window = _bucket_window(key, gran, eff_start, eff_stop)
        if window is None:
            return _empty()
        lo, hi = window
        if section == "revenue":
            docs, total = await _paginate_raw("invoices", {"status": "paid", "paid_at": {"$gte": lo, "$lt": hi}}, "paid_at", skip, limit)
            names = await _resolve_tenant_names([str(d.get("tenant_id")) for d in docs])
            rows = [
                {
                    "tenant": names.get(str(d.get("tenant_id")), {}).get("companyName", "Unknown"),
                    "amountMinor": d.get("total_minor"),
                    "status": d.get("status"),
                    "paidAt": d.get("paid_at"),
                }
                for d in docs
            ]
            return {"columns": ["tenant", "amountMinor", "status", "paidAt"], "rows": rows, "total": total}
        if section == "newSubscriptions":
            docs, total = await _paginate_raw("subscriptions", {"date_created": {"$gte": lo, "$lt": hi}}, "date_created", skip, limit)
            names = await _resolve_tenant_names([str(d.get("tenant_id")) for d in docs])
            rows = [
                {
                    "tenant": names.get(str(d.get("tenant_id")), {}).get("companyName", "Unknown"),
                    "status": d.get("status"),
                    "billingCycle": d.get("billing_cycle"),
                    "createdAt": d.get("date_created"),
                }
                for d in docs
            ]
            return {"columns": ["tenant", "status", "billingCycle", "createdAt"], "rows": rows, "total": total}
        # visitorCheckIns / visitorSignups
        coll = "visit_sessions" if section == "visitorCheckIns" else "visitor_profiles"
        ts = "check_in_time" if section == "visitorCheckIns" else "date_created"
        base: Dict[str, Any] = {ts: {"$gte": lo, "$lt": hi}}
        if section == "visitorSignups":
            base["deleted_at"] = None
        docs, total = await _paginate_raw(coll, base, ts, skip, limit)
        names = await _resolve_tenant_names([str(d.get("tenant_id")) for d in docs])
        rows = [
            {
                "tenant": names.get(str(d.get("tenant_id")), {}).get("companyName", "Unknown"),
                "visitorName": d.get("visitor_name_snapshot") or d.get("full_name"),
                "status": d.get("status"),
                "timestamp": d.get(ts),
            }
            for d in docs
        ]
        return {"columns": ["tenant", "visitorName", "status", "timestamp"], "rows": rows, "total": total}

    # ── top tenants by visitors / activity (bar = tenant) ──
    if section in ("topVisitors", "topActivity"):
        match = {"check_in_time": {"$gte": eff_start, "$lte": eff_stop}, "tenant_id": key}
        docs, total = await _paginate_raw("visit_sessions", match, "check_in_time", skip, limit)
        rows = [
            {
                "visitorName": d.get("visitor_name_snapshot"),
                "company": d.get("company_snapshot"),
                "status": d.get("status"),
                "checkInTime": d.get("check_in_time"),
            }
            for d in docs
        ]
        return {"columns": ["visitorName", "company", "status", "checkInTime"], "rows": rows, "total": total}

    if section == "invoiceStatus":
        match = {"status": key, "date_created": {"$gte": eff_start, "$lte": eff_stop}}
        docs, total = await _paginate_raw("invoices", match, "date_created", skip, limit)
        names = await _resolve_tenant_names([str(d.get("tenant_id")) for d in docs])
        rows = [
            {
                "tenant": names.get(str(d.get("tenant_id")), {}).get("companyName", "Unknown"),
                "amountMinor": d.get("total_minor"),
                "status": d.get("status"),
                "createdAt": d.get("date_created"),
            }
            for d in docs
        ]
        return {"columns": ["tenant", "amountMinor", "status", "createdAt"], "rows": rows, "total": total}

    return _empty()


# ─── small shared loaders ─────────────────────────────────────────────


async def _paginate_raw(
    collection: str, match: Dict[str, Any], sort_field: str, skip: int, limit: int
) -> Tuple[List[Dict[str, Any]], int]:
    total = await db[collection].count_documents(match)
    cursor = db[collection].find(match).sort(sort_field, -1).skip(skip).limit(limit)
    docs = [d async for d in cursor]
    return docs, total


async def _plan_names_for_tenants(tenant_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """tenant_id -> { planName } via the tenant's subscription + plan."""
    out: Dict[str, Dict[str, Any]] = {}
    if not tenant_ids:
        return out
    async for sub in db["subscriptions"].find({"tenant_id": {"$in": list(set(tenant_ids))}}):
        tid = str(sub.get("tenant_id"))
        if tid in out:
            continue
        plan_name = None
        pid = sub.get("plan_id")
        if pid and ObjectId.is_valid(str(pid)):
            plan = await db["plans"].find_one({"_id": ObjectId(str(pid))})
            plan_name = (plan or {}).get("display_name")
        out[tid] = {"planName": plan_name}
    return out


async def _tenant_created_at(tenant_id: str) -> int:
    if not ObjectId.is_valid(tenant_id):
        return 0
    doc = await db["tenant_companies"].find_one(
        {"_id": ObjectId(tenant_id)}, projection={"date_created": 1}
    )
    return int(doc.get("date_created") or 0) if doc else 0


async def _platform_launch_at() -> int:
    async for doc in db["tenant_companies"].aggregate(
        [
            {"$match": {"date_created": {"$ne": None}}},
            {"$group": {"_id": None, "min": {"$min": "$date_created"}}},
        ]
    ):
        return int(doc.get("min") or 0)
    return 0


# Re-export for type hints elsewhere if needed.
__all__ = ["drill_tenant", "drill_admin"]
