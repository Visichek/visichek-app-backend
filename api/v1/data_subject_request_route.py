from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Request, status

from core.bulk import enqueue_bulk_write
from core.database import db
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.data_subject_request_schema import DSRCreate, DSRUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.data_subject_request_service import (
    retrieve_dsr_by_id,
    retrieve_dsrs,
)

router = APIRouter(prefix="/dsr", tags=["Data Subject Requests"])
_dpo_roles = verify_system_user_token("super_admin", "dpo")


_DSR_STATUSES = frozenset({"pending", "in_progress", "completed", "rejected"})
_DSR_TYPES = frozenset({"access", "correction", "deletion", "consent_withdrawal"})


DSR_LIST_SPEC = ListSpec(
    sortable_fields=frozenset({"date_created", "sla_deadline", "status", "type"}),
    default_sort=(("date_created", -1),),
    search_fields=("subject_email", "subject_name", "request_number"),
    filters={
        "status": FilterDef(name="status", multi=True, allowed_values=_DSR_STATUSES),
        "type": FilterDef(name="type", multi=True, allowed_values=_DSR_TYPES),
        "slaState": FilterDef(
            name="slaState",
            allowed_values=frozenset({"on_track", "at_risk", "breached"}),
        ),
    },
    range_filters={"createdAt": "date_created"},
    facet_fields=frozenset({"status"}),
)


def _is_default_dsr_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(DSR_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(DSR_LIST_SPEC.default_limit)


def _map_dsr_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _dsr_status_facet(
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


@router.post("")
@document_response(
    message="DSR creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create DSR (async)",
    description="Enqueue a data subject request. The ID is pre-assigned so the submitter can poll status.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        422: "Invalid payload",
    },
)
async def create_dsr_endpoint(
    dsr_data: DSRCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    payload = dsr_data.model_dump(exclude_none=True)
    if principal.tenant_id:
        payload["tenant_id"] = principal.tenant_id
    payload["admin_id"] = principal.user_id
    return await enqueue_write(
        writer_key="dsr.create",
        payload=payload,
        resource_type="dsr",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="DSRs fetched successfully",
    summary="List DSRs",
    description="First-page served from the per-tenant precompute cache.",
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions"},
)
async def list_dsrs(
    request: Request,
    principal: AuthPrincipal = Depends(_dpo_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return {"items": [], "meta": {"total": 0, "skip": 0, "limit": 25, "hasMore": False}}
    if _is_default_dsr_listing(request):
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="dsr.list",
            ttl=60,
            loader=lambda: _load_dsrs_for_tenant(tenant_id),
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: DSR_LIST_SPEC.default_limit]
        return {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": DSR_LIST_SPEC.default_limit,
                "hasMore": len(items) > DSR_LIST_SPEC.default_limit,
            },
        }
    query = parse_list_query(request, DSR_LIST_SPEC)
    return await run_list(
        collection=db.dsrs,
        query=query,
        base_filter={"tenant_id": tenant_id},
        map_doc=_map_dsr_doc,
        facet_runner=_dsr_status_facet,
    )


async def _load_dsrs_for_tenant(tenant_id: str) -> List[Any]:
    dsrs = await retrieve_dsrs(tenant_id=tenant_id, start=0, stop=100)
    return [
        d.model_dump(mode="json", by_alias=True) if hasattr(d, "model_dump") else d
        for d in dsrs
    ]


@router.get("/{dsr_id}")
@document_response(
    message="DSR fetched successfully",
    summary="Get DSR",
    description="Retrieve a specific DSR by ID.",
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        404: "DSR not found",
    },
)
async def get_dsr_endpoint(dsr_id: str, principal: AuthPrincipal = Depends(_dpo_roles)):
    tenant_id = principal.tenant_id or ""
    return await get_or_compute_entity(
        entity_type="dsr",
        entity_id=dsr_id,
        loader=lambda: retrieve_dsr_by_id(dsr_id=dsr_id, tenant_id=tenant_id),
    )


@router.patch("/{dsr_id}")
@document_response(
    message="DSR update queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update DSR (async)",
    description="Enqueue a partial DSR update.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        422: "Invalid payload",
    },
)
async def update_dsr_endpoint(
    dsr_id: str,
    dsr_data: DSRUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    tenant_id = principal.tenant_id or ""
    payload = dsr_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    return await enqueue_write(
        writer_key="dsr.update",
        payload=payload,
        resource_type="dsr",
        resource_id=dsr_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


# ─── Status-transition single endpoints ───────────────────────────────


def _dsr_transition_payload(
    *,
    tenant_id: str,
    new_status: str,
    extras: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"tenant_id": tenant_id, "status": new_status}
    if extras:
        body.update(extras)
    return body


# ─── Bulk endpoints ───────────────────────────────────────────────────


@router.post("/bulk/acknowledge")
@document_response(
    message="Bulk DSR acknowledge queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk acknowledge DSRs",
)
async def bulk_acknowledge_dsr(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/dsr/bulk/acknowledge",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="dsr.bulk_acknowledge",
        ids=payload.get("ids", []),
        resource_type="dsr",
        extras={"tenant_scope": tenant_id},
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/dsr/bulk/acknowledge",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/reject")
@document_response(
    message="Bulk DSR reject queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk reject DSRs",
)
async def bulk_reject_dsr(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/dsr/bulk/reject",
        body=payload,
    )
    if hit is not None:
        return hit.response
    extras = {
        "tenant_scope": tenant_id,
        "reason": str(payload.get("reason") or "")[:2000],
    }
    response = await enqueue_bulk_write(
        writer_key="dsr.bulk_reject",
        ids=payload.get("ids", []),
        resource_type="dsr",
        extras=extras,
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/dsr/bulk/reject",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/{dsr_id}/acknowledge")
@document_response(
    message="DSR acknowledge queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Acknowledge DSR (async)",
)
async def acknowledge_dsr_endpoint(
    dsr_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    tenant_id = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="dsr.update",
        payload=_dsr_transition_payload(tenant_id=tenant_id, new_status="in_progress"),
        resource_type="dsr",
        resource_id=dsr_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{dsr_id}/complete")
@document_response(
    message="DSR completion queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Mark DSR completed (async)",
)
async def complete_dsr_endpoint(
    dsr_id: str,
    request: Request,
    payload: dict = Body(default_factory=dict),
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    tenant_id = principal.tenant_id or ""
    extras: dict[str, Any] = {}
    if "resolution" in payload:
        extras["resolution"] = str(payload["resolution"])[:2000]
    return await enqueue_write(
        writer_key="dsr.update",
        payload=_dsr_transition_payload(
            tenant_id=tenant_id, new_status="completed", extras=extras
        ),
        resource_type="dsr",
        resource_id=dsr_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{dsr_id}/reject")
@document_response(
    message="DSR reject queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reject DSR (async)",
)
async def reject_dsr_endpoint(
    dsr_id: str,
    request: Request,
    payload: dict = Body(default_factory=dict),
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    tenant_id = principal.tenant_id or ""
    extras: dict[str, Any] = {}
    if "reason" in payload:
        extras["rejection_reason"] = str(payload["reason"])[:2000]
    return await enqueue_write(
        writer_key="dsr.update",
        payload=_dsr_transition_payload(
            tenant_id=tenant_id, new_status="rejected", extras=extras
        ),
        resource_type="dsr",
        resource_id=dsr_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
