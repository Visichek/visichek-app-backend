from __future__ import annotations

import time

from core.database import db
from repositories.visit_session_repo import (
    get_visit_sessions,
    get_active_visitors,
    get_visitor_session_stats,
    count_visit_sessions,
)
from repositories.incident_log_repo import (
    get_incident_logs,
    get_incidents_approaching_deadline,
)
from repositories.appointment_repo import count_appointments, get_appointments


def _start_of_today_ts() -> int:
    now = int(time.time())
    return now - (now % 86400)


async def _receptionist_stats(tenant_id: str, department_id: str | None) -> dict:
    start_today = _start_of_today_ts()
    base = {"tenant_id": tenant_id}
    if department_id:
        base["department_id"] = department_id

    recent_check_ins = await get_visit_sessions(
        filter_dict=base, start=0, stop=20,
    )
    active = await get_active_visitors(tenant_id=tenant_id, department_id=department_id)
    visitors_today = await count_visit_sessions(
        {**base, "check_in_time": {"$gte": start_today}}
    )
    appt_filter = {"tenant_id": tenant_id, "scheduled_datetime": {"$gte": start_today, "$lt": start_today + 86400}}
    if department_id:
        appt_filter["department_id"] = department_id
    expected_today = await count_appointments(appt_filter)
    upcoming_appointments = await get_appointments(
        filter_dict=appt_filter, start=0, stop=10,
    )

    return {
        "role_view": "receptionist",
        "currently_active": len(active),
        "active_visitors": active,
        "visitors_today": visitors_today,
        "expected_today": expected_today,
        "recent_check_ins": recent_check_ins,
        "upcoming_appointments": upcoming_appointments,
        "last_updated": int(time.time()),
    }


async def _dept_admin_stats(tenant_id: str, department_id: str | None) -> dict:
    stats = await get_visitor_session_stats(tenant_id=tenant_id, department_id=department_id)
    active = await get_active_visitors(tenant_id=tenant_id, department_id=department_id)
    incident_filter = {"tenant_id": tenant_id}
    if department_id:
        incident_filter["department_id"] = department_id
    open_incidents = await db.incident_logs.count_documents({**incident_filter, "status": {"$ne": "resolved"}})
    return {
        "role_view": "dept_admin",
        **stats,
        "currently_active": len(active),
        "open_incidents": open_incidents,
        "last_updated": int(time.time()),
    }


async def _super_admin_stats(tenant_id: str) -> dict:
    stats = await get_visitor_session_stats(tenant_id=tenant_id)
    active = await get_active_visitors(tenant_id=tenant_id)
    total_departments = await db.departments.count_documents({"tenant_id": tenant_id})
    total_branches = await db.branches.count_documents({"tenant_id": tenant_id})
    total_system_users = await db.system_users.count_documents({"tenant_id": tenant_id})
    total_incidents = await db.incident_logs.count_documents({"tenant_id": tenant_id})
    open_incidents = await db.incident_logs.count_documents({"tenant_id": tenant_id, "status": {"$ne": "resolved"}})
    start_today = _start_of_today_ts()
    visitors_today = await count_visit_sessions({"tenant_id": tenant_id, "check_in_time": {"$gte": start_today}})
    return {
        "role_view": "super_admin",
        **stats,
        "currently_active": len(active),
        "total_departments": total_departments,
        "total_branches": total_branches,
        "total_system_users": total_system_users,
        "total_incidents": total_incidents,
        "open_incidents": open_incidents,
        "visitors_today": visitors_today,
        "last_updated": int(time.time()),
    }


