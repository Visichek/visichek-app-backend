from typing import Any, List

from fastapi import APIRouter, Depends, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.sub_processor_schema import SubProcessorCreate, SubProcessorUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.sub_processor_service import retrieve_sub_processors

router = APIRouter(prefix="/sub-processors", tags=["Sub-Processors"])
_dpo_roles = verify_system_user_token("super_admin", "dpo")


@router.post("")
@document_response(
    message="Sub-processor creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create sub-processor (async)",
    description="Enqueue a sub-processor create. The ID is pre-assigned so the client can poll the list view.",
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
async def create_sp(
    sp_data: SubProcessorCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    payload = sp_data.model_dump(exclude_none=True)
    if principal.tenant_id:
        payload["tenant_id"] = principal.tenant_id
    return await enqueue_write(
        writer_key="sub_processor.create",
        payload=payload,
        resource_type="sub_processor",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Sub-processors fetched successfully",
    summary="List sub-processors",
    description="Served from the per-tenant precompute cache refreshed by worker-precompute.",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "tenant_001",
            "provider": "AWS",
            "purpose": "Cloud hosting",
            "jurisdiction": "US",
            "dpa_signed": True,
            "uses_data_for_training": False,
            "date_created": 1712448000,
        }
    ],
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions"},
)
async def list_sps(principal: AuthPrincipal = Depends(_dpo_roles)) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return await retrieve_sub_processors(tenant_id=tenant_id)
    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="sub_processors.list",
        ttl=60,
        loader=lambda: _load_sps_for_tenant(tenant_id),
    )


async def _load_sps_for_tenant(tenant_id: str) -> List[Any]:
    sps = await retrieve_sub_processors(tenant_id=tenant_id)
    out: List[Any] = []
    for sp in sps:
        if hasattr(sp, "model_dump"):
            out.append(sp.model_dump(mode="json", by_alias=True))
        else:
            out.append(sp)
    return out


@router.patch("/{sp_id}")
@document_response(
    message="Sub-processor update queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update sub-processor (async)",
    description="Enqueue a partial sub-processor update.",
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
async def update_sp(
    sp_id: str,
    sp_data: SubProcessorUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    tenant_id = principal.tenant_id or ""
    payload = sp_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    return await enqueue_write(
        writer_key="sub_processor.update",
        payload=payload,
        resource_type="sub_processor",
        resource_id=sp_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{sp_id}")
@document_response(
    message="Sub-processor deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete sub-processor (async)",
    description="Enqueue a sub-processor deletion.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "job_id": "c4e6f8a0-3456-4fab-9bcd-2345678901cd",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
    },
)
async def delete_sp(
    sp_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    tenant_id = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="sub_processor.delete",
        payload={"tenant_id": tenant_id},
        resource_type="sub_processor",
        resource_id=sp_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
