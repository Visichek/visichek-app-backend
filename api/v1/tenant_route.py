from typing import Annotated, Any, List

from fastapi import APIRouter, Depends, Query, Request, status

from core.errors import auth_permission_denied, auth_role_mismatch
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.tenant_schema import TenantCreate, TenantUpdate
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_any_token, verify_super_admin_token
from security.principal import AuthPrincipal
from services.tenant_service import (
    retrieve_tenant_by_id_with_summary,
    retrieve_tenants_with_summary,
)

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.post("")
@document_response(
    message="Tenant creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Enqueue a plain tenant create. For bootstrap (tenant + first "
        "super_admin atomically), use POST /admins/tenants/bootstrap which "
        "stays synchronous."
    ),
    summary="Create tenant (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        422: "Validation error",
    },
)
async def create_tenant_endpoint(
    tenant_data: TenantCreate,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="tenant.create",
        payload=tenant_data.model_dump(exclude_none=True),
        resource_type="tenant",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Tenants fetched successfully",
    description="First page served from the global precompute cache.",
    summary="List all tenants",
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
)
async def list_tenants(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    admin=Depends(check_admin_account_status_and_permissions),
) -> Any:
    if start == 0 and stop == 100:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.GLOBAL.value}",
            resource="tenants.list",
            ttl=60,
            loader=_load_tenants,
        )
    return await retrieve_tenants_with_summary(start=start, stop=stop)


async def _load_tenants() -> List[Any]:
    tenants = await retrieve_tenants_with_summary(start=0, stop=100)
    return [
        t.model_dump(mode="json", by_alias=True) if hasattr(t, "model_dump") else t
        for t in tenants
    ]


@router.get("/{tenant_id}")
@document_response(
    message="Tenant fetched successfully",
    description="Retrieve a specific tenant by ID.",
    summary="Retrieve tenant by ID",
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Tenant not found",
    },
)
async def get_tenant_endpoint(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    if principal.role == "admin":
        pass
    elif principal.role == "super_admin":
        if principal.tenant_id != tenant_id:
            raise auth_permission_denied(permission_key="tenant.read")
    else:
        raise auth_role_mismatch(required_role="admin", actual_role=principal.role)
    return await retrieve_tenant_by_id_with_summary(tenant_id=tenant_id)


@router.patch("/{tenant_id}")
@document_response(
    message="Tenant update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a partial tenant update.",
    summary="Update tenant by ID (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        422: "Validation error",
    },
)
async def update_tenant_endpoint(
    tenant_id: str,
    tenant_data: TenantUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    return await enqueue_write(
        writer_key="tenant.update",
        payload=tenant_data.model_dump(exclude_none=True),
        resource_type="tenant",
        resource_id=tenant_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
