from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.tenant_settings_schema import TenantSettingsUpdate
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.tenant_settings_service import retrieve_or_create_tenant_settings

router = APIRouter(prefix="/tenants", tags=["Tenant Settings"])


@router.get("/{tenant_id}/settings")
@document_response(
    message="Tenant settings fetched successfully",
    description="Served from the per-tenant precompute cache.",
    summary="Get tenant settings",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - must be super admin",
    },
)
async def get_tenant_settings(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="tenant.settings",
        ttl=60,
        loader=lambda: _load_tenant_settings(tenant_id),
    )


async def _load_tenant_settings(tenant_id: str) -> Any:
    result = await retrieve_or_create_tenant_settings(tenant_id)
    return (
        result.model_dump(mode="json", by_alias=True)
        if hasattr(result, "model_dump")
        else result
    )


@router.patch("/{tenant_id}/settings")
@document_response(
    message="Tenant settings update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a partial tenant settings update.",
    summary="Update tenant settings (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - must be super admin",
        422: "Validation error",
    },
)
async def update_tenant_settings(
    tenant_id: str,
    data: TenantSettingsUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    payload = data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    payload["_actor_id"] = principal.user_id
    payload["_actor_role"] = principal.role
    return await enqueue_write(
        writer_key="tenant_settings.update",
        payload=payload,
        resource_type="tenant_settings",
        resource_id=tenant_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