async def _security_officer_stats(tenant_id: str) -> dict:
    open_incidents = await db.incident_logs.count_documents({"tenant_id": tenant_id, "status": {"$ne": "resolved"}})
    total_incidents = await db.incident_logs.count_documents({"tenant_id": tenant_id})
    approaching = await get_incidents_approaching_deadline(tenant_id=tenant_id, start=0, stop=20)
    recent_incidents = await get_incident_logs({"tenant_id": tenant_id}, start=0, stop=20)
    active = await get_active_visitors(tenant_id=tenant_id)
    return {
        "role_view": "security_officer",
        "open_incidents": open_incidents,
        "total_incidents": total_incidents,
        "incidents_approaching_deadline": approaching,
        "recent_incidents": recent_incidents,
        "currently_active_visitors": len(active),
        "last_updated": int(time.time()),
    }


async def _auditor_stats(tenant_id: str) -> dict:
    total_audit_events = await db.audit_trail.count_documents({"tenant_id": tenant_id})
    start_today = _start_of_today_ts()
    events_today = await db.audit_trail.count_documents({"tenant_id": tenant_id, "timestamp": {"$gte": start_today}})
    total_incidents = await db.incident_logs.count_documents({"tenant_id": tenant_id})
    total_visits = await count_visit_sessions({"tenant_id": tenant_id})
    recent_events = await db.audit_trail.find({"tenant_id": tenant_id}).sort("timestamp", -1).limit(20).to_list(20)
    for e in recent_events:
        e["_id"] = str(e.get("_id"))
    return {
        "role_view": "auditor",
        "total_audit_events": total_audit_events,
        "audit_events_today": events_today,
        "total_incidents": total_incidents,
        "total_visit_sessions": total_visits,
        "recent_audit_events": recent_events,
        "last_updated": int(time.time()),
    }


async def _dpo_stats(tenant_id: str) -> dict:
    dsr_total = await db.data_subject_requests.count_documents({"tenant_id": tenant_id})
    dsr_open = await db.data_subject_requests.count_documents({"tenant_id": tenant_id, "status": {"$nin": ["completed", "rejected"]}})
    privacy_notices = await db.privacy_notices.count_documents({"tenant_id": tenant_id})
    sub_processors = await db.sub_processors.count_documents({"tenant_id": tenant_id})
    retention_policies = await db.retention_policies.count_documents({"tenant_id": tenant_id})
    open_incidents = await db.incident_logs.count_documents({"tenant_id": tenant_id, "status": {"$ne": "resolved"}})
    return {
        "role_view": "dpo",
        "data_subject_requests_total": dsr_total,
        "data_subject_requests_open": dsr_open,
        "privacy_notices": privacy_notices,
        "sub_processors": sub_processors,
        "retention_policies": retention_policies,
        "open_incidents": open_incidents,
        "last_updated": int(time.time()),
    }


async def get_dashboard_stats(
    tenant_id: str,
    department_id: str = None,
    role: str | None = None,
) -> dict:
    if role == "receptionist":
        return await _receptionist_stats(tenant_id, department_id)
    if role == "dept_admin":
        return await _dept_admin_stats(tenant_id, department_id)
    if role == "super_admin":
        return await _super_admin_stats(tenant_id)
    if role == "security_officer":
        return await _security_officer_stats(tenant_id)
    if role == "auditor":
        return await _auditor_stats(tenant_id)
    if role == "dpo":
        return await _dpo_stats(tenant_id)

    stats = await get_visitor_session_stats(tenant_id=tenant_id, department_id=department_id)
    active = await get_active_visitors(tenant_id=tenant_id, department_id=department_id)
    stats["currently_active"] = len(active)
    return stats


async def get_visitor_log(
    tenant_id: str,
    department_id: str = None,
    status_filter: str = None,
    search: str = None,
    date_from: int = None,
    date_to: int = None,
    host_id: str = None,
    verification_status: str = None,
    start: int = 0,
    stop: int = 100,
) -> list:
    filter_dict = {"tenant_id": tenant_id}
    if department_id:
        filter_dict["department_id"] = department_id
    if status_filter:
        filter_dict["status"] = status_filter
    if date_from or date_to:
        time_filter = {}
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
