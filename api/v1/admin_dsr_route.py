"""Application-admin facing Data Subject Request (DSR) oversight routes.

Platform admins see every DSR across all tenants for cross-tenant
compliance oversight. These endpoints are READ-ONLY — actual DSR
processing (acknowledge / complete / reject) remains a tenant
responsibility through ``/v1/dsr/*`` (DPO + super_admin only).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from core.database import db
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute, register_precompute
from core.response_envelope import document_response
from schemas.admin_schema import AdminOut
from security.account_status_check import (
    check_admin_account_status_and_permissions,
)
from services.data_subject_request_service import (
    compute_admin_dsr_stats,
    retrieve_all_dsrs,
    retrieve_dsr_by_id_admin,
    retrieve_dsrs_approaching_sla,
    retrieve_dsrs_breached_sla,
)
from services.notification_service import (
    extract_resource_ids,
    schedule_resource_read_receipt,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admins/dsr", tags=["Application Admin DSR"])


_DSR_STATUSES = frozenset({"pending", "in_progress", "completed", "rejected"})
_DSR_TYPES = frozenset({"access", "correction", "deletion", "consent_withdrawal"})


ADMIN_DSR_LIST_SPEC = ListSpec(
    sortable_fields=frozenset({"date_created", "sla_deadline", "status"}),
    default_sort=(("date_created", -1),),
    search_fields=("notes",),
    filters={
        "status": FilterDef(name="status", multi=True, allowed_values=_DSR_STATUSES),
        "requestType": FilterDef(
            name="requestType",
            mongo_field="request_type",
            multi=True,
            allowed_values=_DSR_TYPES,
        ),
        "tenantId": FilterDef(name="tenantId", mongo_field="tenant_id"),
    },
    range_filters={"createdAt": "date_created", "slaDeadline": "sla_deadline"},
    facet_fields=frozenset({"status"}),
)


def _is_default_admin_dsr_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(ADMIN_DSR_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(
        ADMIN_DSR_LIST_SPEC.default_limit
    )


def _map_dsr_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _admin_dsr_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for v in _DSR_STATUSES:
        out[v] = await collection.count_documents({**base, "status": v})
    out["all"] = sum(out.values())
    return out


async def _load_all_dsrs() -> list[dict[str, Any]]:
    rows = await retrieve_all_dsrs(start=0, stop=100)
    return [r.model_dump(mode="json", by_alias=True) for r in rows]


def _auto_read_admin_dsrs(admin: AdminOut, result: Any) -> None:
    schedule_resource_read_receipt(
        user_id=admin.id or "",
        user_role="admin",
        resource_type="dsr",
        resource_ids=extract_resource_ids(result),
    )


# ── List + filter ─────────────────────────────────────────────────────


@router.get("")
@document_response(
    message="DSRs fetched successfully",
    summary="List all DSRs (admin)",
    description=(
        "Cross-tenant DSR list. Page 1 with no filters is served from the "
        "GLOBAL precompute cache. Filters supported: ``status``, "
        "``requestType``, ``tenantId``, plus ``createdAt`` / ``slaDeadline`` "
        "range filters."
    ),
    include_meta=True,
)
async def admin_list_dsrs(
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    if _is_default_admin_dsr_listing(request):
        cached = await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="dsr.admin_list",
            ttl=60,
            loader=_load_all_dsrs,
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: ADMIN_DSR_LIST_SPEC.default_limit]
        result = {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": ADMIN_DSR_LIST_SPEC.default_limit,
                "hasMore": len(items) > ADMIN_DSR_LIST_SPEC.default_limit,
            },
        }
        _auto_read_admin_dsrs(admin, result)
        return result
    query = parse_list_query(request, ADMIN_DSR_LIST_SPEC)
    result = await run_list(
        collection=db.data_subject_requests,
        query=query,
        map_doc=_map_dsr_doc,
        facet_runner=_admin_dsr_status_facet,
    )
    _auto_read_admin_dsrs(admin, result)
    return result


# ── SLA queues ────────────────────────────────────────────────────────


@router.get("/approaching-sla")
@document_response(
    message="DSRs approaching SLA fetched",
    description=(
        "Open DSRs whose ``sla_deadline`` elapses within the next ``window`` "
        "seconds (default 86400 = 24h). Sorted by deadline ascending so the "
        "most urgent surface first."
    ),
    summary="DSRs nearing SLA deadline (admin)",
    include_meta=True,
)
async def admin_dsrs_approaching_sla(
    window: Annotated[int, Query(ge=60, le=14 * 86400)] = 86400,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0, le=500)] = 100,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    _ = admin
    return await retrieve_dsrs_approaching_sla(
        window_seconds=window, start=start, stop=stop
    )


@router.get("/breached-sla")
@document_response(
    message="DSRs past SLA fetched",
    description=(
        "Open DSRs whose ``sla_deadline`` has already passed — i.e. the "
        "tenant has missed the legal window. These are the highest-priority "
        "items for platform-admin escalation."
    ),
    summary="DSRs past SLA deadline (admin)",
    include_meta=True,
)
async def admin_dsrs_breached_sla(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0, le=500)] = 100,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    _ = admin
    return await retrieve_dsrs_breached_sla(start=start, stop=stop)


# ── Stats ─────────────────────────────────────────────────────────────


@router.get("/stats")
@document_response(
    message="DSR stats computed",
    description=(
        "Aggregate counts across every tenant for the platform-admin "
        "compliance dashboard. Returns total + breakdowns by status and "
        "request type, plus SLA-at-risk and SLA-breached counts."
    ),
    summary="Platform-wide DSR stats (admin)",
)
async def admin_dsr_stats(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    _ = admin
    return await compute_admin_dsr_stats()


# ── Detail ────────────────────────────────────────────────────────────


@router.get("/{dsr_id}")
@document_response(
    message="DSR fetched successfully",
    summary="Get DSR (admin)",
    description=(
        "Cross-tenant DSR fetch — the tenant filter is intentionally "
        "omitted so application admins can retrieve any DSR by id."
    ),
    response_codes={404: "DSR not found"},
)
async def admin_get_dsr(
    dsr_id: str,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    result = await get_or_compute_entity(
        entity_type="dsr_admin",
        entity_id=dsr_id,
        loader=lambda: retrieve_dsr_by_id_admin(dsr_id=dsr_id),
    )
    schedule_resource_read_receipt(
        user_id=admin.id or "",
        user_role="admin",
        resource_type="dsr",
        resource_ids=[dsr_id],
    )
    return result


# ── Precompute loader ─────────────────────────────────────────────────


# Registering against PrecomputeScope.GLOBAL means the fanout fills this
# cache once per cycle instead of once per tenant — admins see a single
# shared rollup.
@register_precompute("dsr.admin_list", scope=PrecomputeScope.GLOBAL)
async def _precompute_admin_dsr_list(_scope_id: str) -> list[dict[str, Any]]:
    return await _load_all_dsrs()
