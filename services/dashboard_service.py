from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.database import db
from repositories.appointment_repo import count_appointments, get_appointments
from repositories.appointment_repo import (
    count_due_scheduled_appointments_for_checkout,
)
from repositories.incident_log_repo import get_incidents_approaching_deadline
from repositories.visit_session_repo import (
    count_awaiting_checkout_sessions,
    count_visit_sessions,
    get_visit_sessions,
)
from schemas.dashboard_stats_schema import (
    DayOfWeekBucket,
    DistributionSlice,
    GrowthMetric,
    HourlyBucket,
    RecentCheckIn,
    TenantDashboardStats,
    TimeSeriesPoint,
    TopItem,
    UpcomingAppointment,
)

# Human labels for enum values surfaced in pie-chart legends.
_VISIT_STATUS_LABELS: Dict[str, str] = {
    "registered": "Registered",
    "pending_verification": "Pending verification",
    "pending_approval": "Pending approval",
    "approved": "Approved",
    "checked_in": "Checked in",
    "checked_out": "Checked out",
    "rejected": "Rejected",
    "denied": "Denied",
    "cancelled": "Cancelled",
}
_CHECK_IN_METHOD_LABELS: Dict[str, str] = {
    "qr_registration": "QR registration",
    "id_scan": "ID scan",
    "manual_entry": "Manual entry",
}
_CHECK_OUT_METHOD_LABELS: Dict[str, str] = {
    "qr_scan": "QR scan",
    "manual": "Manual",
}
_VERIFICATION_STATUS_LABELS: Dict[str, str] = {
    "verified": "Verified",
    "unverified": "Unverified",
    "denied": "Denied",
}
_VERIFICATION_METHOD_LABELS: Dict[str, str] = {
    "id_scan": "ID scan",
    "qr_upload": "QR upload",
    "host_approval": "Host approval",
}
_BADGE_FORMAT_LABELS: Dict[str, str] = {"A6": "A6", "A7": "A7"}
_APPOINTMENT_STATUS_LABELS: Dict[str, str] = {
    "scheduled": "Scheduled",
    "checked_in": "Checked in",
    "checked_out": "Checked out",
    "no_show": "No show",
    "cancelled": "Cancelled",
    # Legacy values still possible on older records.
    "fulfilled": "Fulfilled",
    "missed": "Missed",
}
_INCIDENT_TYPE_LABELS: Dict[str, str] = {
    "data_breach": "Data breach",
    "unauthorized_access": "Unauthorised access",
    "data_export_exposure": "Data export exposure",
    "device_loss": "Device loss",
    "misconfiguration": "Misconfiguration",
    "third_party": "Third party",
}
_INCIDENT_STATUS_LABELS: Dict[str, str] = {
    "open": "Open",
    "investigating": "Investigating",
    "contained": "Contained",
    "reported_to_ndpc": "Reported to NDPC",
    "closed": "Closed",
}
_KYC_STATUS_LABELS: Dict[str, str] = {
    "pending": "Pending",
    "ongoing": "Ongoing",
    "success": "Success",
    "failed": "Failed",
    "skipped": "Skipped",
    "expired": "Expired",
}
_DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_ENUM_PREFIXES = {
    "AppointmentStatus",
    "CheckInMethod",
    "CheckOutMethod",
    "CheckinState",
    "KYCStatus",
    "VerificationMethod",
    "VerificationStatus",
    "VisitStatus",
}


# ─── Time helpers ─────────────────────────────────────────────────────


def _start_of_today_ts(now: Optional[int] = None) -> int:
    """UTC midnight of today as a unix timestamp."""
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp())


def _start_of_day(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp())


