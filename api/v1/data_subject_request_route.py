from typing import Annotated, Any, List

from fastapi import APIRouter, Depends, Query, Request, status

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
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_dpo_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="dsr.list",
            ttl=60,
            loader=lambda: _load_dsrs_for_tenant(tenant_id),
        )
    return await retrieve_dsrs(tenant_id=tenant_id, start=start, stop=stop)


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
    return await retrieve_dsr_by_id(dsr_id=dsr_id, tenant_id=principal.tenant_id or "")


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
