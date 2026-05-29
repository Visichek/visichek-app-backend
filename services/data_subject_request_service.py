from bson import ObjectId
from fastapi import HTTPException
from typing import Any, List, Optional
import time

from repositories.data_subject_request_repo import (
    count_dsrs,
    create_dsr,
    get_dsr,
    get_dsrs,
    update_dsr,
)
from schemas.data_subject_request_schema import DSRCreate, DSRUpdate, DSROut


async def add_dsr(
    dsr_data: DSRCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> DSROut:
    # Set SLA deadline to 30 days from now
    if not dsr_data.sla_deadline:
        dsr_data.sla_deadline = int(time.time()) + (30 * 86400)
    return await create_dsr(dsr_data, preassigned_id=preassigned_id)


async def retrieve_dsr_by_id(dsr_id: str, tenant_id: str) -> DSROut:
    if not ObjectId.is_valid(dsr_id):
        raise HTTPException(status_code=400, detail="Invalid DSR ID format")
    result = await get_dsr({"_id": ObjectId(dsr_id), "tenant_id": tenant_id})
    if not result:
        raise HTTPException(status_code=404, detail="Data subject request not found")
    return result


async def retrieve_dsrs(tenant_id: str, start=0, stop=100) -> List[DSROut]:
    return await get_dsrs(filter_dict={"tenant_id": tenant_id}, start=start, stop=stop)


async def update_dsr_by_id(dsr_id: str, tenant_id: str, dsr_data: DSRUpdate) -> DSROut:
    if not ObjectId.is_valid(dsr_id):
        raise HTTPException(status_code=400, detail="Invalid DSR ID format")
    result = await update_dsr(
        {"_id": ObjectId(dsr_id), "tenant_id": tenant_id}, dsr_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Data subject request not found or update failed"
        )
    return result


# ── Platform admin oversight helpers ──────────────────────────────────


async def retrieve_all_dsrs(
    start: int = 0,
    stop: int = 100,
    *,
    status: Optional[str] = None,
    request_type: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> List[DSROut]:
    """Cross-tenant DSR list for application admin oversight."""
    filter_dict: dict[str, Any] = {}
    if status:
        filter_dict["status"] = status
    if request_type:
        filter_dict["request_type"] = request_type
    if tenant_id:
        filter_dict["tenant_id"] = tenant_id
    return await get_dsrs(filter_dict=filter_dict, start=start, stop=stop)


async def retrieve_dsr_by_id_admin(dsr_id: str) -> DSROut:
    """Admin-scope DSR fetch — tenant_id is not in the filter."""
    if not ObjectId.is_valid(dsr_id):
        raise HTTPException(status_code=400, detail="Invalid DSR ID format")
    result = await get_dsr({"_id": ObjectId(dsr_id)})
    if not result:
        raise HTTPException(status_code=404, detail="Data subject request not found")
    return result


async def retrieve_dsrs_approaching_sla(
    *,
    window_seconds: int = 86400,
    start: int = 0,
    stop: int = 100,
) -> List[DSROut]:
    """Open DSRs whose SLA elapses within ``window_seconds`` (default 24h)."""
    now = int(time.time())
    horizon = now + max(window_seconds, 0)
    filter_dict = {
        "status": {"$in": ["pending", "in_progress"]},
        "sla_deadline": {"$gte": now, "$lte": horizon},
    }
    return await get_dsrs(filter_dict=filter_dict, start=start, stop=stop)


async def retrieve_dsrs_breached_sla(
    start: int = 0,
    stop: int = 100,
) -> List[DSROut]:
    """Open DSRs whose SLA deadline has already passed."""
    now = int(time.time())
    filter_dict = {
        "status": {"$in": ["pending", "in_progress"]},
        "sla_deadline": {"$lt": now},
    }
    return await get_dsrs(filter_dict=filter_dict, start=start, stop=stop)


async def compute_admin_dsr_stats() -> dict[str, Any]:
    """Aggregate counts the admin oversight dashboard renders.

    Returns a dict with: total, breakdown by ``status``, breakdown by
    ``request_type``, count of SLA-at-risk (open, due within 24h), and
    count of SLA-breached (open, past deadline). Cheap — five
    ``count_documents`` calls.
    """
    now = int(time.time())
    soon = now + 86400

    statuses = ("pending", "in_progress", "completed", "rejected")
    types = ("access", "correction", "deletion", "consent_withdrawal")

    total = await count_dsrs({})
    by_status: dict[str, int] = {}
    for s in statuses:
        by_status[s] = await count_dsrs({"status": s})
    by_type: dict[str, int] = {}
    for t in types:
        by_type[t] = await count_dsrs({"request_type": t})

    at_risk = await count_dsrs(
        {
            "status": {"$in": ["pending", "in_progress"]},
            "sla_deadline": {"$gte": now, "$lte": soon},
        }
    )
    breached = await count_dsrs(
        {
            "status": {"$in": ["pending", "in_progress"]},
            "sla_deadline": {"$lt": now},
        }
    )

    return {
        "total": total,
        "by_status": by_status,
        "by_request_type": by_type,
        "sla_at_risk": at_risk,
        "sla_breached": breached,
    }
