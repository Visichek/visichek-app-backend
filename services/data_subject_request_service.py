import asyncio
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


async def _enrich_dsr(dsr: DSROut) -> DSROut:
    """Embed the three external-id snapshots on a DSR concurrently.

    Resolves visitor_profile_id, admin_id, and visit_session_id in one
    ``asyncio.gather`` so no read path serves a bare id (External ID Summary
    Fields rule). Each resolver is best-effort and returns ``None`` rather than
    raising, so enrichment never breaks the primary response.
    """
    from services.summary_resolver import (
        resolve_user_summary,
        resolve_visit_session_summary,
        resolve_visitor_profile_summary,
    )

    visitor, admin, session = await asyncio.gather(
        resolve_visitor_profile_summary(dsr.visitor_profile_id),
        resolve_user_summary(dsr.admin_id, None),
        resolve_visit_session_summary(dsr.visit_session_id),
    )
    dsr.visitor_profile_summary = visitor
    dsr.admin_summary = admin
    dsr.visit_session_summary = session
    return dsr


async def _enrich_dsrs(dsrs: List[DSROut]) -> List[DSROut]:
    """Enrich a page of DSRs, fanning out the per-row resolution concurrently."""
    if dsrs:
        await asyncio.gather(*[_enrich_dsr(d) for d in dsrs])
    return dsrs


async def enrich_dsr_docs(items: List[dict[str, Any]]) -> List[dict[str, Any]]:
    """Attach the three summaries to raw ``run_list`` dicts (filtered list path).

    ``run_list`` maps Mongo docs synchronously via ``_map_dsr_doc``, which
    cannot ``await`` a resolver — so status-tab / search / sort results would
    otherwise come back as bare ids. This async post-pass re-parses each dict
    into ``DSROut``, enriches it, and re-dumps it by alias so the filtered list
    matches the precompute/default list shape. At 25 rows/page the per-row
    fan-out is the same cost profile as ``retrieve_dsrs`` already pays.
    """
    if not items:
        return items
    enriched: List[dict[str, Any]] = []
    for item in items:
        try:
            dsr = DSROut.model_validate(item)
            await _enrich_dsr(dsr)
            enriched.append(dsr.model_dump(mode="json", by_alias=True))
        except Exception:
            # Never let enrichment break the list — fall back to the raw doc.
            enriched.append(item)
    return enriched


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
    return await _enrich_dsr(result)


async def retrieve_dsrs(tenant_id: str, start=0, stop=100) -> List[DSROut]:
    dsrs = await get_dsrs(filter_dict={"tenant_id": tenant_id}, start=start, stop=stop)
    return await _enrich_dsrs(dsrs)


async def update_dsr_by_id(dsr_id: str, tenant_id: str, dsr_data: DSRUpdate) -> DSROut:
    if not ObjectId.is_valid(dsr_id):
        raise HTTPException(status_code=400, detail="Invalid DSR ID format")
    # Stamp resolved_at when the request reaches a terminal state so the
    # compliance trail records when it was closed out. Covers both the single
    # complete/reject transitions and the bulk reject path (both land here).
    if dsr_data.status in ("completed", "rejected") and dsr_data.resolved_at is None:
        dsr_data.resolved_at = int(time.time())
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
    dsrs = await get_dsrs(filter_dict=filter_dict, start=start, stop=stop)
    return await _enrich_dsrs(dsrs)


async def retrieve_dsr_by_id_admin(dsr_id: str) -> DSROut:
    """Admin-scope DSR fetch — tenant_id is not in the filter."""
    if not ObjectId.is_valid(dsr_id):
        raise HTTPException(status_code=400, detail="Invalid DSR ID format")
    result = await get_dsr({"_id": ObjectId(dsr_id)})
    if not result:
        raise HTTPException(status_code=404, detail="Data subject request not found")
    return await _enrich_dsr(result)


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
