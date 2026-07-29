import time
from typing import Any, List, Optional  # noqa: F401

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Request, status

from core.bulk import enqueue_bulk_write
from core.database import db
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, coerce_bool, parse_list_query
from core.list_runner import run_list
from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.incident_log_schema import IncidentLogCreateRequest, IncidentLogUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal, TENANT_USER_ROLES
from services.incident_service import (
    retrieve_incident_by_id,
    retrieve_incidents,
    retrieve_incidents_approaching_deadline,
)
from services.notification_service import (
    extract_resource_ids,
    schedule_resource_read_receipt,
)

router = APIRouter(prefix="/incidents", tags=["Incidents"])

# Reporting (file) and reading the incident log are open to EVERY tenant role
# — any staff member can raise an incident and see what's on record. Triage
# (status transitions + NDPC-notified marking) stays restricted to the
# security / compliance roles so the regulatory trail can't be altered by
# arbitrary staff.
_report_roles = verify_system_user_token(*TENANT_USER_ROLES)
_triage_roles = verify_system_user_token("super_admin", "security_officer", "dpo")


_INCIDENT_STATUSES = frozenset(
    {"open", "investigating", "contained", "reported_to_ndpc", "closed"}
)
_INCIDENT_TYPES = frozenset(
    {
        "data_breach",
        "unauthorized_access",
        "data_export_exposure",
        "device_loss",
        "misconfiguration",
        "third_party",
    }
)
_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})

_DEADLINE_WINDOW_SECONDS = 24 * 60 * 60


def _approaching_builder(values):
    if not values or values[0].lower() != "true":
        return {}
    now = int(time.time())
    return {
        "notification_deadline": {"$gte": now, "$lte": now + _DEADLINE_WINDOW_SECONDS}
    }


INCIDENTS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"date_created", "notification_deadline", "risk_level", "status"}
    ),
    default_sort=(("date_created", -1),),
    # ``summary`` is not a field on incident_logs — only ``description`` is.
    search_fields=("description",),
    filters={
        "status": FilterDef(
            name="status", multi=True, allowed_values=_INCIDENT_STATUSES
        ),
        "incidentType": FilterDef(
            name="incidentType",
            mongo_field="incident_type",
            allowed_values=_INCIDENT_TYPES,
        ),
        "riskLevel": FilterDef(
            name="riskLevel", mongo_field="risk_level", allowed_values=_RISK_LEVELS
        ),
        "ndpcNotified": FilterDef(
            name="ndpcNotified", mongo_field="ndpc_notified", coerce=coerce_bool
        ),
        "branchId": FilterDef(name="branchId", mongo_field="branch_id"),
        "approachingDeadline": FilterDef(
            name="approachingDeadline", builder=_approaching_builder
        ),
    },
    range_filters={"dateCreated": "date_created"},
    facet_fields=frozenset({"status"}),
)


