from typing import Annotated, Any, List

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.department_schema import DepartmentCreate
from schemas.otp_schema import MfaAdminUpdate
from schemas.system_user_schema import SystemUserCreate
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.dashboard_service import get_dashboard_stats
from services.department_service import retrieve_departments
from services.system_user_service import retrieve_system_users

router = APIRouter(prefix="/super-admin", tags=["Tenant Super Admin"])


@router.get("/analytics")
@document_response(
    message="Company-wide analytics fetched successfully",
    description="Served from the per-tenant precompute cache.",
    summary="Get company-wide analytics",
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
)
async def company_analytics(
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="super_admin.analytics",
        ttl=60,
        loader=lambda: _load_analytics(tenant_id),
    )


async def _load_analytics(tenant_id: str) -> Any:
    result = await get_dashboard_stats(tenant_id=tenant_id)
    return result.model_dump(mode="json", by_alias=True) if hasattr(result, "model_dump") else result


@router.get("/departments")
@document_response(
    message="All departments fetched successfully",
    description="First page served from the per-tenant precompute cache.",
    summary="List all departments",
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
)
async def list_all_departments(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="departments.list",
            ttl=60,
            loader=lambda: _load_departments(tenant_id),
        )
    return await retrieve_departments(tenant_id=tenant_id, start=start, stop=stop)


async def _load_departments(tenant_id: str) -> List[Any]:
    depts = await retrieve_departments(tenant_id=tenant_id, start=0, stop=100)
    return [
        d.model_dump(mode="json", by_alias=True) if hasattr(d, "model_dump") else d
        for d in depts
    ]


@router.post("/departments")
@document_response(
    message="Department creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a department create for this super admin's tenant.",
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
async def create_department(
    dept_data: DepartmentCreate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    payload = dept_data.model_dump(exclude_none=True)
    if principal.tenant_id:
        payload["tenant_id"] = principal.tenant_id
    payload["created_by"] = principal.user_id
    return await enqueue_write(
        writer_key="department.create",
        payload=payload,
        resource_type="department",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/admins")
@document_response(
    message="All system users fetched successfully",
    description="First page served from the per-tenant precompute cache.",
    summary="List all system users",
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
)
async def list_all_admins(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="system_users.list",
            ttl=60,
            loader=lambda: _load_system_users(tenant_id),
        )
    return await retrieve_system_users(tenant_id=tenant_id, start=start, stop=stop)


async def _load_system_users(tenant_id: str) -> List[Any]:
    users = await retrieve_system_users(tenant_id=tenant_id, start=0, stop=100)
    return [
        u.model_dump(mode="json", by_alias=True) if hasattr(u, "model_dump") else u
        for u in users
    ]


@router.post("/admins/invite")
@document_response(
    message="System user invite queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a system user invite. Plan cap + email uniqueness checks run inside the writer.",
    summary="Invite a new system user (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d4",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        422: "Validation error",
    },
)
async def invite_admin(
    user_data: SystemUserCreate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    payload = user_data.model_dump(exclude_none=True)
    if principal.tenant_id:
        payload["tenant_id"] = principal.tenant_id
    return await enqueue_write(
        writer_key="system_user.invite",
        payload=payload,
        resource_type="system_user",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/registration-qr")
@document_response(
    message="Registration QR code generated",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Generate a signed URL/QR code for visitor self-registration. "
        "Stays synchronous — the caller needs the signed token immediately."
    ),
    summary="Generate tenant registration QR code",
)
async def generate_registration_qr(
    department_id: str | None = None,
    branch_id: str | None = None,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    from services.visit_session_service import generate_tenant_registration_qr

    tenant_id = principal.tenant_id
    return await generate_tenant_registration_qr(
        tenant_id or "", department_id, branch_id
    )


@router.get("/visitor-log")
@document_response(
    message="Company-wide visitor log retrieved",
    description="Live query — filters are dynamic so precompute doesn't apply.",
    summary="Company-wide visitor logs",
    include_meta=True,
)
async def get_company_visitor_log(
    start_date: Annotated[
        int | None, Query(description="Unix timestamp for start date filter")
    ] = None,
    end_date: Annotated[
        int | None, Query(description="Unix timestamp for end date filter")
    ] = None,
    status_filter: Annotated[
        str | None, Query(description="Filter by visit status")
    ] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id
    from repositories.visit_session_repo import get_visit_sessions, count_visit_sessions
    from typing import Any as _Any, Dict as _Dict

    filter_dict: _Dict[str, _Any] = {"tenant_id": tenant_id}
    if start_date:
        filter_dict.setdefault("check_in_time", {})
        filter_dict["check_in_time"]["$gte"] = start_date
    if end_date:
        filter_dict.setdefault("check_in_time", {})
        filter_dict["check_in_time"]["$lte"] = end_date
    if status_filter:
        filter_dict["status"] = status_filter
    sessions = await get_visit_sessions(
        filter_dict=filter_dict, start=skip, stop=skip + limit
    )
    total = await count_visit_sessions(filter_dict)
    return {"items": sessions, "total": total, "skip": skip, "limit": limit}


@router.patch("/admins/{user_id}/department")
@document_response(
    message="Department assignment queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a system user's department assignment. Invalidates the user's gate cache on commit.",
    summary="Assign admin to department (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d4",
        "job_id": "c4e6f8a0-3456-4fab-9bcd-2345678901cd",
        "status": "queued",
    },
)
async def assign_admin_department(
    user_id: str,
    department_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="system_user.assign_department",
        payload={"tenant_id": tenant_id, "department_id": department_id},
        resource_type="system_user",
        resource_id=user_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch("/admins/{user_id}/mfa")
@document_response(
    message="MFA settings update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue an admin-triggered MFA change for a tenant user. Invalidates the user's gate cache on commit.",
    summary="Set user MFA (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d4",
        "job_id": "d5f7a9b1-4567-4abc-8def-3456789012de",
        "status": "queued",
    },
)
async def set_user_mfa(
    user_id: str,
    mfa_data: MfaAdminUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="system_user.set_mfa",
        payload={
            "tenant_id": tenant_id,
            "mfa_enabled": mfa_data.mfa_enabled,
            "mfa_locked_by_admin": mfa_data.mfa_locked_by_admin,
        },
        resource_type="system_user",
        resource_id=user_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
