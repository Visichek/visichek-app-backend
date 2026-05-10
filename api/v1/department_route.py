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
from schemas.department_schema import (
    DepartmentCreate,
    DepartmentUpdate,
)
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.department_service import (
    retrieve_department_by_id_with_summary,
    retrieve_departments_with_summary,
    validate_department_create,
    validate_department_update,
)


DEPARTMENTS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset({"name", "code", "date_created"}),
    default_sort=(("name", 1),),
    search_fields=("name", "code"),
    filters={
        "branchId": FilterDef(name="branchId", mongo_field="branch_id"),
    },
    facet_fields=frozenset(),
)


def _is_default_dept_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(DEPARTMENTS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(DEPARTMENTS_LIST_SPEC.default_limit)


def _map_dept_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc

router = APIRouter(prefix="/departments", tags=["Departments"])

_admin_roles = verify_system_user_token("super_admin", "dept_admin")


@router.post("")
@document_response(
    message="Department creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a department-create mutation. The ID is pre-assigned so the client can poll the list view or `/v1/departments/{id}` immediately. Actual persistence runs on the writes worker; progress is traceable via queue_job_log.",
    summary="Create a new department (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        422: "Validation error",
    },
)
async def create_department_endpoint(
    dept_data: DepartmentCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    # Sync gate: reject duplicates / cap violations up-front so the client
    # gets a 4xx instead of a 202 followed by a failed-job notification.
    await validate_department_create(
        tenant_id=tenant_id,
        name=dept_data.name,
        code=dept_data.code,
    )
    payload = dept_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    payload["created_by"] = principal.user_id
    return await enqueue_write(
        writer_key="department.create",
        payload=payload,
        resource_type="department",
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Departments fetched successfully",
    description="Retrieve a paginated list of departments. The first page is served from the precompute cache (Redis) refreshed per active tenant; pagination beyond page 1 falls through to the live service.",
    summary="List departments",
    success_example=[
        {
            "id": "64f1a2b3c4d5e6f7a8b9c0d2",
            "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "code": "HR-001",
            "name": "Human Resources",
            "is_active": True,
            "created_by": "64f1a2b3c4d5e6f7a8b9c0d3",
            "date_created": 1712500000,
            "last_updated": 1712500000,
        }
    ],
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
)
async def list_departments(
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return {"items": [], "meta": {"total": 0, "skip": 0, "limit": 25, "hasMore": False}}
    if _is_default_dept_listing(request):
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="departments.list",
            ttl=60,
            loader=lambda: _load_departments_for_tenant(tenant_id),
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: DEPARTMENTS_LIST_SPEC.default_limit]
        return {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": DEPARTMENTS_LIST_SPEC.default_limit,
                "hasMore": len(items) > DEPARTMENTS_LIST_SPEC.default_limit,
            },
        }
    query = parse_list_query(request, DEPARTMENTS_LIST_SPEC)
    return await run_list(
        collection=db.departments,
        query=query,
        base_filter={"tenant_id": tenant_id},
        map_doc=_map_dept_doc,
    )


async def _load_departments_for_tenant(tenant_id: str) -> List[Any]:
    departments = await retrieve_departments_with_summary(
        tenant_id=tenant_id, start=0, stop=100
    )
    out: List[Any] = []
    for dept in departments:
        if hasattr(dept, "model_dump"):
            out.append(dept.model_dump(mode="json", by_alias=True))
        else:
            out.append(dept)
    return out


@router.get("/{department_id}")
@document_response(
    message="Department fetched successfully",
    description="Retrieve a specific department by ID. Served from the HttpCache middleware (60s per scope).",
    summary="Retrieve department by ID",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "code": "HR-001",
        "name": "Human Resources",
        "is_active": True,
        "created_by": "64f1a2b3c4d5e6f7a8b9c0d3",
        "date_created": 1712500000,
        "last_updated": 1712500000,
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Department not found",
    },
)
async def get_department_endpoint(
    department_id: str,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    return await get_or_compute_entity(
        entity_type="department",
        entity_id=department_id,
        loader=lambda: retrieve_department_by_id_with_summary(
            department_id=department_id, tenant_id=tenant_id
        ),
    )


@router.patch("/{department_id}")
@document_response(
    message="Department update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a partial department update. The path id is echoed back so the client can refetch immediately once the worker completes (observable via queue_job_log).",
    summary="Update department by ID (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        422: "Validation error",
    },
)
async def update_department_endpoint(
    department_id: str,
    dept_data: DepartmentUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    # Sync gate: reject duplicates up-front so the client gets a 4xx
    # instead of a 202 followed by a failed-job notification.
    await validate_department_update(
        department_id=department_id,
        tenant_id=tenant_id,
        name=dept_data.name,
        code=dept_data.code,
    )
    payload = dept_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    return await enqueue_write(
        writer_key="department.update",
        payload=payload,
        resource_type="department",
        resource_id=department_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{department_id}")
@document_response(
    message="Department deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a department deletion. Only super-admins can trigger this.",
    summary="Delete department by ID (async)",
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
async def delete_department_endpoint(
    department_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    tenant_id = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="department.delete",
        payload={"tenant_id": tenant_id},
        resource_type="department",
        resource_id=department_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/bulk/delete")
@document_response(
    message="Bulk department delete queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk delete departments",
)
async def bulk_delete_departments(
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
        route="POST /v1/departments/bulk/delete",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="department.bulk_delete",
        ids=payload.get("ids", []),
        resource_type="department",
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
        route="POST /v1/departments/bulk/delete",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response