def _is_default_inc_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(INCIDENTS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(INCIDENTS_LIST_SPEC.default_limit)


def _map_inc_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _inc_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for v in _INCIDENT_STATUSES:
        out[v] = await collection.count_documents({**base, "status": v})
    out["all"] = sum(out.values())
    return out


@router.post("")
@document_response(
    message="Incident creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a security incident creation.",
    summary="Create incident (async)",
    success_example={
        "id": "6789abcdef0123456789abcd",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        422: "Unprocessable entity - validation failed",
    },
)
async def create_incident(
    log_data: IncidentLogCreateRequest,
    request: Request,
    principal: AuthPrincipal = Depends(_report_roles),
):
    payload = log_data.model_dump(exclude_none=True)
    payload["tenant_id"] = principal.tenant_id or ""
    payload["reported_by"] = principal.user_id
    # Branch is resolved from the caller's token (branch-scoped roles are
    # pinned to their own branch; super_admins may pass an explicit one,
    # else HQ). Stored on the incident for attribution and branchId
    # filtering only — incident reads are not branch-isolated, so this
    # tag does not by itself enforce access separation.
    from services.branch_service import resolve_branch_for_principal

    payload["branch_id"] = await resolve_branch_for_principal(
        principal,
        principal.tenant_id or "",
        explicit_branch_id=payload.get("branch_id"),
    )
    request_id = getattr(request.state, "request_id", None)
    payload["_actor_id"] = principal.user_id
    payload["_actor_role"] = principal.role
    payload["_request_id"] = request_id
    return await enqueue_write(
        writer_key="incident.create",
        payload=payload,
        resource_type="incident",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )


@router.get("")
@document_response(
    message="Incidents fetched successfully",
    description="First page served from the per-tenant precompute cache.",
    summary="List incidents",
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_incidents(
    request: Request,
    principal: AuthPrincipal = Depends(_report_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return {
            "items": [],
            "meta": {"total": 0, "skip": 0, "limit": 25, "hasMore": False},
        }
    if _is_default_inc_listing(request):
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="incidents.list",
            ttl=60,
            loader=lambda: _load_incidents_for_tenant(tenant_id),
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: INCIDENTS_LIST_SPEC.default_limit]
        result = {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": INCIDENTS_LIST_SPEC.default_limit,
                "hasMore": len(items) > INCIDENTS_LIST_SPEC.default_limit,
            },
        }
        _auto_read_incidents(principal, result)
        return result
    query = parse_list_query(request, INCIDENTS_LIST_SPEC)
    result = await run_list(
        collection=db.incident_logs,
        query=query,
        base_filter={"tenant_id": tenant_id},
        map_doc=_map_inc_doc,
        facet_runner=_inc_status_facet,
    )
    _auto_read_incidents(principal, result)
    return result


def _auto_read_incidents(principal: AuthPrincipal, result: Any) -> None:
    """Auto-mark incident notifications read for ids surfaced in this read."""
    schedule_resource_read_receipt(
        user_id=principal.user_id,
        user_role=principal.role,
        resource_type="incident",
        resource_ids=extract_resource_ids(result),
    )


async def _load_incidents_for_tenant(tenant_id: str) -> List[Any]:
    incidents = await retrieve_incidents(tenant_id=tenant_id, start=0, stop=100)
    return [
        i.model_dump(mode="json", by_alias=True) if hasattr(i, "model_dump") else i
        for i in incidents
    ]


@router.get("/approaching-deadline")
@document_response(
    message="Incidents approaching notification deadline fetched",
    description="Served from the per-tenant precompute cache.",
    summary="List incidents approaching 72h NDPC notification deadline",
    include_meta=True,
)
async def get_approaching_deadline_incidents(
    request: Request,
    principal: AuthPrincipal = Depends(_report_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    qp = request.query_params
    skip = int(qp.get("skip", "0"))
    limit = int(qp.get("limit", "100"))
    if skip == 0 and limit == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="incidents.approaching_deadline",
            ttl=60,
            loader=lambda: _load_approaching_deadline(tenant_id),
        )
    return await retrieve_incidents_approaching_deadline(
        tenant_id=tenant_id, start=skip, stop=skip + limit
    )


async def _load_approaching_deadline(tenant_id: str) -> List[Any]:
    incidents = await retrieve_incidents_approaching_deadline(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [
        i.model_dump(mode="json", by_alias=True) if hasattr(i, "model_dump") else i
        for i in incidents
    ]


@router.get("/{incident_id}")
@document_response(
    message="Incident fetched successfully",
    description="Retrieve a specific incident by ID.",
    summary="Get incident",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Incident not found",
    },
)
async def get_incident(
    incident_id: str, principal: AuthPrincipal = Depends(_report_roles)
):
    tenant_id = principal.tenant_id or ""
    result = await get_or_compute_entity(
        entity_type="incident",
        entity_id=incident_id,
        loader=lambda: retrieve_incident_by_id(
            incident_id=incident_id, tenant_id=tenant_id
        ),
    )
    schedule_resource_read_receipt(
        user_id=principal.user_id,
        user_role=principal.role,
        resource_type="incident",
        resource_ids=[incident_id],
    )
    return result


@router.patch("/{incident_id}")
@document_response(
    message="Incident update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a partial incident update.",
    summary="Update incident (async)",
    success_example={
        "id": "6789abcdef0123456789abcd",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        422: "Unprocessable entity - validation failed",
    },
)
async def update_incident(
    incident_id: str,
    log_data: IncidentLogUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_triage_roles),
):
    tenant_id = principal.tenant_id or ""
    payload = log_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    request_id = getattr(request.state, "request_id", None)
    payload["_actor_id"] = principal.user_id
    payload["_actor_role"] = principal.role
    payload["_request_id"] = request_id
    return await enqueue_write(
        writer_key="incident.update",
        payload=payload,
        resource_type="incident",
        resource_id=incident_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )


# ─── Bulk endpoints ───────────────────────────────────────────────────


@router.post("/bulk/mark-notified")
@document_response(
    message="Bulk mark-notified queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk mark incidents as NDPC-notified",
)
async def bulk_mark_notified(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(_triage_roles),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/incidents/bulk/mark-notified",
        body=payload,
    )
    if hit is not None:
        return hit.response
    extras = {
        "tenant_scope": tenant_id,
        "notification_sent_at": int(payload.get("notificationSentAt") or time.time()),
    }
    response = await enqueue_bulk_write(
        writer_key="incident.bulk_mark_notified",
        ids=payload.get("ids", []),
        resource_type="incident",
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
        route="POST /v1/incidents/bulk/mark-notified",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/status")
@document_response(
    message="Bulk incident status queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk transition incident status",
)
async def bulk_incident_status(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(_triage_roles),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/incidents/bulk/status",
        body=payload,
    )
    if hit is not None:
        return hit.response
    target_status = str(payload.get("status") or "")
    if target_status not in _INCIDENT_STATUSES:
        from core.errors import AppException, ErrorCode

        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="status is required and must be a valid IncidentStatus",
            details={"allowed": sorted(_INCIDENT_STATUSES)},
        )
    extras = {"tenant_scope": tenant_id, "status": target_status}
    response = await enqueue_bulk_write(
        writer_key="incident.bulk_status",
        ids=payload.get("ids", []),
        resource_type="incident",
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
        route="POST /v1/incidents/bulk/status",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response
