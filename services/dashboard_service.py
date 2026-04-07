from __future__ import annotations

from core.database import db
from repositories.visit_session_repo import (
    get_visit_sessions,
    get_active_visitors,
    get_visitor_session_stats,
    count_visit_sessions,
)


async def get_dashboard_stats(tenant_id: str, department_id: str = None) -> dict:
    stats = await get_visitor_session_stats(tenant_id=tenant_id, department_id=department_id)

    # Get active count
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
