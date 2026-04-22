from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.branding_schema import BrandingUpdate
from security.auth import verify_any_system_user_token, verify_super_admin_token
from security.principal import AuthPrincipal
from services.branding_service import (
    retrieve_branding_by_tenant,
    retrieve_public_branding_by_tenant,
)

router = APIRouter(prefix="/branding", tags=["Tenant Branding"])


# ─── Public (unauthenticated) ───────────────────────────────────────────


@router.get("/public/tenant/{tenant_id}")
@document_response(
    message="Branding fetched successfully",
    success_example={
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "company_display_name": "Acme Corp",
        "primary_color": "#1A73E8",
        "secondary_color": "#34A853",
        "accent_color": "#FBBC04",
        "logo_url": "https://s3.../tenants/64f1a.../logo.png?X-Amz-...",
        "favicon_url": "https://s3.../tenants/64f1a.../favicon.ico?X-Amz-...",
    },
    description=(
        "Public endpoint — no auth required. Served from the per-tenant "
        "precompute cache so login-screen renders never hit the DB."
    ),
    summary="Get tenant branding (public)",
    response_codes={404: "Not found - tenant has no branding configured"},
)
async def get_public_tenant_branding(tenant_id: str) -> Any:
    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="branding.public",
        ttl=120,
        loader=lambda: _load_public_branding(tenant_id),
    )


async def _load_public_branding(tenant_id: str) -> Any:
    branding = await retrieve_public_branding_by_tenant(tenant_id)
    return branding.model_dump(mode="json", by_alias=True)


# ─── Authenticated (system user) ────────────────────────────────────────


@router.get("/tenant/{tenant_id}")
@document_response(
    message="Branding fetched successfully",
    description="Served from the per-tenant precompute cache.",
    summary="Get tenant branding",
    response_codes={401: "Unauthorized"},
)
async def get_tenant_branding(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> Any:
    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="branding.full",
        ttl=120,
        loader=lambda: _load_full_branding(tenant_id),
    )


async def _load_full_branding(tenant_id: str) -> Any:
    branding = await retrieve_branding_by_tenant(tenant_id)
    return branding.model_dump(mode="json", by_alias=True)


@router.put("")
@document_response(
    message="Branding upsert queued",
    status_code=status.HTTP_202_ACCEPTED,
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    description=(
        "Enqueue an upsert of the tenant's branding config. Writer runs "
        "with the tenant_id from the token. Only super admins may call."
    ),
    summary="Set tenant branding (async)",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - must be super admin",
        422: "Validation error",
    },
)
async def set_tenant_branding(
    branding_data: BrandingUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    payload = branding_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    return await enqueue_write(
        writer_key="branding.upsert",
        payload=payload,
        resource_type="branding",
        resource_id=tenant_id,  # upsert — one record per tenant
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("")
@document_response(
    message="Branding deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    description="Enqueue deletion of the tenant's branding config.",
    summary="Reset tenant branding (async)",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - must be super admin",
    },
)
async def reset_tenant_branding(
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="branding.delete",
        payload={"tenant_id": tenant_id},
        resource_type="branding",
        resource_id=tenant_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
