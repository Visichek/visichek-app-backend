from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.checkin_service import (
    confirm_checkin,
    get_checkin_detail,
    list_checkins_analytics,
    list_checkins_for_tenant,
)
from schemas.checkin_schema import CheckinConfirmRequest, CheckinOut

router = APIRouter(tags=["Check-Ins"])


@router.get(
    "/tenants/{tenant_id}/checkins",
    response_model=list[CheckinOut],
)
@document_response(
    message="Check-ins retrieved",
    description="List pending or approved check-ins for a tenant (paginated).",
    summary="List check-ins",
    include_meta=True,
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
    },
)
async def list_pending_checkins(
    tenant_id: str,
    state: Annotated[Optional[str], Query()] = "pending_approval",
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0, le=100)] = 20,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "super_admin", "dept_admin")
    ),
):
    """List check-ins for a tenant (receptionist/super_admin/dept_admin)."""
    # Validate tenant_id matches principal
    if principal.tenant_id != tenant_id:
        from core.errors import auth_permission_denied

        raise auth_permission_denied("tenant_scope")

    checkins, total = await list_checkins_for_tenant(
        tenant_id=tenant_id, state=state, skip=skip, limit=limit
    )
    return checkins, {"total": total, "skip": skip, "limit": limit, "state": state}


@router.get(
    "/checkins/{checkin_id}",
    response_model=CheckinOut,
)
@document_response(
    message="Check-in detail retrieved",
    description="Get full check-in details (receptionist/super_admin/dept_admin).",
    summary="Get check-in detail",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
        404: "Check-in not found",
    },
)
async def get_checkin(
    checkin_id: str,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "super_admin", "dept_admin")
    ),
):
    """Get check-in detail (receptionist/super_admin/dept_admin)."""
    tenant_id = principal.tenant_id or ""
    return await get_checkin_detail(tenant_id=tenant_id, checkin_id=checkin_id)


@router.post(
    "/checkins/{checkin_id}/confirm",
)
@document_response(
    message="Check-in confirmed",
    description="Approve or reject a pending check-in (receptionist/super_admin).",
    summary="Confirm check-in",
    response_codes={
        400: "Invalid action or missing required fields",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Check-in not found",
    },
)
async def confirm_pending_checkin(
    checkin_id: str,
    payload: CheckinConfirmRequest,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "super_admin")
    ),
):
    """Approve or reject a check-in (receptionist/super_admin)."""
    return await confirm_checkin(checkin_id, principal, payload)


@router.get(
    "/tenants/{tenant_id}/checkins/analytics",
)
@document_response(
    message="Check-in analytics retrieved",
    description="Get check-in analytics for a date range (receptionist/super_admin/auditor).",
    summary="Get check-in analytics",
    include_meta=True,
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
    },
)
async def get_checkin_analytics(
    tenant_id: str,
    state: Annotated[Optional[str], Query()] = None,
    from_ts: Annotated[Optional[int], Query()] = None,
    to_ts: Annotated[Optional[int], Query()] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0, le=100)] = 20,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "super_admin", "dept_admin", "auditor")
    ),
):
    """Get check-in analytics (receptionist/super_admin/dept_admin/auditor)."""
    # Validate tenant_id matches principal
    if principal.tenant_id != tenant_id:
        from core.errors import auth_permission_denied

        raise auth_permission_denied("tenant_scope")

    checkins, total = await list_checkins_analytics(
        tenant_id=tenant_id,
        state=state,
        from_ts=from_ts,
        to_ts=to_ts,
        skip=skip,
        limit=limit,
    )
    return checkins, {"total": total, "skip": skip, "limit": limit}
