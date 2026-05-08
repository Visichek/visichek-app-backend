from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.imports import TenantEnumKind
from schemas.tenant_enum_schema import (
    TenantEnumBundleOut,
    TenantEnumOut,
    TenantEnumUpdate,
)
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.tenant_enum_service import (
    list_enums_for_tenant,
    public_enum_bundle_for_tenant,
)


# ── Authenticated routes (super_admin manages enums) ──────────────────
router = APIRouter(prefix="/tenants/{tenant_id}/enums", tags=["Tenant Enums"])


def _check_tenant_match(principal: AuthPrincipal, tenant_id: str) -> None:
    """Reject super_admins editing a tenant they don't own.

    Application admins go through their own routes, so this defence
    only matters for the system-user side.
    """
    if principal.tenant_id and principal.tenant_id != tenant_id:
        from core.errors import auth_permission_denied

        raise auth_permission_denied("tenant_enum.manage")


@router.get("")
@document_response(
    message="Tenant enums retrieved",
    description=(
        "List every configurable enum for a tenant — purpose-of-visit, ID "
        "types, visitor categories. Auto-seeds defaults for kinds the "
        "tenant has never customised so the response is always complete."
    ),
    summary="List tenant enums",
)
async def list_tenant_enums_endpoint(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
) -> List[TenantEnumOut]:
    _check_tenant_match(principal, tenant_id)
    cached: List[Any] = await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="tenant_enums.list",
        ttl=20,
        loader=lambda: _load_enums_for_tenant(tenant_id),
    )
    return cached


@router.get("/{kind}")
@document_response(
    message="Tenant enum retrieved",
    description="Get a single enum row (auto-seeds defaults if absent).",
    summary="Get tenant enum",
)
async def get_tenant_enum_endpoint(
    tenant_id: str,
    kind: TenantEnumKind,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
) -> TenantEnumOut:
    _check_tenant_match(principal, tenant_id)
    from services.tenant_enum_service import get_or_seed_enum

    return await get_or_seed_enum(tenant_id, kind)


@router.patch("/{kind}", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Tenant enum update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Replace the option list and/or ``allow_custom`` flag for one "
        "enum kind. Queued — poll ``GET /v1/jobs/{job_id}`` for the "
        "committed result."
    ),
    summary="Update tenant enum (async)",
    success_example={
        "id": "507f1f77bcf86cd799439012",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
)
async def update_tenant_enum_endpoint(
    tenant_id: str,
    kind: TenantEnumKind,
    payload: TenantEnumUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    _check_tenant_match(principal, tenant_id)
    data = payload.model_dump(exclude_none=True)
    data["tenant_id"] = tenant_id
    data["kind"] = kind.value
    data["_actor_id"] = principal.user_id
    data["_actor_role"] = principal.role
    data["_request_id"] = getattr(request.state, "request_id", None)
    return await enqueue_write(
        writer_key="tenant_enum.update",
        payload=data,
        resource_type="tenant_enum",
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{kind}/reset", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Tenant enum reset queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Replace the tenant's option list with the system defaults. "
        "Useful when a super_admin has disabled too many values and "
        "wants to start over."
    ),
    summary="Reset tenant enum to defaults (async)",
)
async def reset_tenant_enum_endpoint(
    tenant_id: str,
    kind: TenantEnumKind,
    request: Request,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    _check_tenant_match(principal, tenant_id)
    data = {
        "tenant_id": tenant_id,
        "kind": kind.value,
        "_actor_id": principal.user_id,
        "_actor_role": principal.role,
        "_request_id": getattr(request.state, "request_id", None),
    }
    return await enqueue_write(
        writer_key="tenant_enum.reset",
        payload=data,
        resource_type="tenant_enum",
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


async def _load_enums_for_tenant(tenant_id: str) -> List[Any]:
    rows = await list_enums_for_tenant(tenant_id)
    return [r.model_dump(mode="json", by_alias=True) for r in rows]


# ── Public kiosk endpoint ─────────────────────────────────────────────
public_router = APIRouter(prefix="/checkin-configs", tags=["Check-In Configs"])


@public_router.get("/{checkin_config_id}/enums")
@document_response(
    message="Public tenant enum bundle retrieved",
    description=(
        "Public kiosk endpoint. Returns every active picker the kiosk "
        "needs (purpose-of-visit, id_type, visitor_category) in one "
        "round trip. Inactive options are filtered out before the "
        "bundle is built."
    ),
    summary="Get tenant enum bundle for kiosk",
    response_codes={404: "Check-in config not found or inactive"},
)
async def get_kiosk_enum_bundle(checkin_config_id: str) -> TenantEnumBundleOut:
    from services.checkin_config_service import resolve_public_config

    config = await resolve_public_config(checkin_config_id)
    return await public_enum_bundle_for_tenant(config.tenant_id)


@public_router.get("/by-tenant/{tenant_id}/enums")
@document_response(
    message="Public tenant enum bundle retrieved",
    description=(
        "Companion to ``GET /v1/checkin-configs/by-tenant/{tenant_id}`` "
        "— used when the kiosk is keyed on tenant_id (no checkin_config "
        "row yet). Returns the same payload as the config-keyed route."
    ),
    summary="Get tenant enum bundle for kiosk by tenant_id",
)
async def get_kiosk_enum_bundle_by_tenant(tenant_id: str) -> TenantEnumBundleOut:
    return await public_enum_bundle_for_tenant(tenant_id)
