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
from schemas.host_schema import HostCreate, HostUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.host_service import (
    retrieve_host_by_id_with_summary,
    retrieve_hosts_with_summary,
    validate_host_create,
    validate_host_update,
)


HOSTS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset({"name", "date_created", "last_updated"}),
    default_sort=(("name", 1),),
    search_fields=("name", "email", "phone"),
    filters={
        "departmentId": FilterDef(name="departmentId", mongo_field="department_id"),
        "isActive": FilterDef(name="isActive", mongo_field="is_active"),
    },
    facet_fields=frozenset(),
)


def _is_default_host_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(HOSTS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(HOSTS_LIST_SPEC.default_limit)


def _map_host_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


router = APIRouter(prefix="/hosts", tags=["Hosts"])

_admin_roles = verify_system_user_token("super_admin", "dept_admin")
# Read access is wider than management: receptionists can't manage the host
# roster but DO create appointments, so the appointment host-picker must be
# able to list/read hosts for them.
_read_roles = verify_system_user_token("super_admin", "dept_admin", "receptionist")


@router.post("")
@document_response(
    message="Host creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Enqueue a host-create mutation. A host is either a dedicated host "
        "or a mirror of a tenant system user (set `source_system_user_id`). "
        "The ID is pre-assigned so the client can poll the list view. "
        "Actual persistence runs on the writes worker."
    ),
    summary="Create a new host (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Department or linked system user not found",
        409: "Host with this name already exists",
        422: "Validation error",
    },
)
async def create_host_endpoint(
    host_data: HostCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    # Sync gate: reject bad department / duplicate name up-front so the
    # client gets a 4xx instead of a 202 followed by a failed-job notice.
    await validate_host_create(
        tenant_id=tenant_id,
        name=host_data.name,
        phone=host_data.phone,
        department_id=host_data.department_id,
        source_system_user_id=host_data.source_system_user_id,
    )
    payload = host_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    request_id = getattr(request.state, "request_id", None)
    payload["_actor_id"] = principal.user_id
    payload["_actor_role"] = principal.role
    payload["_request_id"] = request_id
    return await enqueue_write(
        writer_key="host.create",
        payload=payload,
        resource_type="host",
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )


@router.get("")
@document_response(
    message="Hosts fetched successfully",
    description=(
        "Retrieve a paginated list of hosts. The first page is served from "
        "the per-tenant precompute cache; pagination beyond page 1 falls "
        "through to the live service."
    ),
    summary="List hosts",
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
)
async def list_hosts(
    request: Request,
    principal: AuthPrincipal = Depends(_read_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return {"items": [], "meta": {"total": 0, "skip": 0, "limit": 25, "hasMore": False}}
    if _is_default_host_listing(request):
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="hosts.list",
            ttl=60,
            loader=lambda: _load_hosts_for_tenant(tenant_id),
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: HOSTS_LIST_SPEC.default_limit]
        return {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": HOSTS_LIST_SPEC.default_limit,
                "hasMore": len(items) > HOSTS_LIST_SPEC.default_limit,
            },
        }
    query = parse_list_query(request, HOSTS_LIST_SPEC)
    return await run_list(
        collection=db.hosts,
        query=query,
        base_filter={"tenant_id": tenant_id},
        map_doc=_map_host_doc,
    )


async def _load_hosts_for_tenant(tenant_id: str) -> List[Any]:
    hosts = await retrieve_hosts_with_summary(tenant_id=tenant_id, start=0, stop=100)
    out: List[Any] = []
    for host in hosts:
        if hasattr(host, "model_dump"):
            out.append(host.model_dump(mode="json", by_alias=True))
        else:
            out.append(host)
    return out


@router.get("/{host_id}")
@document_response(
    message="Host fetched successfully",
    description="Retrieve a specific host by ID. Served from the per-entity read-through cache.",
    summary="Retrieve host by ID",
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Host not found",
    },
)
async def get_host_endpoint(
    host_id: str,
    principal: AuthPrincipal = Depends(_read_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    return await get_or_compute_entity(
        entity_type="host",
        entity_id=host_id,
        loader=lambda: retrieve_host_by_id_with_summary(
            host_id=host_id, tenant_id=tenant_id
        ),
    )


@router.patch("/{host_id}")
@document_response(
    message="Host update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Enqueue a partial host update. Name, phone, email, department, "
        "picture and signature image URLs are all updatable."
    ),
    summary="Update host by ID (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Host or department not found",
        409: "Host with this name already exists",
        422: "Validation error",
    },
)
async def update_host_endpoint(
    host_id: str,
    host_data: HostUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    # Sync gate: reject bad department / duplicate name up-front.
    await validate_host_update(
        host_id=host_id,
        tenant_id=tenant_id,
        name=host_data.name,
        department_id=host_data.department_id,
    )
    payload = host_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    request_id = getattr(request.state, "request_id", None)
    payload["_actor_id"] = principal.user_id
    payload["_actor_role"] = principal.role
    payload["_request_id"] = request_id
    return await enqueue_write(
        writer_key="host.update",
        payload=payload,
        resource_type="host",
        resource_id=host_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )


@router.delete("/{host_id}")
@document_response(
    message="Host deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a host deletion. Only super-admins can trigger this.",
    summary="Delete host by ID (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "job_id": "c4e6f8a0-3456-4fab-9bcd-2345678901cd",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
    },
)
async def delete_host_endpoint(
    host_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    tenant_id = principal.tenant_id or ""
    request_id = getattr(request.state, "request_id", None)
    return await enqueue_write(
        writer_key="host.delete",
        payload={
            "tenant_id": tenant_id,
            "_actor_id": principal.user_id,
            "_actor_role": principal.role,
            "_request_id": request_id,
        },
        resource_type="host",
        resource_id=host_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )


@router.post("/bulk/delete")
@document_response(
    message="Bulk host delete queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk delete hosts",
)
async def bulk_delete_hosts(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/hosts/bulk/delete",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="host.bulk_delete",
        ids=payload.get("ids", []),
        resource_type="host",
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
        route="POST /v1/hosts/bulk/delete",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response
