from typing import Annotated, Any, List

from fastapi import APIRouter, Depends, Query, Request, status

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
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    # First-page requests are served from the precomputed cache which is
    # refreshed by worker-precompute for every tenant with live traffic.
    # Non-first pages hit the live service so the cache footprint stays
    # bounded.
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="departments.list",
            ttl=60,
            loader=lambda: _load_departments_for_tenant(tenant_id),
        )
    return await retrieve_departments_with_summary(
        tenant_id=tenant_id, start=start, stop=stop
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
