"""Range-aware, role-scoped, plan-gated analytics for the tenant Insights page.

Backs ``GET /v1/dashboard/insights``. Unlike the legacy
``GET /v1/dashboard/stats`` (fixed 7/30-day windows, role-blind, plan-blind),
this endpoint:

* accepts a caller-chosen ``start`` / ``stop`` window (bounded below by the
  tenant's creation date — see :func:`_resolve_range`),
* auto-picks a bucket granularity from the window length and zero-fills gaps,
* computes only the sections / KPIs the requested role's tabs need,
* hard-scopes data per role (dept_admin -> own department, receptionist ->
  own branch) regardless of what the client asks for,
* gates content by plan: Free tenants get a deliberately minimal experience
  (Overview only, 7-day fixed window, no trends, two charts + a top-3 list).

The shape is documented end-to-end in ``stats.txt`` at the repo root.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from core.database import db
from core.errors import AppException, ErrorCode
from schemas.insights_schema import (
    AppliedFilter,
    HourlyBucket,
    InsightsMeta,
    InsightsResponse,
    InsightsSection,
    Kpi,
    RecentCheckIn,
    TimeSeriesPoint,
    TopItem,
    Trend,
)

# Reuse the labelling / formatting helpers already battle-tested on the
# legacy dashboard so the two payloads stay visually identical.
from services.dashboard_service import (
    _APPOINTMENT_STATUS_LABELS,
    _INCIDENT_STATUS_LABELS,
    _VISIT_STATUS_LABELS,
    _build_distribution,
    _build_top_items,
    _normalise_count_key,
    _resolve_id_labels,
    _shift_month,
    _start_of_day,
    _start_of_month_ts,
    _start_of_week_ts,
)

# ─── Section / role catalogue ─────────────────────────────────────────

# Every section id the Insights UI knows how to render. Used to validate
# role->section references and to compute the locked set.
ALL_SECTIONS = {
    "traffic",
    "audit",
    "hourly",
    "visitStatus",
    "incident",
    "dsr",
    "appointment",
    "newReturning",
    "topDepartments",
    "feed",
}

# Union of section ids referenced across a role's three tabs (Overview + 2).
# We only compute the sections in this set for the requested role.
_ROLE_SECTIONS: Dict[str, List[str]] = {
    "super_admin": [
        "traffic",
        "newReturning",
        "topDepartments",
        "hourly",
        "visitStatus",
        "incident",
        "dsr",
        "audit",
        "appointment",
    ],
    "dept_admin": [
        "traffic",
        "appointment",
        "topDepartments",
        "hourly",
        "visitStatus",
        "newReturning",
    ],
    "receptionist": [
        "traffic",
        "hourly",
        "visitStatus",
        "feed",
    ],
    "auditor": [
        "audit",
        "hourly",
        "visitStatus",
        "topDepartments",
        "incident",
    ],
    "security_officer": [
        "incident",
        "visitStatus",
        "traffic",
        "dsr",
        "audit",
        "hourly",
        "topDepartments",
    ],
    "dpo": [
        "dsr",
        "visitStatus",
        "traffic",
        "newReturning",
        "audit",
        "hourly",
        "topDepartments",
    ],
}

# Sections a Free tenant may see, regardless of role. Everything else this
# role's tabs reference is returned in ``meta.lockedSections`` with NO data.
_FREE_AVAILABLE_SECTIONS = {"traffic", "visitStatus", "topDepartments"}

# Free-tier ceilings (also advertised in the limitations payload as caps).
_FREE_RANGE_DAYS = 7
_FREE_TOP_LIST_MAX = 3
_PAID_TOP_LIST_MAX = 10

_DSR_TYPE_LABELS: Dict[str, str] = {
    "access": "Access",
    "correction": "Correction",
    "deletion": "Deletion",
    "consent_withdrawal": "Consent withdrawal",
    "portability": "Portability",
    "objection": "Objection",
}

# Maps the FE ``operation_type`` filter onto the past-tense verb suffix the
# audit ``action`` field uses (e.g. "appointment.created").
_OP_TYPE_ACTION_REGEX: Dict[str, str] = {
    "create": r"\.created$",
    "update": r"\.updated$",
    "delete": r"\.deleted$",
    "read": r"\.(read|viewed|listed|exported)$",
}

_DAY = 86400


# ─── Range / granularity ──────────────────────────────────────────────


def _resolve_range(
    tenant_created_at: int,
    start: Optional[int],
    stop: Optional[int],
    *,
    now: int,
) -> Tuple[int, int]:
    """Resolve and clamp the requested window.

    Defaults: ``stop`` -> now, ``start`` -> stop - 7d. ``start`` is clamped up
    to ``tenant_created_at`` (you cannot scroll back before the tenant
    existed). Raises 422 if ``stop`` < ``start`` after clamping."""
    effective_stop = stop if stop is not None else now
    effective_start = start if start is not None else effective_stop - 7 * _DAY
    if tenant_created_at and effective_start < tenant_created_at:
        effective_start = tenant_created_at
    if effective_stop < effective_start:
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="`stop` must be greater than or equal to `start`.",
        )
    return effective_start, effective_stop


def _auto_granularity(start: int, stop: int, override: Optional[str]) -> str:
    if override in ("hour", "day", "week", "month"):
        return override
    window = stop - start
    if window <= 48 * 3600:
        return "hour"
    if window <= 31 * _DAY:
        return "day"
    if window <= 180 * _DAY:
        return "week"
    return "month"


def _bucket_boundaries(
    start: int, stop: int, granularity: str
) -> List[Tuple[int, str]]:
    """Contiguous, zero-fillable bucket starts + labels covering [start, stop]."""
    out: List[Tuple[int, str]] = []
    if granularity == "hour":
        cursor = start - (start % 3600)
        while cursor <= stop:
            label = datetime.fromtimestamp(cursor, tz=timezone.utc).strftime("%H:00")
            out.append((cursor, label))
            cursor += 3600
    elif granularity == "week":
        cursor = _start_of_week_ts(start)
        while cursor <= stop:
            label = datetime.fromtimestamp(cursor, tz=timezone.utc).strftime("%Y-%m-%d")
            out.append((cursor, label))
            cursor += 7 * _DAY
    elif granularity == "month":
        cursor = _start_of_month_ts(start)
        while cursor <= stop:
            label = datetime.fromtimestamp(cursor, tz=timezone.utc).strftime("%Y-%m-%d")
            out.append((cursor, label))
            cursor = _shift_month(cursor, 1)
    else:  # day
        cursor = _start_of_day(start)
        while cursor <= stop:
            label = datetime.fromtimestamp(cursor, tz=timezone.utc).strftime("%Y-%m-%d")
            out.append((cursor, label))
            cursor += _DAY
    return out


def _fold_counts(
    raw: Dict[int, int], boundaries: List[Tuple[int, str]]
) -> List[TimeSeriesPoint]:
    """Assign each ``{bucket_unit_start: count}`` to its enclosing boundary."""
    starts = [b[0] for b in boundaries]
    points = [
        TimeSeriesPoint(timestamp=ts, label=lbl, value=0) for ts, lbl in boundaries
    ]
    for unit_start, count in raw.items():
        # last boundary whose start <= unit_start
        idx = -1
        for i, b_start in enumerate(starts):
            if b_start <= unit_start:
                idx = i
            else:
                break
        if 0 <= idx < len(points):
            points[idx].value += count
    return points


# ─── Mongo count helpers ──────────────────────────────────────────────


async def _raw_unit_counts(
    collection: str,
    match: Dict[str, Any],
    ts_field: str,
    *,
    start: int,
    stop: int,
    granularity: str,
) -> Dict[int, int]:
    """Per-unit counts keyed by unit-start ts. ``hour`` units when the chosen
    granularity is hourly, otherwise daily units (folded into week/month
    buckets in Python)."""
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
                "count": {"$sum": 1},
            }
        },
    ]
    out: Dict[int, int] = {}
    async for doc in db[collection].aggregate(pipeline):
        key = str(doc.get("_id") or "")
        if not key:
            continue
        try:
            dt = datetime.strptime(key, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        out[int(dt.timestamp())] = out.get(int(dt.timestamp()), 0) + int(
            doc.get("count", 0)
        )
    return out


async def _group_count_range(
    collection: str,
    match: Dict[str, Any],
    field: str,
) -> Dict[str, int]:
    pipeline = [
        {"$match": match},
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
    ]
    out: Dict[str, int] = {}
    async for doc in db[collection].aggregate(pipeline):
        key = _normalise_count_key(doc.get("_id"))
        out[key] = out.get(key, 0) + int(doc.get("count", 0))
    return out


# ─── Scope ────────────────────────────────────────────────────────────


class _Scope:
    """Resolved data-access scope for the request.

    ``department_ids`` is ``None`` for tenant-wide scope, or a concrete list
    when restricted to a department (dept_admin) or a branch's departments
    (receptionist / branch filter). ``branch_id`` is carried so checkins —
    which store ``tenant_specific_data.branch_id`` directly — can be filtered
    without a department round-trip."""

    def __init__(
        self,
        tenant_id: str,
        department_ids: Optional[List[str]],
        branch_id: Optional[str],
        department_id: Optional[str],
        host_id: Optional[str] = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.department_ids = department_ids
        self.branch_id = branch_id
        self.department_id = department_id
        self.host_id = host_id


async def _dept_ids_for_branch(tenant_id: str, branch_id: str) -> List[str]:
    cursor = db["departments"].find(
        {"tenant_id": tenant_id, "branch_id": branch_id}, projection={"_id": 1}
    )
    return [str(doc["_id"]) async for doc in cursor]


async def _resolve_scope(
    *,
    tenant_id: str,
    caller_user_id: str,
    caller_role: str,
    department_id: Optional[str],
    branch_id: Optional[str],
) -> _Scope:
    """Apply per-role hard scoping. Client-supplied ``department_id`` /
    ``branch_id`` are only honoured for ``super_admin``; every other role is
    pinned to its own department / branch so cross-scope data cannot leak."""
    if caller_role == "dept_admin":
        own_dept = await _own_department_id(tenant_id, caller_user_id)
        dept_ids = [own_dept] if own_dept else []
        return _Scope(tenant_id, dept_ids, None, own_dept)

    if caller_role == "receptionist":
        own_branch = await _own_branch_id(tenant_id, caller_user_id)
        if own_branch:
            dept_ids = await _dept_ids_for_branch(tenant_id, own_branch)
            return _Scope(tenant_id, dept_ids, own_branch, None)
        return _Scope(tenant_id, None, None, None)

    if caller_role == "super_admin":
        if branch_id:
            dept_ids = await _dept_ids_for_branch(tenant_id, branch_id)
            return _Scope(tenant_id, dept_ids, branch_id, department_id)
        if department_id:
            return _Scope(tenant_id, [department_id], None, department_id)
        return _Scope(tenant_id, None, None, None)

    # auditor / security_officer / dpo: tenant-wide, read-only.
    return _Scope(tenant_id, None, None, None)


async def _own_department_id(tenant_id: str, user_id: str) -> Optional[str]:
    doc = await _system_user_doc(tenant_id, user_id)
    return str(doc["department_id"]) if doc and doc.get("department_id") else None


async def _own_branch_id(tenant_id: str, user_id: str) -> Optional[str]:
    doc = await _system_user_doc(tenant_id, user_id)
    if doc and doc.get("branch_ids"):
        ids = doc["branch_ids"]
        return str(ids[0]) if ids else None
    return None


async def _system_user_doc(tenant_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    if not ObjectId.is_valid(user_id):
        return None
    return await db["system_users"].find_one(
        {"_id": ObjectId(user_id), "tenant_id": tenant_id},
        projection={"department_id": 1, "branch_ids": 1},
    )


def _visit_match(
    scope: _Scope, *, start: Optional[int] = None, stop: Optional[int] = None
) -> Dict[str, Any]:
    match: Dict[str, Any] = {"tenant_id": scope.tenant_id}
    if scope.department_ids is not None:
        match["department_id"] = {"$in": scope.department_ids}
    if scope.host_id:
        match["host_id"] = scope.host_id
    if start is not None or stop is not None:
        rng: Dict[str, Any] = {}
        if start is not None:
            rng["$gte"] = start
        if stop is not None:
            rng["$lte"] = stop
        match["check_in_time"] = rng
    return match


def _checkin_match(
    scope: _Scope, *, start: Optional[int] = None, stop: Optional[int] = None
) -> Dict[str, Any]:
    match: Dict[str, Any] = {"tenant_id": scope.tenant_id}
    if scope.branch_id:
        match["tenant_specific_data.branch_id"] = scope.branch_id
    elif scope.department_ids is not None:
        match["tenant_specific_data.department_id"] = {"$in": scope.department_ids}
    if start is not None or stop is not None:
        rng: Dict[str, Any] = {}
        if start is not None:
            rng["$gte"] = start
        if stop is not None:
            rng["$lte"] = stop
        match["date_created"] = rng
    return match


# ─── Trend ────────────────────────────────────────────────────────────


def _trend(current: float, previous: float, *, good_when_up: bool = True) -> Trend:
    change = current - previous
    if previous == 0:
        pct = 100.0 if current > 0 else 0.0
    else:
        pct = round((change / previous) * 100, 1)
    if change > 0:
        direction = "up"
    elif change < 0:
        direction = "down"
    else:
        direction = "flat"
    is_good = direction == "flat" or (direction == "up") == good_when_up
    return Trend(change_percent=pct, direction=direction, is_good=is_good)


async def _count_visits(scope: _Scope, start: int, stop: int) -> int:
    sessions, checkins = await asyncio.gather(
        db["visit_sessions"].count_documents(
            _visit_match(scope, start=start, stop=stop)
        ),
        db["checkins"].count_documents(_checkin_match(scope, start=start, stop=stop)),
    )
    return int(sessions) + int(checkins)


# ─── Section builders (only invoked when the section is available) ────


async def _section_traffic(
    scope: _Scope, start: int, stop: int, granularity: str
) -> InsightsSection:
    boundaries = _bucket_boundaries(start, stop, granularity)
    session_raw, checkin_raw = await asyncio.gather(
        _raw_unit_counts(
            "visit_sessions",
            _visit_match(scope),
            "check_in_time",
            start=start,
            stop=stop,
            granularity=granularity,
        ),
        _raw_unit_counts(
            "checkins",
            _checkin_match(scope),
            "date_created",
            start=start,
            stop=stop,
            granularity=granularity,
        ),
    )
    merged: Dict[int, int] = dict(session_raw)
    for ts, count in checkin_raw.items():
        merged[ts] = merged.get(ts, 0) + count
    return InsightsSection(
        type="timeSeries",
        title="Visit traffic",
        points=_fold_counts(merged, boundaries),
        value_label="Check-ins",
    )


async def _section_audit(
    scope: _Scope,
    start: int,
    stop: int,
    granularity: str,
    *,
    actor_id: Optional[str],
    operation_type: Optional[str],
    resource_type: Optional[str],
) -> InsightsSection:
    boundaries = _bucket_boundaries(start, stop, granularity)
    match: Dict[str, Any] = {"tenant_id": scope.tenant_id}
    if actor_id:
        match["actor_id"] = actor_id
    if resource_type:
        match["resource_type"] = resource_type
    if operation_type and operation_type in _OP_TYPE_ACTION_REGEX:
        match["action"] = {"$regex": _OP_TYPE_ACTION_REGEX[operation_type]}
    raw = await _raw_unit_counts(
        "audit_trail",
        match,
        "timestamp",
        start=start,
        stop=stop,
        granularity=granularity,
    )
    return InsightsSection(
        type="timeSeries",
        title="Audit activity",
        points=_fold_counts(raw, boundaries),
        value_label="Events",
    )


async def _section_hourly(scope: _Scope, start: int, stop: int) -> InsightsSection:
    """Check-ins by hour-of-day (0..23), aggregated over the whole range."""
    counts: Dict[int, int] = {}

    async def _add(collection: str, match: Dict[str, Any], ts_field: str) -> None:
        pipeline = [
            {"$match": {**match, ts_field: {"$gte": start, "$lte": stop}}},
            {
                "$group": {
                    "_id": {
                        "$hour": {
                            "date": {"$toDate": {"$multiply": [f"${ts_field}", 1000]}},
                            "timezone": "UTC",
                        }
                    },
                    "count": {"$sum": 1},
                }
            },
        ]
        async for doc in db[collection].aggregate(pipeline):
            hour = int(doc.get("_id") or 0)
            counts[hour] = counts.get(hour, 0) + int(doc.get("count", 0))

    await _add("visit_sessions", _visit_match(scope), "check_in_time")
    await _add("checkins", _checkin_match(scope), "date_created")
    buckets = [
        HourlyBucket(hour=h, label=f"{h:02d}:00", value=counts.get(h, 0))
        for h in range(24)
    ]
    return InsightsSection(type="hourly", title="Check-ins by hour", buckets=buckets)


async def _section_visit_status(
    scope: _Scope, start: int, stop: int
) -> InsightsSection:
    session_counts, checkin_counts = await asyncio.gather(
        _group_count_range(
            "visit_sessions", _visit_match(scope, start=start, stop=stop), "status"
        ),
        _group_count_range(
            "checkins", _checkin_match(scope, start=start, stop=stop), "state"
        ),
    )
    merged: Dict[str, int] = dict(session_counts)
    for key, value in checkin_counts.items():
        merged[key] = merged.get(key, 0) + value
    return InsightsSection(
        type="distribution",
        title="Visit status",
        slices=_build_distribution(merged, _VISIT_STATUS_LABELS),
    )


async def _section_incident(
    scope: _Scope,
    start: int,
    stop: int,
    *,
    incident_type: Optional[str],
    incident_status: Optional[str],
    severity: Optional[str],
    now: int,
) -> InsightsSection:
    match: Dict[str, Any] = {
        "tenant_id": scope.tenant_id,
        "date_created": {"$gte": start, "$lte": stop},
    }
    if incident_type:
        match["incident_type"] = incident_type
    if incident_status:
        match["status"] = incident_status
    if severity:
        match["risk_level"] = severity
    counts = await _group_count_range("incident_logs", match, "status")
    past_deadline, approaching = await asyncio.gather(
        db["incident_logs"].count_documents(
            {
                "tenant_id": scope.tenant_id,
                "ndpc_notified": {"$ne": True},
                "notification_deadline": {"$lt": now},
                "status": {"$nin": ["closed", "reported_to_ndpc"]},
            }
        ),
        db["incident_logs"].count_documents(
            {
                "tenant_id": scope.tenant_id,
                "ndpc_notified": {"$ne": True},
                "notification_deadline": {"$gte": now, "$lte": now + _DAY},
                "status": {"$nin": ["closed", "reported_to_ndpc"]},
            }
        ),
    )
    return InsightsSection(
        type="distribution",
        title="Incidents by status",
        slices=_build_distribution(counts, _INCIDENT_STATUS_LABELS),
        meta={
            "pastDeadline": int(past_deadline),
            "approachingDeadline": int(approaching),
        },
    )


async def _section_dsr(
    scope: _Scope,
    start: int,
    stop: int,
    *,
    dsr_type: Optional[str],
    dsr_status: Optional[str],
    lawful_basis: Optional[str],
) -> InsightsSection:
    match: Dict[str, Any] = {
        "tenant_id": scope.tenant_id,
        "date_created": {"$gte": start, "$lte": stop},
    }
    if dsr_type:
        match["request_type"] = dsr_type
    if dsr_status:
        match["status"] = dsr_status
    if lawful_basis:
        match["lawful_basis"] = lawful_basis
    counts = await _group_count_range("data_subject_requests", match, "request_type")
    return InsightsSection(
        type="distribution",
        title="Data-subject requests",
        slices=_build_distribution(counts, _DSR_TYPE_LABELS),
    )


async def _section_appointment(scope: _Scope, start: int, stop: int) -> InsightsSection:
    match: Dict[str, Any] = {
        "tenant_id": scope.tenant_id,
        "scheduled_datetime": {"$gte": start, "$lte": stop},
    }
    if scope.department_ids is not None:
        match["department_id"] = {"$in": scope.department_ids}
    counts = await _group_count_range("expected_appointments", match, "status")
    return InsightsSection(
        type="distribution",
        title="Appointments by status",
        slices=_build_distribution(counts, _APPOINTMENT_STATUS_LABELS),
    )


async def _section_new_returning(
    scope: _Scope, start: int, stop: int
) -> InsightsSection:
    in_range = _visit_match(scope, start=start, stop=stop)
    visitor_ids: List[str] = []
    async for doc in db["visit_sessions"].aggregate(
        [{"$match": in_range}, {"$group": {"_id": "$visitor_profile_id"}}]
    ):
        if doc.get("_id"):
            visitor_ids.append(str(doc["_id"]))

    new_count = 0
    returning_count = 0
    if visitor_ids:
        all_time = _visit_match(scope)
        async for doc in db["visit_sessions"].aggregate(
            [
                {"$match": {**all_time, "visitor_profile_id": {"$in": visitor_ids}}},
                {
                    "$group": {
                        "_id": "$visitor_profile_id",
                        "first": {"$min": "$check_in_time"},
                    }
                },
            ]
        ):
            if (doc.get("first") or 0) >= start:
                new_count += 1
            else:
                returning_count += 1
    slices = _build_distribution(
        {"new": new_count, "returning": returning_count},
        {"new": "First-time visitors", "returning": "Returning visitors"},
    )
    return InsightsSection(type="distribution", title="New vs returning", slices=slices)


async def _section_top_departments(
    scope: _Scope, start: int, stop: int, *, limit: int, by_host: bool
) -> InsightsSection:
    """Top departments by visit count in range. For dept_admin (``by_host``)
    this degenerates to top hosts within the department."""
    field = "host_id" if by_host else "department_id"
    pipeline = [
        {
            "$match": {
                **_visit_match(scope, start=start, stop=stop),
                field: {"$ne": None},
            }
        },
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    counts: Dict[str, int] = {}
    async for doc in db["visit_sessions"].aggregate(pipeline):
        key = doc.get("_id")
        if key:
            counts[str(key)] = int(doc.get("count", 0))

    if by_host:
        label_map: Dict[str, str] = {}
        ids = [ObjectId(k) for k in counts if ObjectId.is_valid(k)]
        if ids:
            async for d in db["system_users"].find({"_id": {"$in": ids}}):
                label_map[str(d["_id"])] = (
                    d.get("full_name") or d.get("email") or "Unknown host"
                )
        items: List[TopItem] = _build_top_items(
            counts, label_map=label_map, limit=limit
        )
        title = "Top hosts"
    else:
        # Fold in the check-in collection. Kiosk / receptionist submissions
        # are stored as ``checkins`` (with the department under
        # ``tenant_specific_data.department_id``), NOT ``visit_sessions``, so
        # counting only the latter silently drops every visit that came
        # through the check-in flow — which is most of them. Every other
        # Insights section sums both collections; this one must too.
        ci_field = "tenant_specific_data.department_id"
        ci_pipeline = [
            {
                "$match": {
                    **_checkin_match(scope, start=start, stop=stop),
                    ci_field: {"$nin": [None, ""]},
                }
            },
            {"$group": {"_id": f"${ci_field}", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit},
        ]
        async for doc in db["checkins"].aggregate(ci_pipeline):
            key = doc.get("_id")
            if key:
                counts[str(key)] = counts.get(str(key), 0) + int(doc.get("count", 0))

        label_map, extras = await _resolve_id_labels(
            "departments", list(counts.keys()), "name"
        )
        items = _build_top_items(
            counts, label_map=label_map, extra_map=extras, limit=limit
        )
        title = "Top departments"
    return InsightsSection(type="topList", title=title, items=items)


async def _section_feed(
    scope: _Scope, *, status_filter: Optional[str]
) -> InsightsSection:
    match = _visit_match(scope)
    if status_filter:
        match["status"] = status_filter
    events: List[RecentCheckIn] = []
    cursor = db["visit_sessions"].find(match).sort("check_in_time", -1).limit(10)
    async for s in cursor:
        ci = s.get("check_in_time")
        co = s.get("check_out_time")
        duration = max(int((co - ci) / 60), 0) if ci and co else None
        events.append(
            RecentCheckIn(
                id=str(s.get("_id") or ""),
                visitor_name=s.get("visitor_name_snapshot"),
                company=s.get("company_snapshot"),
                department_name=s.get("department_name_snapshot"),
                host_name=s.get("host_name_snapshot"),
                purpose=s.get("purpose"),
                status=_normalise_count_key(s.get("status"))
                if s.get("status")
                else None,
                check_in_time=ci,
                check_out_time=co,
                duration_minutes=duration,
                verified=_normalise_count_key(s.get("verification_status"))
                == "verified",
            )
        )
    return InsightsSection(type="feed", title="Recent check-ins", events=events)


# ─── KPI builders ─────────────────────────────────────────────────────


async def _live_active(scope: _Scope) -> int:
    checked_in, approved = await asyncio.gather(
        db["visit_sessions"].count_documents(
            {**_visit_match(scope), "status": "checked_in"}
        ),
        db["checkins"].count_documents({**_checkin_match(scope), "state": "approved"}),
    )
    return int(checked_in) + int(approved)


async def _verification_rate(scope: _Scope, start: int, stop: int) -> float:
    total, verified = await asyncio.gather(
        db["visit_sessions"].count_documents(
            _visit_match(scope, start=start, stop=stop)
        ),
        db["visit_sessions"].count_documents(
            {
                **_visit_match(scope, start=start, stop=stop),
                "verification_status": "verified",
            }
        ),
    )
    return round((verified / total) * 100, 1) if total else 0.0


async def _open_incidents(scope: _Scope) -> int:
    return int(
        await db["incident_logs"].count_documents(
            {
                "tenant_id": scope.tenant_id,
                "status": {"$nin": ["closed", "reported_to_ndpc"]},
            }
        )
    )


async def _critical_incidents(scope: _Scope) -> int:
    return int(
        await db["incident_logs"].count_documents(
            {
                "tenant_id": scope.tenant_id,
                "risk_level": "critical",
                "status": {"$nin": ["closed"]},
            }
        )
    )


async def _build_kpis(
    role: str,
    scope: _Scope,
    start: int,
    stop: int,
    *,
    trends_enabled: bool,
    now: int,
    audit_match_base: Dict[str, Any],
) -> List[Kpi]:
    span = stop - start
    prev_start, prev_stop = start - span, start

    def _maybe_trend(
        cur: float, prev: float, *, good_when_up: bool = True
    ) -> Optional[Trend]:
        return _trend(cur, prev, good_when_up=good_when_up) if trends_enabled else None

    if role == "super_admin":
        visits, prev_visits, active, vrate, open_inc, crit = await asyncio.gather(
            _count_visits(scope, start, stop),
            _count_visits(scope, prev_start, prev_stop) if trends_enabled else _zero(),
            _live_active(scope),
            _verification_rate(scope, start, stop),
            _open_incidents(scope),
            _critical_incidents(scope),
        )
        return [
            Kpi(
                key="totalVisits",
                label="Total visits",
                value=visits,
                trend=_maybe_trend(visits, prev_visits),
                description="Visits in the selected range",
            ),
            Kpi(
                key="currentlyActive",
                label="Currently active",
                value=active,
                description="Visitors on-site right now",
            ),
            Kpi(
                key="verificationRate",
                label="Verification rate",
                value=vrate,
                unit="%",
                trend=None,
                description="Verified visits in range",
            ),
            Kpi(
                key="openIncidents",
                label="Open incidents",
                value=open_inc,
                description=f"{crit} critical",
            ),
        ]

    if role == "dept_admin":
        visits, prev_visits, appts, prev_appts, noshow, hosts = await asyncio.gather(
            _count_visits(scope, start, stop),
            _count_visits(scope, prev_start, prev_stop) if trends_enabled else _zero(),
            _appt_count(scope, start, stop),
            _appt_count(scope, prev_start, prev_stop) if trends_enabled else _zero(),
            _no_show_rate(scope, start, stop),
            _active_hosts(scope, start, stop),
        )
        return [
            Kpi(
                key="deptVisitors",
                label="Department visitors",
                value=visits,
                trend=_maybe_trend(visits, prev_visits),
                description="Visits in range",
            ),
            Kpi(
                key="appointmentsInRange",
                label="Appointments",
                value=appts,
                trend=_maybe_trend(appts, prev_appts),
                description="Scheduled in range",
            ),
            Kpi(
                key="noShowRate",
                label="No-show rate",
                value=noshow,
                unit="%",
                description="No-shows + cancellations",
            ),
            Kpi(
                key="activeHosts",
                label="Active hosts",
                value=hosts,
                description="Distinct hosts in range",
            ),
        ]

    if role == "receptionist":
        start_today = _start_of_day(now)
        today, awaiting, wait, badges = await asyncio.gather(
            _count_visits(scope, start_today, now),
            _live_active(scope),
            _avg_wait_minutes(scope, start, stop),
            _badges_printed(scope, start, stop),
        )
        return [
            Kpi(
                key="checkedInToday",
                label="Checked in today",
                value=today,
                description="Check-ins since midnight UTC",
            ),
            Kpi(
                key="awaitingCheckout",
                label="Awaiting checkout",
                value=awaiting,
                description="Still on-site",
            ),
            Kpi(
                key="avgWaitMinutes",
                label="Avg wait",
                value=wait,
                unit="min",
                description="Arrival to approval, in range",
            ),
            Kpi(
                key="badgesPrinted",
                label="Badges printed",
                value=badges,
                trend=_maybe_trend(badges, 0),
                description="Badges issued in range",
            ),
        ]

    if role == "auditor":
        start_today = _start_of_day(now)
        today, in_range, prev_range, actors, exports = await asyncio.gather(
            _audit_count(audit_match_base, start_today, now),
            _audit_count(audit_match_base, start, stop),
            _audit_count(audit_match_base, prev_start, prev_stop)
            if trends_enabled
            else _zero(),
            _unique_actors(audit_match_base, start, stop),
            _export_events(scope, start, stop),
        )
        return [
            Kpi(
                key="auditEventsToday",
                label="Audit events today",
                value=today,
                description="Since midnight UTC",
            ),
            Kpi(
                key="auditEventsInRange",
                label="Audit events",
                value=in_range,
                trend=_maybe_trend(in_range, prev_range),
                description="In the selected range",
            ),
            Kpi(
                key="uniqueActors",
                label="Unique actors",
                value=actors,
                description="Distinct actors in range",
            ),
            Kpi(
                key="exportsInRange",
                label="Exports",
                value=exports,
                description="Compliance/audit exports in range",
            ),
        ]

    if role == "security_officer":
        open_inc, crit, approaching, resolved = await asyncio.gather(
            _open_incidents(scope),
            _critical_incidents(scope),
            _approaching_deadline(scope, now),
            _resolved_incidents(scope, start, stop),
        )
        return [
            Kpi(
                key="openIncidents",
                label="Open incidents",
                value=open_inc,
                description="Not yet closed",
            ),
            Kpi(
                key="criticalIncidents",
                label="Critical incidents",
                value=crit,
                description="Critical risk, open",
            ),
            Kpi(
                key="approachingDeadline",
                label="Approaching deadline",
                value=approaching,
                description="NDPC 72h within 24h",
            ),
            Kpi(
                key="resolvedInRange",
                label="Resolved",
                value=resolved,
                trend=_maybe_trend(resolved, 0),
                description="Closed in range",
            ),
        ]

    if role == "dpo":
        open_dsr, consent, withdrawals, policies, processors = await asyncio.gather(
            _open_dsr(scope),
            _consent_rate(scope, start, stop),
            _consent_withdrawals(scope, start, stop),
            db["retention_policies"].count_documents({"tenant_id": scope.tenant_id}),
            db["sub_processors"].count_documents({"tenant_id": scope.tenant_id}),
        )
        return [
            Kpi(
                key="openDsr",
                label="Open DSRs",
                value=open_dsr,
                description="Pending data-subject requests",
            ),
            Kpi(
                key="consentRate",
                label="Consent rate",
                value=consent,
                unit="%",
                trend=None,
                description=f"{withdrawals} withdrawals in range",
            ),
            Kpi(
                key="retentionPolicies",
                label="Retention policies",
                value=int(policies),
                description="Active policies",
            ),
            Kpi(
                key="subProcessors",
                label="Sub-processors",
                value=int(processors),
                description="Registered processors",
            ),
        ]

    return []


async def _zero() -> int:
    return 0


async def _appt_count(scope: _Scope, start: int, stop: int) -> int:
    match: Dict[str, Any] = {
        "tenant_id": scope.tenant_id,
        "scheduled_datetime": {"$gte": start, "$lte": stop},
    }
    if scope.department_ids is not None:
        match["department_id"] = {"$in": scope.department_ids}
    return int(await db["expected_appointments"].count_documents(match))


async def _no_show_rate(scope: _Scope, start: int, stop: int) -> float:
    match: Dict[str, Any] = {
        "tenant_id": scope.tenant_id,
        "scheduled_datetime": {"$gte": start, "$lte": stop},
    }
    if scope.department_ids is not None:
        match["department_id"] = {"$in": scope.department_ids}
    counts = await _group_count_range("expected_appointments", match, "status")
    total = sum(counts.values())
    bad = (
        counts.get("no_show", 0) + counts.get("missed", 0) + counts.get("cancelled", 0)
    )
    return round((bad / total) * 100, 1) if total else 0.0


async def _active_hosts(scope: _Scope, start: int, stop: int) -> int:
    ids = await db["visit_sessions"].distinct(
        "host_id",
        {**_visit_match(scope, start=start, stop=stop), "host_id": {"$ne": None}},
    )
    return len(ids)


async def _avg_wait_minutes(scope: _Scope, start: int, stop: int) -> float:
    pipeline = [
        {
            "$match": {
                **_checkin_match(scope, start=start, stop=stop),
                "approved_at": {"$ne": None},
            }
        },
        {
            "$group": {
                "_id": None,
                "avg": {"$avg": {"$subtract": ["$approved_at", "$date_created"]}},
            }
        },
    ]
    async for doc in db["checkins"].aggregate(pipeline):
        avg = doc.get("avg") or 0
        return round(avg / 60.0, 1) if avg else 0.0
    return 0.0


async def _badges_printed(scope: _Scope, start: int, stop: int) -> int:
    return int(
        await db["visit_sessions"].count_documents(
            {
                **_visit_match(scope, start=start, stop=stop),
                "badge_qr_token": {"$ne": None},
            }
        )
    )


async def _audit_count(base: Dict[str, Any], start: int, stop: int) -> int:
    return int(
        await db["audit_trail"].count_documents(
            {**base, "timestamp": {"$gte": start, "$lte": stop}}
        )
    )


async def _unique_actors(base: Dict[str, Any], start: int, stop: int) -> int:
    ids = await db["audit_trail"].distinct(
        "actor_id", {**base, "timestamp": {"$gte": start, "$lte": stop}}
    )
    return len(ids)


async def _export_events(scope: _Scope, start: int, stop: int) -> int:
    return int(
        await db["audit_trail"].count_documents(
            {
                "tenant_id": scope.tenant_id,
                "timestamp": {"$gte": start, "$lte": stop},
                "action": {"$regex": "export"},
            }
        )
    )


async def _approaching_deadline(scope: _Scope, now: int) -> int:
    return int(
        await db["incident_logs"].count_documents(
            {
                "tenant_id": scope.tenant_id,
                "ndpc_notified": {"$ne": True},
                "notification_deadline": {"$gte": now, "$lte": now + _DAY},
                "status": {"$nin": ["closed", "reported_to_ndpc"]},
            }
        )
    )


async def _resolved_incidents(scope: _Scope, start: int, stop: int) -> int:
    return int(
        await db["incident_logs"].count_documents(
            {
                "tenant_id": scope.tenant_id,
                "status": {"$in": ["closed", "reported_to_ndpc"]},
                "date_created": {"$gte": start, "$lte": stop},
            }
        )
    )


async def _open_dsr(scope: _Scope) -> int:
    return int(
        await db["data_subject_requests"].count_documents(
            {
                "tenant_id": scope.tenant_id,
                "status": {"$nin": ["completed", "rejected"]},
            }
        )
    )


async def _consent_rate(scope: _Scope, start: int, stop: int) -> float:
    match = _visit_match(scope, start=start, stop=stop)
    granted, total = await asyncio.gather(
        db["visit_sessions"].count_documents({**match, "consent_granted": True}),
        db["visit_sessions"].count_documents(match),
    )
    return round((granted / total) * 100, 1) if total else 0.0


async def _consent_withdrawals(scope: _Scope, start: int, stop: int) -> int:
    return int(
        await db["visit_sessions"].count_documents(
            {
                **_visit_match(scope),
                "consent_withdrawal_at": {"$gte": start, "$lte": stop},
            }
        )
    )


# ─── Plan / tier ──────────────────────────────────────────────────────


async def _resolve_tier(tenant_id: str) -> str:
    try:
        from services.plan_cache_service import resolve_tenant_plan

        resolved = await resolve_tenant_plan(tenant_id)
        if resolved and resolved.get("tier"):
            return str(resolved["tier"]).lower()
    except Exception:
        pass
    return "free"


async def _tenant_created_at(tenant_id: str) -> int:
    if not ObjectId.is_valid(tenant_id):
        return 0
    doc = await db["tenant_companies"].find_one(
        {"_id": ObjectId(tenant_id)}, projection={"date_created": 1}
    )
    return int(doc.get("date_created") or 0) if doc else 0


async def _earliest_data(tenant_id: str, fallback: int) -> int:
    async def _min(collection: str, field: str) -> Optional[int]:
        async for doc in db[collection].aggregate(
            [
                {"$match": {"tenant_id": tenant_id, field: {"$ne": None}}},
                {"$group": {"_id": None, "min": {"$min": f"${field}"}}},
            ]
        ):
            return int(doc.get("min")) if doc.get("min") else None
        return None

    sess_min, checkin_min = await asyncio.gather(
        _min("visit_sessions", "check_in_time"),
        _min("checkins", "date_created"),
    )
    candidates = [v for v in (sess_min, checkin_min) if v]
    if not candidates:
        return fallback
    return max(min(candidates), fallback)


# ─── Applied-filter chips ─────────────────────────────────────────────


def _humanise(value: Any) -> str:
    """`checked_in` -> `Checked in`, `consent_withdrawal` -> `Consent withdrawal`."""
    return str(value).replace("_", " ").strip().capitalize()


# (param_name, camelCase key, entity collection or None for enum/humanised).
_TENANT_FILTER_SPECS = [
    ("department_id", "departmentId", "departments"),
    ("branch_id", "branchId", "branches"),
    ("host_id", "hostId", "system_users"),
    ("actor_id", "actorId", "system_users"),
    ("operation_type", "operationType", None),
    ("resource_type", "resourceType", None),
    ("incident_type", "incidentType", None),
    ("incident_status", "incidentStatus", None),
    ("severity", "severity", None),
    ("dsr_type", "dsrType", None),
    ("dsr_status", "dsrStatus", None),
    ("lawful_basis", "lawfulBasis", None),
    ("status_filter", "statusFilter", None),
]


async def _resolve_entity_label(collection: str, tenant_id: str, raw_id: str) -> str:
    """Best-effort entity name for a filter id; falls back to the raw id."""
    if not ObjectId.is_valid(raw_id):
        return raw_id
    query: Dict[str, Any] = {"_id": ObjectId(raw_id)}
    if collection in ("departments", "branches", "system_users"):
        query["tenant_id"] = tenant_id
    doc = await db[collection].find_one(query)
    if not doc:
        return raw_id
    if collection == "system_users":
        return doc.get("full_name") or doc.get("email") or raw_id
    return doc.get("name") or raw_id


async def _build_tenant_applied_filters(
    tenant_id: str, values: Dict[str, Optional[str]]
) -> List[AppliedFilter]:
    out: List[AppliedFilter] = []
    for param, key, collection in _TENANT_FILTER_SPECS:
        val = values.get(param)
        if not val:
            continue
        label = (
            await _resolve_entity_label(collection, tenant_id, val)
            if collection
            else _humanise(val)
        )
        out.append(AppliedFilter(key=key, label=label))
    return out


# ─── Public entrypoint ────────────────────────────────────────────────


async def get_insights(
    *,
    tenant_id: str,
    caller_user_id: str,
    caller_role: str,
    role_view: Optional[str] = None,
    start: Optional[int] = None,
    stop: Optional[int] = None,
    granularity: Optional[str] = None,
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
) -> InsightsResponse:
    now = int(time.time())
    role = role_view if role_view in _ROLE_SECTIONS else caller_role
    if role not in _ROLE_SECTIONS:
        role = "super_admin"

    if not tenant_id:
        return InsightsResponse(meta=InsightsMeta(role_view=role, last_updated=now))

    tier = await _resolve_tier(tenant_id)
    is_free = tier == "free"
    created_at = await _tenant_created_at(tenant_id)

    # Free is locked to a fixed 7-day daily window; start/stop are ignored.
    if is_free:
        eff_stop = now
        eff_start = max(now - _FREE_RANGE_DAYS * _DAY, created_at or 0)
        gran = "day"
    else:
        eff_start, eff_stop = _resolve_range(created_at, start, stop, now=now)
        gran = _auto_granularity(eff_start, eff_stop, granularity)

    scope = await _resolve_scope(
        tenant_id=tenant_id,
        caller_user_id=caller_user_id,
        caller_role=caller_role,
        department_id=department_id,
        branch_id=branch_id,
    )
    # ``host_id`` is a narrowing filter exposed to dept_admin (hosts in their
    # department) and super_admin; safe to apply for any role when supplied.
    scope.host_id = host_id

    referenced = _ROLE_SECTIONS[role]
    if is_free:
        available = [s for s in referenced if s in _FREE_AVAILABLE_SECTIONS]
    else:
        available = list(referenced)
    locked = [s for s in referenced if s not in available]

    top_limit = _FREE_TOP_LIST_MAX if is_free else _PAID_TOP_LIST_MAX
    by_host = role == "dept_admin"
    audit_base: Dict[str, Any] = {"tenant_id": tenant_id}

    # Build only the available sections.
    section_coros: Dict[str, Any] = {}
    for sid in available:
        if sid == "traffic":
            section_coros[sid] = _section_traffic(scope, eff_start, eff_stop, gran)
        elif sid == "audit":
            section_coros[sid] = _section_audit(
                scope,
                eff_start,
                eff_stop,
                gran,
                actor_id=actor_id,
                operation_type=operation_type,
                resource_type=resource_type,
            )
        elif sid == "hourly":
            section_coros[sid] = _section_hourly(scope, eff_start, eff_stop)
        elif sid == "visitStatus":
            section_coros[sid] = _section_visit_status(scope, eff_start, eff_stop)
        elif sid == "incident":
            section_coros[sid] = _section_incident(
                scope,
                eff_start,
                eff_stop,
                incident_type=incident_type,
                incident_status=incident_status,
                severity=severity,
                now=now,
            )
        elif sid == "dsr":
            section_coros[sid] = _section_dsr(
                scope,
                eff_start,
                eff_stop,
                dsr_type=dsr_type,
                dsr_status=dsr_status,
                lawful_basis=lawful_basis,
            )
        elif sid == "appointment":
            section_coros[sid] = _section_appointment(scope, eff_start, eff_stop)
        elif sid == "newReturning":
            section_coros[sid] = _section_new_returning(scope, eff_start, eff_stop)
        elif sid == "topDepartments":
            section_coros[sid] = _section_top_departments(
                scope, eff_start, eff_stop, limit=top_limit, by_host=by_host
            )
        elif sid == "feed":
            section_coros[sid] = _section_feed(scope, status_filter=status_filter)

    kpis, earliest = await asyncio.gather(
        _build_kpis(
            role,
            scope,
            eff_start,
            eff_stop,
            trends_enabled=not is_free,
            now=now,
            audit_match_base=audit_base,
        ),
        _earliest_data(tenant_id, created_at or eff_start),
    )

    section_keys = list(section_coros.keys())
    section_results: List[InsightsSection] = list(
        await asyncio.gather(*section_coros.values())
    )
    sections: Dict[str, InsightsSection] = {
        key: section_results[i] for i, key in enumerate(section_keys)
    }

    applied_filters = await _build_tenant_applied_filters(
        tenant_id,
        {
            "department_id": department_id,
            "branch_id": branch_id,
            "host_id": host_id,
            "actor_id": actor_id,
            "operation_type": operation_type,
            "resource_type": resource_type,
            "incident_type": incident_type,
            "incident_status": incident_status,
            "severity": severity,
            "dsr_type": dsr_type,
            "dsr_status": dsr_status,
            "lawful_basis": lawful_basis,
            "status_filter": status_filter,
        },
    )

    meta = InsightsMeta(
        role_view=role,
        department_id=scope.department_id,
        branch_id=scope.branch_id,
        tenant_created_at=created_at,
        earliest_data=earliest,
        applied_range={"start": eff_start, "stop": eff_stop},
        granularity=gran,
        plan_tier=tier,
        available_sections=available,
        locked_sections=locked,
        applied_filters=applied_filters,
        last_updated=now,
    )
    return InsightsResponse(meta=meta, kpis=kpis, sections=sections)


# Used by the route to surface a clean 403 on the export endpoint for Free.
async def is_export_allowed(tenant_id: str) -> bool:
    return (await _resolve_tier(tenant_id)) != "free"