def _start_of_week_ts(now: Optional[int] = None) -> int:
    """ISO week start (Monday 00:00 UTC) of the week containing ``now``."""
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    monday = dt - timedelta(days=dt.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(monday.timestamp())


def _start_of_month_ts(now: Optional[int] = None) -> int:
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    first = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(first.timestamp())


def _shift_month(ts: int, months: int) -> int:
    """Return the timestamp of the same day-of-month ``months`` away."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    dt = dt.replace(year=year, month=month)
    return int(dt.timestamp())


def _start_of_quarter_ts(now: Optional[int] = None) -> int:
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    q_first_month = ((dt.month - 1) // 3) * 3 + 1
    first = dt.replace(
        month=q_first_month, day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return int(first.timestamp())


def _start_of_year_ts(now: Optional[int] = None) -> int:
    base = now if now is not None else int(time.time())
    dt = datetime.fromtimestamp(base, tz=timezone.utc)
    first = dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(first.timestamp())


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _normalise_count_key(value: Any) -> str:
    if value is None:
        return ""
    raw = _enum_value(value)
    text = str(raw)
    if "." in text:
        prefix, enum_name = text.rsplit(".", 1)
        if prefix in _ENUM_PREFIXES:
            return enum_name.lower()
    return text


def _merge_counts(*parts: Dict[str, int]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for counts in parts:
        for key, value in counts.items():
            merged[key] = merged.get(key, 0) + int(value or 0)
    return merged


def _checkin_match(
    tenant_id: str,
    department_id: Optional[str],
    *,
    state: Optional[Any] = None,
) -> Dict[str, Any]:
    match: Dict[str, Any] = {"tenant_id": tenant_id}
    if department_id:
        match["tenant_specific_data.department_id"] = department_id
    if state is not None:
        match["state"] = _enum_value(state)
    return match


# ─── Distribution / pie-chart helpers ─────────────────────────────────


def _build_distribution(
    counts: Dict[str, int],
    label_map: Dict[str, str],
) -> List[DistributionSlice]:
    """Convert ``{key: count}`` into pie slices ordered by value desc."""
    total = sum(counts.values())
    slices: List[DistributionSlice] = []
    for key, value in counts.items():
        if value == 0:
            continue
        label = label_map.get(key, key.replace("_", " ").title() if key else "Unknown")
        pct = round((value / total) * 100, 1) if total else 0.0
        slices.append(
            DistributionSlice(key=key or "unknown", label=label, value=value, percentage=pct)
        )
    slices.sort(key=lambda s: s.value, reverse=True)
    return slices


def _build_top_items(
    counts: Dict[str, int],
    *,
    label_map: Optional[Dict[str, str]] = None,
    extra_map: Optional[Dict[str, Dict[str, Any]]] = None,
    limit: int = 10,
    use_id: bool = True,
) -> List[TopItem]:
    """Convert ``{label_or_id: count}`` into a ranked TopItem list."""
    total = sum(counts.values())
    items: List[TopItem] = []
    for key, value in counts.items():
        if value == 0 or not key:
            continue
        label = (label_map or {}).get(key, key)
        pct = round((value / total) * 100, 1) if total else 0.0
        extra = (extra_map or {}).get(key, {})
        items.append(
            TopItem(
                id=key if use_id else None,
                label=label or "Unknown",
                value=value,
                percentage=pct,
                extra=extra,
            )
        )
    items.sort(key=lambda i: i.value, reverse=True)
    return items[:limit]


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


# ─── Common Mongo aggregation helpers ─────────────────────────────────


async def _group_count(
    collection: str,
    match: Dict[str, Any],
    field: str,
) -> Dict[str, int]:
    """``$group``-by-``$field`` count helper. Coerces key to str (or empty)."""
    pipeline: List[Dict[str, Any]] = [
        {"$match": match},
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
    ]
    out: Dict[str, int] = {}
    async for doc in db[collection].aggregate(pipeline):
        key = _normalise_count_key(doc.get("_id"))
        out[key] = out.get(key, 0) + int(doc.get("count", 0))
    return out


async def _visitor_verification_counts(tenant_id: str) -> Dict[str, int]:
    base = {"tenant_id": tenant_id, "deleted_at": None}
    total = await db.visitor_profiles.count_documents(base)
    verified = await db.visitor_profiles.count_documents(
        {
            **base,
            "$or": [
                {"verification_status": "verified"},
                {"last_verification_date": {"$exists": True, "$ne": None}},
                {"id_number": {"$exists": True, "$nin": [None, ""]}},
            ],
        }
    )
    denied = await db.visitor_profiles.count_documents(
        {**base, "verification_status": "denied"}
    )
    unverified = max(total - verified - denied, 0)
    return {
        "verified": verified,
        "unverified": unverified,
        "denied": denied,
    }


async def _visitor_verification_method_counts(tenant_id: str) -> Dict[str, int]:
    pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "deleted_at": None,
                "$or": [
                    {
                        "verification_method": {
                            "$exists": True,
                            "$nin": [None, ""],
                        }
                    },
                    {"id_type": {"$exists": True, "$nin": [None, ""]}},
                ],
            }
        },
        {
            "$project": {
                "method": {
                    "$ifNull": ["$verification_method", "$id_type"],
                }
            }
        },
        {"$group": {"_id": "$method", "count": {"$sum": 1}}},
    ]
    out: Dict[str, int] = {}
    async for doc in db.visitor_profiles.aggregate(pipeline):
        key = _normalise_count_key(doc.get("_id"))
        out[key] = out.get(key, 0) + int(doc.get("count", 0))
    return out


# ─── Visit-session-driven sections ────────────────────────────────────


def _base_visit_match(tenant_id: str, department_id: Optional[str]) -> Dict[str, Any]:
    base: Dict[str, Any] = {"tenant_id": tenant_id}
    if department_id:
        base["department_id"] = department_id
    return base


async def _overview_counts(
    tenant_id: str, department_id: Optional[str]
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)
    checkin_base = _checkin_match(tenant_id, department_id)
    total_visits, total_checkins = await asyncio.gather(
        count_visit_sessions(base),
        db.checkins.count_documents(checkin_base),
    )
    total_visitors = await db.visitor_profiles.count_documents(
        {"tenant_id": tenant_id, "deleted_at": None}
    )
    appt_filter: Dict[str, Any] = {"tenant_id": tenant_id}
    if department_id:
        appt_filter["department_id"] = department_id
    total_appointments = await count_appointments(appt_filter)
    total_departments = await db.departments.count_documents(
        {"tenant_id": tenant_id, "is_active": True}
    )
    total_branches = await db.branches.count_documents({"tenant_id": tenant_id})
    total_system_users = await db.system_users.count_documents(
        {"tenant_id": tenant_id}
    )
    incident_filter: Dict[str, Any] = {"tenant_id": tenant_id}
    total_incidents = await db.incident_logs.count_documents(incident_filter)
    open_incidents = await db.incident_logs.count_documents(
        {**incident_filter, "status": {"$nin": ["closed", "resolved"]}}
    )
    critical_incidents = await db.incident_logs.count_documents(
        {**incident_filter, "risk_level": "critical"}
    )
    return {
        "total_visits": total_visits + total_checkins,
        "total_visitors": total_visitors,
        "total_appointments": total_appointments,
        "total_departments": total_departments,
        "total_branches": total_branches,
        "total_system_users": total_system_users,
        "total_incidents": total_incidents,
        "open_incidents": open_incidents,
        "critical_incidents": critical_incidents,
    }


async def _live_state(
    tenant_id: str, department_id: Optional[str]
) -> Dict[str, Any]:
    (
        checked_in_sessions,
        approved_checkins,
        due_appointments,
        pending_approval,
        pending_kyc,
    ) = await asyncio.gather(
        count_awaiting_checkout_sessions(
            tenant_id=tenant_id, department_id=department_id
        ),
        db.checkins.count_documents(
            _checkin_match(tenant_id, department_id, state="approved")
        ),
        count_due_scheduled_appointments_for_checkout(
            tenant_id=tenant_id,
            due_before_ts=((int(time.time()) // 86400) + 1) * 86400,
            department_id=department_id,
        ),
        db.checkins.count_documents(
            _checkin_match(tenant_id, department_id, state="pending_approval")
        ),
        db.checkins.count_documents(
            _checkin_match(tenant_id, department_id, state="pending_verification")
        ),
    )
    currently_active = checked_in_sessions + approved_checkins
    return {
        "currently_active": currently_active,
        "awaiting_checkout": currently_active + due_appointments,
        "pending_approval": pending_approval,
        "pending_kyc": pending_kyc,
    }


async def _today_snapshot(
    tenant_id: str,
    department_id: Optional[str],
    *,
    start_today: int,
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)
    checkin_base = _checkin_match(tenant_id, department_id)
    today_filter = {**base, "check_in_time": {"$gte": start_today}}
    checkins_today_filter = {**checkin_base, "date_created": {"$gte": start_today}}

    visitors_today, public_checkins_today = await asyncio.gather(
        count_visit_sessions(today_filter),
        db.checkins.count_documents(checkins_today_filter),
    )
    visitors_today += public_checkins_today
    check_ins_today = visitors_today  # synonym; kept for clarity
    check_outs_today, public_checkouts_today = await asyncio.gather(
        count_visit_sessions({**base, "check_out_time": {"$gte": start_today}}),
        db.checkins.count_documents(
            {
                **checkin_base,
                "state": "checked_out",
                "checked_out_at": {"$gte": start_today},
            }
        ),
    )
    check_outs_today += public_checkouts_today
    denials_today, rejected_checkins_today = await asyncio.gather(
        count_visit_sessions(
            {**base, "status": "denied", "date_created": {"$gte": start_today}}
        ),
        db.checkins.count_documents(
            {
                **checkin_base,
                "state": "rejected",
                "last_updated": {"$gte": start_today},
            }
        ),
    )
    denials_today += rejected_checkins_today

    appt_filter: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "scheduled_datetime": {
            "$gte": start_today,
            "$lt": start_today + 86400,
        },
    }
    if department_id:
        appt_filter["department_id"] = department_id
    expected_today = await count_appointments(appt_filter)

    incidents_today = await db.incident_logs.count_documents(
        {"tenant_id": tenant_id, "date_created": {"$gte": start_today}}
    )

    # New vs returning today.
    pipeline = [
        {"$match": today_filter},
        {"$group": {"_id": "$visitor_profile_id"}},
    ]
    visitor_ids: List[str] = []
    async for doc in db.visit_sessions.aggregate(pipeline):
        if doc.get("_id"):
            visitor_ids.append(str(doc["_id"]))
    async for doc in db.checkins.aggregate(
        [
            {"$match": checkins_today_filter},
            {"$group": {"_id": "$visitor_id"}},
        ]
    ):
        if doc.get("_id"):
            visitor_ids.append(str(doc["_id"]))

    new_visitors = 0
    returning_visitors = 0
    if visitor_ids:
        # A visitor is "new" if their earliest visit_session.check_in_time
        # is on or after start_today.
        first_visit_pipeline = [
            {"$match": {**base, "visitor_profile_id": {"$in": visitor_ids}}},
            {
                "$group": {
                    "_id": "$visitor_profile_id",
                    "first_visit": {"$min": "$check_in_time"},
                }
            },
        ]
        async for doc in db.visit_sessions.aggregate(first_visit_pipeline):
            first = doc.get("first_visit") or 0
            if first >= start_today:
                new_visitors += 1
            else:
                returning_visitors += 1

    # Peak hour today (UTC) by check-in count.
    peak_hour = None
    hourly_pipeline = [
        {"$match": today_filter},
        {
            "$group": {
                "_id": {
                    "$hour": {
                        "$toDate": {"$multiply": ["$check_in_time", 1000]},
                    }
                },
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"count": -1}},
        {"$limit": 1},
    ]
    async for doc in db.visit_sessions.aggregate(hourly_pipeline):
        peak_hour = doc.get("_id")
        break
    if peak_hour is None:
        checkin_hourly_pipeline = [
            {"$match": checkins_today_filter},
            {
                "$group": {
                    "_id": {
                        "$hour": {
                            "$toDate": {"$multiply": ["$date_created", 1000]},
                        }
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"count": -1}},
            {"$limit": 1},
        ]
        async for doc in db.checkins.aggregate(checkin_hourly_pipeline):
            peak_hour = doc.get("_id")
            break

    return {
        "visitors_today": visitors_today,
        "check_ins_today": check_ins_today,
        "check_outs_today": check_outs_today,
        "expected_today": expected_today,
        "new_visitors_today": new_visitors,
        "returning_visitors_today": returning_visitors,
        "denials_today": denials_today,
        "incidents_today": incidents_today,
        "peak_hour_today": peak_hour,
    }


async def _period_visits(
    tenant_id: str,
    department_id: Optional[str],
    *,
    now: int,
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)
    checkin_base = _checkin_match(tenant_id, department_id)
    start_today = _start_of_day(now)
    start_yesterday = start_today - 86400
    start_week = _start_of_week_ts(now)
    start_last_week = start_week - 7 * 86400
    start_month = _start_of_month_ts(now)
    start_last_month = _shift_month(start_month, -1)
    start_quarter = _start_of_quarter_ts(now)
    start_year = _start_of_year_ts(now)

    async def _count(gte: int, lt: Optional[int] = None) -> int:
        rng: Dict[str, Any] = {"$gte": gte}
        if lt is not None:
            rng["$lt"] = lt
        sessions, checkins = await asyncio.gather(
            count_visit_sessions({**base, "check_in_time": rng}),
            db.checkins.count_documents({**checkin_base, "date_created": rng}),
        )
        return sessions + checkins

    (
        today_visits,
        yesterday_visits,
        this_week,
        last_week,
        this_month,
        last_month,
        this_quarter,
        this_year,
    ) = await asyncio.gather(
        _count(start_today),
        _count(start_yesterday, start_today),
        _count(start_week),
        _count(start_last_week, start_week),
        _count(start_month),
        _count(start_last_month, start_month),
        _count(start_quarter),
        _count(start_year),
    )

    return {
        "today_visits": today_visits,
        "yesterday_visits": yesterday_visits,
        "visits_this_week": this_week,
        "visits_last_week": last_week,
        "visits_this_month": this_month,
        "visits_last_month": last_month,
        "visits_this_quarter": this_quarter,
        "visits_this_year": this_year,
    }


async def _signup_periods(tenant_id: str, *, now: int) -> Dict[str, Any]:
    start_today = _start_of_day(now)
    start_7d = start_today - 6 * 86400
    start_30d = start_today - 29 * 86400
    start_month = _start_of_month_ts(now)
    start_last_month = _shift_month(start_month, -1)
    start_week = _start_of_week_ts(now)
    start_last_week = start_week - 7 * 86400

    async def _count(filter_dict: Dict[str, Any]) -> int:
        return await db.visitor_profiles.count_documents(filter_dict)

    base = {"tenant_id": tenant_id, "deleted_at": None}
    (
        today,
        seven_d,
        thirty_d,
        this_month,
        last_month,
        this_week,
        last_week,
    ) = await asyncio.gather(
        _count({**base, "date_created": {"$gte": start_today}}),
        _count({**base, "date_created": {"$gte": start_7d}}),
        _count({**base, "date_created": {"$gte": start_30d}}),
        _count({**base, "date_created": {"$gte": start_month}}),
        _count(
            {**base, "date_created": {"$gte": start_last_month, "$lt": start_month}}
        ),
        _count({**base, "date_created": {"$gte": start_week}}),
        _count(
            {**base, "date_created": {"$gte": start_last_week, "$lt": start_week}}
        ),
    )
    return {
        "new_signups_today": today,
        "new_signups_7d": seven_d,
        "new_signups_30d": thirty_d,
        "new_signups_this_month": this_month,
        "new_signups_last_month": last_month,
        "new_signups_this_week": this_week,
        "new_signups_last_week": last_week,
    }


async def _duration_stats(
    tenant_id: str, department_id: Optional[str], *, start_today: int
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)
    pipeline = [
        {
            "$match": {
                **base,
                "check_in_time": {"$ne": None},
                "check_out_time": {"$ne": None},
            }
        },
        {
            "$group": {
                "_id": None,
                "avg": {
                    "$avg": {"$subtract": ["$check_out_time", "$check_in_time"]}
                },
            }
        },
    ]
    avg_seconds = 0
    async for doc in db.visit_sessions.aggregate(pipeline):
        avg_seconds = int(doc.get("avg") or 0)
        break

    today_pipeline = [
        {
            "$match": {
                **base,
                "check_in_time": {"$gte": start_today},
                "check_out_time": {"$ne": None},
            }
        },
        {
            "$group": {
                "_id": None,
                "max": {
                    "$max": {"$subtract": ["$check_out_time", "$check_in_time"]}
                },
                "min": {
                    "$min": {"$subtract": ["$check_out_time", "$check_in_time"]}
                },
            }
        },
    ]
    longest = 0
    shortest = 0
    async for doc in db.visit_sessions.aggregate(today_pipeline):
        longest = int(doc.get("max") or 0)
        shortest = int(doc.get("min") or 0)
        break

    return {
        "avg_visit_duration_seconds": avg_seconds,
        "avg_visit_duration_minutes": round(avg_seconds / 60.0, 1) if avg_seconds else 0.0,
        "longest_visit_today_minutes": round(longest / 60.0, 1) if longest else 0.0,
        "shortest_visit_today_minutes": round(shortest / 60.0, 1) if shortest else 0.0,
    }


async def _visitor_segmentation(tenant_id: str) -> Dict[str, Any]:
    base = {"tenant_id": tenant_id, "deleted_at": None}
    total_profiles = await db.visitor_profiles.count_documents(base)
    returning = await db.visitor_profiles.count_documents(
        {**base, "total_visits": {"$gte": 2}}
    )
    vip = await db.visitor_profiles.count_documents(
        {**base, "total_visits": {"$gte": 5}}
    )
    new = max(total_profiles - returning, 0)
    retention_rate = (
        round((returning / total_profiles) * 100, 1) if total_profiles else 0.0
    )

    new_vs_returning = _build_distribution(
        {"new": new, "returning": returning},
        {"new": "First-time visitors", "returning": "Returning visitors"},
    )

    return {
        "returning_visitor_count": returning,
        "vip_visitor_count": vip,
        "visitor_retention_rate": retention_rate,
        "new_vs_returning": new_vs_returning,
    }


async def _distributions(
    tenant_id: str, department_id: Optional[str]
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)
    checkin_base = _checkin_match(tenant_id, department_id)
    appt_match: Dict[str, Any] = {"tenant_id": tenant_id}
    if department_id:
        appt_match["department_id"] = department_id

    (
        visit_session_status,
        checkin_state,
        check_in_method,
        check_out_method,
        verification_status,
        verification_method,
        consent_granted_counts,
        badge_format,
        visit_purpose,
        checkin_purpose,
        appointment_status,
        incident_type,
        incident_status,
        kyc_status,
    ) = await asyncio.gather(
        _group_count("visit_sessions", base, "status"),
        _group_count("checkins", checkin_base, "state"),
        _group_count("visit_sessions", base, "check_in_method"),
        _group_count(
            "visit_sessions",
            {**base, "check_out_method": {"$ne": None}},
            "check_out_method",
        ),
        _visitor_verification_counts(tenant_id),
        _visitor_verification_method_counts(tenant_id),
        _group_count("visit_sessions", base, "consent_granted"),
        _group_count(
            "visit_sessions",
            {**base, "badge_format": {"$ne": None}},
            "badge_format",
        ),
        _group_count(
            "visit_sessions", {**base, "purpose": {"$ne": None}}, "purpose"
        ),
        _group_count(
            "checkins",
            {**checkin_base, "purpose.purpose": {"$nin": [None, ""]}},
            "purpose.purpose",
        ),
        _group_count("expected_appointments", appt_match, "status"),
        _group_count("incident_logs", {"tenant_id": tenant_id}, "incident_type"),
        _group_count("incident_logs", {"tenant_id": tenant_id}, "status"),
        _group_count("kyc_verifications", {"tenant_id": tenant_id}, "status"),
    )
    visit_status = _merge_counts(visit_session_status, checkin_state)
    purpose = _merge_counts(visit_purpose, checkin_purpose)

    consent_dist_keys = {
        "true": consent_granted_counts.get("True", 0)
        + consent_granted_counts.get("true", 0),
        "false": consent_granted_counts.get("False", 0)
        + consent_granted_counts.get("false", 0),
        "none": consent_granted_counts.get("", 0)
        + consent_granted_counts.get("None", 0),
    }
    consent_label_map = {
        "true": "Granted",
        "false": "Withheld",
        "none": "Not captured",
    }

    return {
        "visit_status_distribution": _build_distribution(
            visit_status, _VISIT_STATUS_LABELS
        ),
        "check_in_method_distribution": _build_distribution(
            check_in_method, _CHECK_IN_METHOD_LABELS
        ),
        "check_out_method_distribution": _build_distribution(
            check_out_method, _CHECK_OUT_METHOD_LABELS
        ),
        "verification_status_distribution": _build_distribution(
            verification_status, _VERIFICATION_STATUS_LABELS
        ),
        "verification_method_distribution": _build_distribution(
            verification_method, _VERIFICATION_METHOD_LABELS
        ),
        "consent_distribution": _build_distribution(
            consent_dist_keys, consent_label_map
        ),
        "badge_format_distribution": _build_distribution(
            badge_format, _BADGE_FORMAT_LABELS
        ),
        "purpose_distribution": _build_distribution(purpose, {}),
        "appointment_status_distribution": _build_distribution(
            appointment_status, _APPOINTMENT_STATUS_LABELS
        ),
        "incident_type_distribution": _build_distribution(
            incident_type, _INCIDENT_TYPE_LABELS
        ),
        "incident_status_distribution": _build_distribution(
            incident_status, _INCIDENT_STATUS_LABELS
        ),
        "kyc_status_distribution": _build_distribution(
            kyc_status, _KYC_STATUS_LABELS
        ),
        "_raw": {
            "visit_status": visit_status,
            "verification_status": verification_status,
            "consent": consent_dist_keys,
            "appointment_status": appointment_status,
            "kyc_status": kyc_status,
        },
    }


async def _resolve_id_labels(
    collection: str,
    ids: List[str],
    label_field: str,
) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    """Resolve a batch of object ids to display labels.

    Returns ``(label_map, extra_map)`` where ``extra_map`` carries optional
    metadata (e.g. department code, branch active flag) to surface in the
    pie/legend tooltip."""
    from bson import ObjectId

    if not ids:
        return {}, {}
    obj_ids = []
    for raw in ids:
        try:
            obj_ids.append(ObjectId(raw))
        except Exception:
            continue
    if not obj_ids:
        return {}, {}
    cursor = db[collection].find({"_id": {"$in": obj_ids}})
    labels: Dict[str, str] = {}
    extras: Dict[str, Dict[str, Any]] = {}
    async for doc in cursor:
        sid = str(doc.get("_id"))
        labels[sid] = str(doc.get(label_field) or "Unknown")
        extras[sid] = {
            k: doc.get(k)
            for k in ("code", "is_active", "company_name")
            if k in doc
        }
    return labels, extras


async def _top_lists(
    tenant_id: str, department_id: Optional[str]
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)
    checkin_base = _checkin_match(tenant_id, department_id)

    async def _grouped(field: str, top_n: int = 10) -> Dict[str, int]:
        pipeline = [
            {"$match": {**base, field: {"$ne": None}}},
            {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": top_n},
        ]
        out: Dict[str, int] = {}
        async for doc in db.visit_sessions.aggregate(pipeline):
            key = doc.get("_id")
            if key is None or key == "":
                continue
            out[_normalise_count_key(key)] = int(doc.get("count", 0))
        return out

    async def _grouped_checkins(field: str, top_n: int = 10) -> Dict[str, int]:
        pipeline = [
            {"$match": {**checkin_base, field: {"$nin": [None, ""]}}},
            {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": top_n},
        ]
        out: Dict[str, int] = {}
        async for doc in db.checkins.aggregate(pipeline):
            key = doc.get("_id")
            if key is None or key == "":
                continue
            out[_normalise_count_key(key)] = int(doc.get("count", 0))
        return out

    (
        session_dept_counts,
        checkin_dept_counts,
        host_counts,
        company_counts,
        visitor_counts,
        session_purpose_counts,
        checkin_purpose_counts,
        denial_counts,
        check_in_method_counts,
        checkin_branch_counts,
    ) = await asyncio.gather(
        _grouped("department_id"),
        _grouped_checkins("tenant_specific_data.department_id"),
        _grouped("host_id"),
        _grouped("company_snapshot"),
        _grouped("visitor_profile_id"),
        _grouped("purpose"),
        _grouped_checkins("purpose.purpose"),
        _grouped("denial_reason"),
        _grouped("check_in_method"),
        _grouped_checkins("tenant_specific_data.branch_id"),
    )
    dept_counts = _merge_counts(session_dept_counts, checkin_dept_counts)
    purpose_counts = _merge_counts(session_purpose_counts, checkin_purpose_counts)

    # Branch top-list — visit_sessions don't carry branch_id directly, so
    # we route through departments to find their branches. Cheap because
    # we've already capped at top 10 departments.
    dept_label_map, dept_extras = await _resolve_id_labels(
        "departments", list(dept_counts.keys()), "name"
    )
    host_label_map: Dict[str, str] = {}
    if host_counts:
        from bson import ObjectId

        ids = []
        for raw in host_counts.keys():
            try:
                ids.append(ObjectId(raw))
            except Exception:
                continue
        if ids:
            cursor = db.system_users.find({"_id": {"$in": ids}})
            async for doc in cursor:
                host_label_map[str(doc["_id"])] = (
                    doc.get("full_name")
                    or doc.get("email")
                    or "Unknown host"
                )

    visitor_label_map: Dict[str, str] = {}
    visitor_extra: Dict[str, Dict[str, Any]] = {}
    if visitor_counts:
        from bson import ObjectId

        ids = []
        for raw in visitor_counts.keys():
            try:
                ids.append(ObjectId(raw))
            except Exception:
                continue
        if ids:
            cursor = db.visitor_profiles.find({"_id": {"$in": ids}})
            async for doc in cursor:
                sid = str(doc["_id"])
                visitor_label_map[sid] = doc.get("full_name") or "Unknown visitor"
                visitor_extra[sid] = {
                    "company": doc.get("company"),
                    "total_visits": doc.get("total_visits", 0),
                }

    # Branch rollup from department.branch_id.
    branch_counts: Dict[str, int] = dict(checkin_branch_counts)
    if dept_counts:
        from bson import ObjectId

        ids = []
        for raw in dept_counts.keys():
            try:
                ids.append(ObjectId(raw))
            except Exception:
                continue
        dept_to_branch: Dict[str, str] = {}
        if ids:
            cursor = db.departments.find({"_id": {"$in": ids}})
            async for doc in cursor:
                branch_id = doc.get("branch_id")
                if branch_id:
                    dept_to_branch[str(doc["_id"])] = str(branch_id)
        for dept_id, count in dept_counts.items():
            br = dept_to_branch.get(dept_id)
            if not br:
                continue
            branch_counts[br] = branch_counts.get(br, 0) + count
    branch_label_map, _ = await _resolve_id_labels(
        "branches", list(branch_counts.keys()), "name"
    )

    return {
        "top_departments": _build_top_items(
            dept_counts, label_map=dept_label_map, extra_map=dept_extras
        ),
        "top_hosts": _build_top_items(host_counts, label_map=host_label_map),
        "top_companies": _build_top_items(company_counts, use_id=False),
        "top_visitors": _build_top_items(
            visitor_counts, label_map=visitor_label_map, extra_map=visitor_extra
        ),
        "top_branches": _build_top_items(branch_counts, label_map=branch_label_map),
        "top_purposes": _build_top_items(purpose_counts, use_id=False),
        "top_denial_reasons": _build_top_items(denial_counts, use_id=False, limit=5),
        "top_check_in_methods": _build_top_items(
            check_in_method_counts, label_map=_CHECK_IN_METHOD_LABELS
        ),
    }


async def _daily_series(
    collection: str,
    match: Dict[str, Any],
    *,
    timestamp_field: str,
    days: int,
    now: int,
) -> List[TimeSeriesPoint]:
    """Build a contiguous daily series ending today, zero-filling gaps."""
    start = _start_of_day(now) - (days - 1) * 86400
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
                "count": {"$sum": 1},
            }
        },
    ]
    counts: Dict[str, int] = {}
    async for doc in db[collection].aggregate(pipeline):
        counts[str(doc["_id"])] = int(doc["count"])

    out: List[TimeSeriesPoint] = []
    for i in range(days):
        ts = start + i * 86400
        label = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append(
            TimeSeriesPoint(timestamp=ts, label=label, value=counts.get(label, 0))
        )
    return out


def _combine_daily_series(
    first: List[TimeSeriesPoint], second: List[TimeSeriesPoint]
) -> List[TimeSeriesPoint]:
    second_by_ts = {point.timestamp: point.value for point in second}
    return [
        TimeSeriesPoint(
            timestamp=point.timestamp,
            label=point.label,
            value=point.value + second_by_ts.get(point.timestamp, 0),
        )
        for point in first
    ]


async def _time_series(
    tenant_id: str, department_id: Optional[str], *, now: int
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)
    checkin_base = _checkin_match(tenant_id, department_id)
    appt_filter: Dict[str, Any] = {"tenant_id": tenant_id}
    if department_id:
        appt_filter["department_id"] = department_id

    (
        visit_sessions_7,
        checkins_7,
        visit_sessions_30,
        checkins_30,
        session_check_outs_7,
        checkin_check_outs_7,
        signups_30,
        appts_30,
        incidents_30,
    ) = await asyncio.gather(
        _daily_series(
            "visit_sessions", base, timestamp_field="check_in_time", days=7, now=now
        ),
        _daily_series(
            "checkins", checkin_base, timestamp_field="date_created", days=7, now=now
        ),
        _daily_series(
            "visit_sessions", base, timestamp_field="check_in_time", days=30, now=now
        ),
        _daily_series(
            "checkins", checkin_base, timestamp_field="date_created", days=30, now=now
        ),
        _daily_series(
            "visit_sessions",
            {**base, "check_out_time": {"$ne": None}},
            timestamp_field="check_out_time",
            days=7,
            now=now,
        ),
        _daily_series(
            "checkins",
            {**checkin_base, "checked_out_at": {"$ne": None}},
            timestamp_field="checked_out_at",
            days=7,
            now=now,
        ),
        _daily_series(
            "visitor_profiles",
            {"tenant_id": tenant_id, "deleted_at": None},
            timestamp_field="date_created",
            days=30,
            now=now,
        ),
        _daily_series(
            "expected_appointments",
            appt_filter,
            timestamp_field="date_created",
            days=30,
            now=now,
        ),
        _daily_series(
            "incident_logs",
            {"tenant_id": tenant_id},
            timestamp_field="date_created",
            days=30,
            now=now,
        ),
    )
    visits_7 = _combine_daily_series(visit_sessions_7, checkins_7)
    visits_30 = _combine_daily_series(visit_sessions_30, checkins_30)
    check_outs_7 = _combine_daily_series(session_check_outs_7, checkin_check_outs_7)
    return {
        "visits_last_7_days": visits_7,
        "visits_last_30_days": visits_30,
        "check_outs_last_7_days": check_outs_7,
        "signups_last_30_days": signups_30,
        "appointments_last_30_days": appts_30,
        "incidents_last_30_days": incidents_30,
    }


async def _heatmaps(
    tenant_id: str, department_id: Optional[str], *, now: int
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)
    checkin_base = _checkin_match(tenant_id, department_id)
    start_30d = _start_of_day(now) - 29 * 86400
    match = {**base, "check_in_time": {"$gte": start_30d}}
    checkin_match = {**checkin_base, "date_created": {"$gte": start_30d}}

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
    checkin_hour_pipeline = [
        {"$match": checkin_match},
        {
            "$group": {
                "_id": {
                    "$hour": {
                        "$toDate": {"$multiply": ["$date_created", 1000]}
                    }
                },
                "count": {"$sum": 1},
            }
        },
    ]
    async for doc in db.checkins.aggregate(checkin_hour_pipeline):
        hour = int(doc["_id"] or 0)
        hour_counts[hour] = hour_counts.get(hour, 0) + int(doc["count"])
    hourly = [
        HourlyBucket(hour=h, label=f"{h:02d}:00", value=hour_counts.get(h, 0))
        for h in range(24)
    ]

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
        # MongoDB $isoDayOfWeek returns 1..7 with 1=Monday; Python uses 0..6.
        iso = int(doc["_id"] or 1)
        dow_counts[iso - 1] = int(doc["count"])
    checkin_dow_pipeline = [
        {"$match": checkin_match},
        {
            "$group": {
                "_id": {
                    "$isoDayOfWeek": {
                        "$toDate": {"$multiply": ["$date_created", 1000]}
                    }
                },
                "count": {"$sum": 1},
            }
        },
    ]
    async for doc in db.checkins.aggregate(checkin_dow_pipeline):
        iso = int(doc["_id"] or 1)
        key = iso - 1
        dow_counts[key] = dow_counts.get(key, 0) + int(doc["count"])
    dow = [
        DayOfWeekBucket(day=d, label=_DOW_LABELS[d], value=dow_counts.get(d, 0))
        for d in range(7)
    ]

    return {"hourly_distribution": hourly, "day_of_week_distribution": dow}


async def _appointment_metrics(
    *,
    appointment_status_counts: Dict[str, int],
    total_visits: int,
) -> Dict[str, Any]:
    # Roll legacy "fulfilled" into the new "checked_out" bucket so old
    # records and new ones contribute to the same metric.
    scheduled = appointment_status_counts.get("scheduled", 0)
    checked_in = appointment_status_counts.get("checked_in", 0)
    fulfilled = appointment_status_counts.get(
        "checked_out", 0
    ) + appointment_status_counts.get("fulfilled", 0)
    no_show = appointment_status_counts.get(
        "no_show", 0
    ) + appointment_status_counts.get("missed", 0)
    cancelled = appointment_status_counts.get("cancelled", 0)
    total = scheduled + checked_in + fulfilled + no_show + cancelled
    fulfillment = round((fulfilled / total) * 100, 1) if total else 0.0
    no_show_rate = round(((no_show + cancelled) / total) * 100, 1) if total else 0.0
    conversion = (
        round((fulfilled / total_visits) * 100, 1) if total_visits else 0.0
    )
    return {
        "appointments_scheduled": scheduled,
        "appointments_fulfilled": fulfilled,
        "appointments_missed": no_show,
        "appointments_cancelled": cancelled,
        "appointment_fulfillment_rate": fulfillment,
        "appointment_no_show_rate": no_show_rate,
        "appointment_conversion_rate": conversion,
    }


async def _quality_metrics(
    tenant_id: str,
    department_id: Optional[str],
    *,
    visit_status_counts: Dict[str, int],
    verification_status_counts: Dict[str, int],
    consent_counts: Dict[str, int],
    kyc_counts: Dict[str, int],
    total_visits: int,
    total_visitors: int,
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)

    verified = verification_status_counts.get("verified", 0)
    verification_rate = (
        round((verified / total_visitors) * 100, 1) if total_visitors else 0.0
    )

    consent_total = sum(consent_counts.values()) or 0
    granted = consent_counts.get("true", 0)
    consent_rate = (
        round((granted / consent_total) * 100, 1) if consent_total else 0.0
    )
    consent_withdrawal_count = await db.visit_sessions.count_documents(
        {**base, "consent_withdrawal_at": {"$ne": None}}
    )

    denied = visit_status_counts.get("denied", 0)
    denial_rate = round((denied / total_visits) * 100, 1) if total_visits else 0.0

    badge_issued = await db.visit_sessions.count_documents(
        {**base, "badge_qr_token": {"$ne": None}}
    )
    badge_rate = (
        round((badge_issued / total_visits) * 100, 1) if total_visits else 0.0
    )

    kyc_terminal = (
        kyc_counts.get("success", 0)
        + kyc_counts.get("failed", 0)
        + kyc_counts.get("expired", 0)
    )
    kyc_pass = (
        round((kyc_counts.get("success", 0) / kyc_terminal) * 100, 1)
        if kyc_terminal
        else 0.0
    )

    return {
        "verification_rate": verification_rate,
        "consent_rate": consent_rate,
        "consent_withdrawal_count": consent_withdrawal_count,
        "denial_rate": denial_rate,
        "badge_issue_rate": badge_rate,
        "avg_kyc_pass_rate": kyc_pass,
    }


async def _real_time_samples(
    tenant_id: str, department_id: Optional[str], *, start_today: int
) -> Dict[str, Any]:
    base = _base_visit_match(tenant_id, department_id)
    checkin_base = _checkin_match(tenant_id, department_id)
    sessions = await get_visit_sessions(filter_dict=base, start=0, stop=10)
    recent: List[RecentCheckIn] = []
    for s in sessions:
        duration = None
        if s.check_in_time and s.check_out_time:
            duration = max(int((s.check_out_time - s.check_in_time) / 60), 0)
        recent.append(
            RecentCheckIn(
                id=s.id or "",
                visitor_name=s.visitor_name_snapshot,
                company=s.company_snapshot,
                department_name=s.department_name_snapshot,
                host_name=s.host_name_snapshot,
                purpose=s.purpose,
                status=_normalise_count_key(s.status) if s.status else None,
                check_in_time=s.check_in_time,
                check_out_time=s.check_out_time,
                duration_minutes=duration,
                verified=_normalise_count_key(s.verification_status) == "verified",
            )
        )

    checkin_docs: List[Dict[str, Any]] = []
    async for doc in (
        db.checkins.find(checkin_base).sort("date_created", -1).limit(10)
    ):
        checkin_docs.append(doc)
    if checkin_docs:
        from repositories.visitor_repo import get_visitors_by_ids

        visitor_ids = [
            str(doc.get("visitor_id")) for doc in checkin_docs if doc.get("visitor_id")
        ]
        visitors = await get_visitors_by_ids(
            tenant_id=tenant_id, visitor_ids=visitor_ids
        )
        visitors_by_id = {v.id: v for v in visitors if v.id}
        for doc in checkin_docs:
            visitor = visitors_by_id.get(str(doc.get("visitor_id") or ""))
            bio = visitor.bio_data if visitor is not None else {}
            purpose_doc = doc.get("purpose") or {}
            purpose = (
                purpose_doc.get("purpose")
                if isinstance(purpose_doc, dict)
                else str(purpose_doc)
            )
            check_in_time = doc.get("approved_at") or doc.get("date_created")
            check_out_time = doc.get("checked_out_at")
            duration = None
            if check_in_time and check_out_time:
                duration = max(int((check_out_time - check_in_time) / 60), 0)
            recent.append(
                RecentCheckIn(
                    id=str(doc.get("_id") or ""),
                    visitor_name=visitor.full_name if visitor is not None else None,
                    company=(
                        (bio or {}).get("company")
                        or (bio or {}).get("organization")
                    ),
                    department_name=None,
                    host_name=None,
                    purpose=purpose,
                    status=_normalise_count_key(doc.get("state")),
                    check_in_time=check_in_time,
                    check_out_time=check_out_time,
                    duration_minutes=duration,
                    verified=bool(doc.get("verified")),
                )
            )
    recent.sort(key=lambda row: row.check_in_time or 0, reverse=True)
    recent = recent[:10]

    appt_filter: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "scheduled_datetime": {
            "$gte": start_today,
            "$lt": start_today + 86400,
        },
        "status": {"$in": ["scheduled"]},
    }
    if department_id:
        appt_filter["department_id"] = department_id
    upcoming_docs = await get_appointments(filter_dict=appt_filter, start=0, stop=10)
    upcoming: List[UpcomingAppointment] = [
        UpcomingAppointment(
            id=a.id or "",
            visitor_name=a.visitor_name_snapshot,
            host_name=a.host_name_snapshot,
            department_id=a.department_id,
            purpose=a.purpose,
            status=_normalise_count_key(a.status) if a.status else None,
            scheduled_datetime=a.scheduled_datetime,
        )
        for a in upcoming_docs
    ]

    return {
        "recent_check_ins": recent,
        "upcoming_appointments_today": upcoming,
    }


async def _compliance_summary(tenant_id: str) -> Dict[str, Any]:
    open_dsr = await db.data_subject_requests.count_documents(
        {"tenant_id": tenant_id, "status": {"$nin": ["completed", "rejected"]}}
    )
    total_dsr = await db.data_subject_requests.count_documents(
        {"tenant_id": tenant_id}
    )
    now = int(time.time())
    dsr_30d = await db.data_subject_requests.count_documents(
        {"tenant_id": tenant_id, "date_created": {"$gte": now - 30 * 86400}}
    )
    incidents_approaching = await get_incidents_approaching_deadline(
        tenant_id=tenant_id, start=0, stop=200
    )
    privacy_notices = await db.privacy_notices.count_documents(
        {"tenant_id": tenant_id}
    )
    retention_policies = await db.retention_policies.count_documents(
        {"tenant_id": tenant_id}
    )
    sub_processors = await db.sub_processors.count_documents(
        {"tenant_id": tenant_id}
    )
    return {
        "open_dsr_requests": open_dsr,
        "total_dsr_requests": total_dsr,
        "dsr_requests_30d": dsr_30d,
        "incidents_approaching_deadline": len(incidents_approaching),
        "privacy_notices_count": privacy_notices,
        "retention_policies_count": retention_policies,
        "sub_processors_count": sub_processors,
    }


async def _audit_summary(tenant_id: str, *, start_today: int, now: int) -> Dict[str, Any]:
    total = await db.audit_trail.count_documents({"tenant_id": tenant_id})
    today = await db.audit_trail.count_documents(
        {"tenant_id": tenant_id, "timestamp": {"$gte": start_today}}
    )
    seven_d = await db.audit_trail.count_documents(
        {"tenant_id": tenant_id, "timestamp": {"$gte": now - 7 * 86400}}
    )
    return {
        "total_audit_events": total,
        "audit_events_today": today,
        "audit_events_7d": seven_d,
    }


async def _overdue_checkouts(
    tenant_id: str, department_id: Optional[str]
) -> int:
    """Count active visits whose expected duration window has elapsed.

    The expected duration is stored on the originating ``checkin`` doc.
    We approximate "overdue" as any active visit older than 4h when no
    checkin record is found, which is a deliberate over-count rather
    than under-count (better to surface than miss)."""
    base = _base_visit_match(tenant_id, department_id)
    now = int(time.time())
    threshold = now - 4 * 3600
    return await db.visit_sessions.count_documents(
        {**base, "status": "checked_in", "check_in_time": {"$lt": threshold}}
    )


# ─── Public entrypoint ────────────────────────────────────────────────


# Capability keys the frontend can use to render upgrade nudges in the
# spots where Free-tier dashboard fields are ``null``. Names map 1:1 to
# UI sections so a future field-level matrix isn't needed.
_FREE_UPGRADE_CAPABILITIES: List[str] = [
    "charts",
    "trends",
    "exports",
    "top-N",
    "heatmaps",
    "recent_activity",
    "growth",
    "compliance",
    "audit",
    "incidents",
    "appointments",
]


async def _resolve_tier_safely(tenant_id: str) -> str:
    """Best-effort resolution of the tenant's plan tier.

    Returns the tier string (``"free"`` / ``"starter"`` / ``"premium"`` /
    ``"enterprise"``) or ``"free"`` if resolution fails. The free default
    is intentional — when in doubt, serve the slim dashboard rather than
    paid-tier content to an unverified tenant.
    """
    try:
        from services.plan_cache_service import resolve_tenant_plan

        resolved = await resolve_tenant_plan(tenant_id)
        if resolved and resolved.get("tier"):
            return str(resolved["tier"]).lower()
    except Exception:
        pass
    return "free"


async def _build_basic_stats_for_free(
    tenant_id: str, role: Optional[str] = None
) -> TenantDashboardStats:
    """Slim dashboard payload for Free-plan tenants.

    Returns only basic counters, live state, and today's snapshot. Every
    other field (charts, time series, heatmaps, top-N, recent activity,
    growth metrics, compliance, audit) stays ``None`` so the frontend can
    render an upgrade nudge in its place. The capability keys the user
    could unlock are listed in ``upgrade_required_for``.

    All queries are direct counts — no ``$facet``, no time-bucket
    aggregations, no ``_resolve_id_labels`` — to keep the slim path
    genuinely cheap on the precompute fanout.
    """
    now = int(time.time())
    start_today = _start_of_today_ts(now)
    base = _base_visit_match(tenant_id, None)
    checkin_base = _checkin_match(tenant_id, None)
    today_filter = {**base, "check_in_time": {"$gte": start_today}}

    total_visits, total_checkins = await asyncio.gather(
        count_visit_sessions(base),
        db.checkins.count_documents(checkin_base),
    )
    total_visits += total_checkins
    total_visitors = await db.visitor_profiles.count_documents(
        {"tenant_id": tenant_id, "deleted_at": None}
    )
    total_departments = await db.departments.count_documents(
        {"tenant_id": tenant_id, "is_active": True}
    )
    total_system_users = await db.system_users.count_documents(
        {"tenant_id": tenant_id}
    )

    checked_in_sessions, approved_checkins, pending_approval = await asyncio.gather(
        count_awaiting_checkout_sessions(tenant_id=tenant_id),
        db.checkins.count_documents(
            _checkin_match(tenant_id, None, state="approved")
        ),
        db.checkins.count_documents(
            _checkin_match(tenant_id, None, state="pending_approval")
        ),
    )
    awaiting = checked_in_sessions + approved_checkins

    visitors_today, public_checkins_today = await asyncio.gather(
        count_visit_sessions(today_filter),
        db.checkins.count_documents(
            {**checkin_base, "date_created": {"$gte": start_today}}
        ),
    )
    visitors_today += public_checkins_today
    check_outs_today, public_checkouts_today = await asyncio.gather(
        count_visit_sessions({**base, "check_out_time": {"$gte": start_today}}),
        db.checkins.count_documents(
            {
                **checkin_base,
                "state": "checked_out",
                "checked_out_at": {"$gte": start_today},
            }
        ),
    )
    check_outs_today += public_checkouts_today
    denials_today, rejected_checkins_today = await asyncio.gather(
        count_visit_sessions(
            {**base, "status": "denied", "date_created": {"$gte": start_today}}
        ),
        db.checkins.count_documents(
            {
                **checkin_base,
                "state": "rejected",
                "last_updated": {"$gte": start_today},
            }
        ),
    )
    denials_today += rejected_checkins_today

    # New vs returning today. Free tenants are capped at 50 visitors per
    # month, so this aggregation is trivially small.
    new_visitors_today = 0
    returning_visitors_today = 0
    visitor_ids: List[str] = []
    async for doc in db.visit_sessions.aggregate(
        [
            {"$match": today_filter},
            {"$group": {"_id": "$visitor_profile_id"}},
        ]
    ):
        if doc.get("_id"):
            visitor_ids.append(str(doc["_id"]))
    if visitor_ids:
        async for doc in db.visit_sessions.aggregate(
            [
                {"$match": {**base, "visitor_profile_id": {"$in": visitor_ids}}},
                {
                    "$group": {
                        "_id": "$visitor_profile_id",
                        "first_visit": {"$min": "$check_in_time"},
                    }
                },
            ]
        ):
            first = doc.get("first_visit") or 0
            if first >= start_today:
                new_visitors_today += 1
            else:
                returning_visitors_today += 1

    return TenantDashboardStats(
        plan_tier="free",
        upgrade_required_for=list(_FREE_UPGRADE_CAPABILITIES),
        total_visits=total_visits,
        total_visitors=total_visitors,
        total_departments=total_departments,
        total_system_users=total_system_users,
        currently_active=awaiting,
        awaiting_checkout=awaiting,
        pending_approval=pending_approval,
        visitors_today=visitors_today,
        check_ins_today=visitors_today,  # synonym; kept for symmetry with full payload
        check_outs_today=check_outs_today,
        new_visitors_today=new_visitors_today,
        returning_visitors_today=returning_visitors_today,
        denials_today=denials_today,
        role_view=role,
        last_updated=now,
        period={
            "now": now,
            "start_today": start_today,
        },
    )


async def get_dashboard_stats(
    tenant_id: str,
    department_id: Optional[str] = None,
    role: Optional[str] = None,
) -> TenantDashboardStats:
    """Return a comprehensive dashboard payload for ``tenant_id``.

    The same shape is returned for every role — ``role_view`` is just a
    label so the frontend can hide irrelevant sections without a
    secondary API call. Department-scoped views (``department_id``) are
    not cached at the precompute layer.

    Free-plan tenants get a slimmed payload (basic counters + today
    snapshot only) — see ``_build_basic_stats_for_free``. Paid tiers get
    the full payload with charts, time series, heatmaps, top-N, growth
    metrics, compliance, and audit sections populated.

    All paid-tier sections default to zero / empty list so a brand-new
    tenant with no data still gets a fully-shaped response."""
    if not tenant_id:
        return TenantDashboardStats(role_view=role, department_id=department_id)

    # Free tenants get the slim payload. Department slicing is also a
    # paid feature (Free is capped at one department) so we ignore
    # ``department_id`` on the slim path.
    tier = await _resolve_tier_safely(tenant_id)
    if tier == "free":
        return await _build_basic_stats_for_free(tenant_id, role=role)

    now = int(time.time())
    start_today = _start_of_today_ts(now)

    # Batched into ≤6-arg gathers so asyncio.gather's typed overloads
    # propagate each task's return type instead of collapsing to a union.
    overview, live, today, period, signups, durations = await asyncio.gather(
        _overview_counts(tenant_id, department_id),
        _live_state(tenant_id, department_id),
        _today_snapshot(tenant_id, department_id, start_today=start_today),
        _period_visits(tenant_id, department_id, now=now),
        _signup_periods(tenant_id, now=now),
        _duration_stats(tenant_id, department_id, start_today=start_today),
    )
    (
        segmentation,
        distributions,
        top_lists,
        time_series,
        heatmaps,
        real_time,
    ) = await asyncio.gather(
        _visitor_segmentation(tenant_id),
        _distributions(tenant_id, department_id),
        _top_lists(tenant_id, department_id),
        _time_series(tenant_id, department_id, now=now),
        _heatmaps(tenant_id, department_id, now=now),
        _real_time_samples(tenant_id, department_id, start_today=start_today),
    )
    compliance, audit, overdue = await asyncio.gather(
        _compliance_summary(tenant_id),
        _audit_summary(tenant_id, start_today=start_today, now=now),
        _overdue_checkouts(tenant_id, department_id),
    )

    raw = distributions.pop("_raw", {})
    appt_metrics = await _appointment_metrics(
        appointment_status_counts=raw.get("appointment_status", {}),
        total_visits=overview["total_visits"],
    )
    quality = await _quality_metrics(
        tenant_id,
        department_id,
        visit_status_counts=raw.get("visit_status", {}),
        verification_status_counts=raw.get("verification_status", {}),
        consent_counts=raw.get("consent", {}),
        kyc_counts=raw.get("kyc_status", {}),
        total_visits=overview["total_visits"],
        total_visitors=overview["total_visitors"],
    )

    visits_growth_dod = _growth(
        period["today_visits"], period["yesterday_visits"]
    )
    visits_growth_wow = _growth(
        period["visits_this_week"], period["visits_last_week"]
    )
    visits_growth_mom = _growth(
        period["visits_this_month"], period["visits_last_month"]
    )
    signups_growth_wow = _growth(
        signups["new_signups_this_week"], signups["new_signups_last_week"]
    )
    signups_growth_mom = _growth(
        signups["new_signups_this_month"], signups["new_signups_last_month"]
    )

    payload: Dict[str, Any] = {
        "plan_tier": tier,
        "upgrade_required_for": [],
        **overview,
        **live,
        **today,
        "visits_this_week": period["visits_this_week"],
        "visits_last_week": period["visits_last_week"],
        "visits_this_month": period["visits_this_month"],
        "visits_last_month": period["visits_last_month"],
        "visits_this_quarter": period["visits_this_quarter"],
        "visits_this_year": period["visits_this_year"],
        "visits_growth_dod": visits_growth_dod,
        "visits_growth_wow": visits_growth_wow,
        "visits_growth_mom": visits_growth_mom,
        "signups_growth_wow": signups_growth_wow,
        "signups_growth_mom": signups_growth_mom,
        **durations,
        "overdue_checkouts": overdue,
        "new_signups_today": signups["new_signups_today"],
        "new_signups_7d": signups["new_signups_7d"],
        "new_signups_30d": signups["new_signups_30d"],
        "new_signups_this_month": signups["new_signups_this_month"],
        "new_signups_last_month": signups["new_signups_last_month"],
        **segmentation,
        **distributions,
        **top_lists,
        **time_series,
        **heatmaps,
        **appt_metrics,
        **quality,
        **real_time,
        **compliance,
        **audit,
        "role_view": role,
        "department_id": department_id,
        "period": {
            "now": now,
            "start_today": start_today,
            "start_week": _start_of_week_ts(now),
            "start_month": _start_of_month_ts(now),
            "start_quarter": _start_of_quarter_ts(now),
            "start_year": _start_of_year_ts(now),
        },
        "last_updated": now,
    }
    return TenantDashboardStats(**payload)


# ─── Visitor log (unchanged behaviour, kept here for compatibility) ──


async def get_visitor_log(
    tenant_id: str,
    department_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    host_id: Optional[str] = None,
    verification_status: Optional[str] = None,
    start: int = 0,
    stop: int = 100,
) -> dict:
    filter_dict: Dict[str, Any] = {"tenant_id": tenant_id}
    if department_id:
        filter_dict["department_id"] = department_id
    if status_filter:
        filter_dict["status"] = status_filter
    if date_from or date_to:
        time_filter: Dict[str, Any] = {}
        if date_from:
            time_filter["$gte"] = date_from
        if date_to:
            time_filter["$lte"] = date_to
        filter_dict["check_in_time"] = time_filter
    if host_id:
        filter_dict["host_id"] = host_id
    if verification_status:
        filter_dict["verification_status"] = verification_status
    if search:
        filter_dict["$or"] = [
            {"visitor_name_snapshot": {"$regex": search, "$options": "i"}},
            {"company_snapshot": {"$regex": search, "$options": "i"}},
            {"host_name_snapshot": {"$regex": search, "$options": "i"}},
            {"purpose": {"$regex": search, "$options": "i"}},
        ]

    sessions = await get_visit_sessions(filter_dict=filter_dict, start=start, stop=stop)
    total = await count_visit_sessions(filter_dict)
    return {"items": sessions, "total": total, "start": start, "stop": stop}
