from __future__ import annotations

from typing import Annotated, Any, List, Optional

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from security.auth import verify_optional_kiosk_token, verify_system_user_token
from security.principal import AuthPrincipal
from services.checkin_config_service import (
    enforce_kiosk_submit_access,
    list_configs_for_tenant,
    resolve_public_config,
)
from services.visitor_service import lookup_visitor
from schemas.checkin_config_schema import (
    CheckinConfigCreate,
    CheckinConfigUpdate,
    PublicCheckinConfigOut,
)
from schemas.checkin_schema import CheckinSubmitRequest

router = APIRouter(prefix="/checkin-configs", tags=["Check-In Configs"])


@router.get("/{checkin_config_id}", response_model=PublicCheckinConfigOut)
@document_response(
    message="Check-in configuration retrieved",
    description="Get public check-in configuration for a kiosk (unauthenticated).",
    summary="Get public check-in config",
    response_codes={404: "Check-in config not found or inactive"},
)
async def get_public_checkin_config(checkin_config_id: str):
    """Get public check-in configuration (unauthenticated endpoint for kiosk)."""
    return await get_or_compute_entity(
        entity_type="checkin_config",
        entity_id=checkin_config_id,
        loader=lambda: resolve_public_config(checkin_config_id),
    )


@router.get("/{checkin_config_id}/visitors/lookup")
@document_response(
    message="Visitor lookup result",
    description="Search for a returning visitor by email and/or phone (kiosk).",
    summary="Lookup visitor",
    response_codes={
        400: "Neither email nor phone provided",
        404: "Visitor not found",
    },
)
async def lookup_returning_visitor(
    checkin_config_id: str,
    email: Annotated[Optional[str], Query()] = None,
    phone: Annotated[Optional[str], Query()] = None,
):
    """Lookup a returning visitor by email and/or phone (kiosk endpoint)."""
    from core.errors import AppException, ErrorCode

    if not email and not phone:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="At least one of email or phone must be provided",
        )

    config = await resolve_public_config(checkin_config_id)
    return await lookup_visitor(tenant_id=config.tenant_id, email=email, phone=phone)


@router.post("/{checkin_config_id}/checkins", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Check-in submitted successfully",
    description=(
        "Submit a new check-in via kiosk (unauthenticated). Stays synchronous "
        "so the visitor receives immediate confirmation + badge context."
    ),
    summary="Submit check-in",
    status_code=status.HTTP_201_CREATED,
    response_codes={
        400: "Validation failed or missing required fields",
        409: "Visitor has pending check-in already",
    },
)
async def submit_visitor_checkin(
    checkin_config_id: str,
    payload: CheckinSubmitRequest,
    principal: Optional[AuthPrincipal] = Depends(verify_optional_kiosk_token),
):
    """Submit a check-in via kiosk (plan-gated). On plans that grant
    ``/v1/public/tenants/*/submit`` the endpoint is fully public; on
    Free / Starter a system user with visitor permissions must drive
    the kiosk (enforced via :func:`enforce_kiosk_submit_access`)."""
    from services.checkin_service import submit_checkin as submit_checkin_service

    config = await resolve_public_config(checkin_config_id)
    await enforce_kiosk_submit_access(tenant_id=config.tenant_id, principal=principal)
    return await submit_checkin_service(checkin_config_id, payload)


@router.post("")
@document_response(
    message="Check-in configuration creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a check-in config create (super_admin only).",
    summary="Create check-in config (async)",
    success_example={
        "id": "507f1f77bcf86cd799439012",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - only super_admin allowed",
    },
)
async def create_checkin_config(
    payload: CheckinConfigCreate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    data = payload.model_dump(exclude_none=True)
    # tenant_id is token-derived, never client-supplied.
    data["tenant_id"] = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="checkin_config.create",
        payload=data,
        resource_type="checkin_config",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.patch("/{checkin_config_id}")
@document_response(
    message="Check-in configuration update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a check-in config update (super_admin only).",
    summary="Update check-in config (async)",
    success_example={
        "id": "507f1f77bcf86cd799439012",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
    },
)
async def update_checkin_config(
    checkin_config_id: str,
    payload: CheckinConfigUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    data = payload.model_dump(exclude_none=True)
    data["tenant_id"] = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="checkin_config.update",
        payload=data,
        resource_type="checkin_config",
        resource_id=checkin_config_id,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Check-in configurations retrieved",
    description="First page served from the per-tenant precompute cache.",
    summary="List check-in configs",
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Forbidden"},
)
async def list_checkin_configs(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0, le=100)] = 20,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("super_admin", "dept_admin")
    ),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if skip == 0 and limit in (20, 100) and tenant_id:
        cached: List[Any] = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="checkin_configs.list",
            ttl=60,
            loader=lambda: _load_configs_for_tenant(tenant_id),
        )
        return cached[:limit], {"total": len(cached), "skip": skip, "limit": limit}

    configs, total = await list_configs_for_tenant(
        tenant_id=tenant_id, skip=skip, limit=limit
    )
    return configs, {"total": total, "skip": skip, "limit": limit}


async def _load_configs_for_tenant(tenant_id: str) -> List[Any]:
    configs, _ = await list_configs_for_tenant(tenant_id=tenant_id, skip=0, limit=100)
    return [
        c.model_dump(mode="json", by_alias=True) if hasattr(c, "model_dump") else c
        for c in configs
    ]
