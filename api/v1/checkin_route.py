from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, Header, Query, Request, status

from core.bulk import enqueue_bulk_write
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.checkin_service import (
    confirm_checkin,
    force_approve_pending_verification,
    get_checkin_detail,
    list_checkins_analytics,
    list_checkins_for_tenant,
    list_pending_approvals_for_tenant,
)
from schemas.checkin_schema import (
    CheckinConfirmRequest,
    CheckinWithVisitorOut,
    PendingApprovalItem,
)

router = APIRouter(tags=["Check-Ins"])


def _checkin_bulk_invocation(
    *,
    request: Request,
    payload: dict[str, Any],
    idempotency_key: Optional[str],
    principal: AuthPrincipal,
    route_label: str,
) -> tuple[Optional[Any], str, str, str, str]:
    """Shared bulk-endpoint preamble (mirrors visitor_route.py helper).

    Resolves the actor scope, checks the idempotency key, returns either
    the cached response (idempotency hit) or the actor metadata the
    caller needs to enqueue the bulk write.
    """
    actor_id = principal.user_id
    actor_role = principal.role
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(actor_id, actor_role)
    hit = check_idempotency(
        key=idempotency_key, scope=scope, route=route_label, body=payload
    )
    return hit, actor_id, actor_role, tenant_id, scope


@router.get(
    "/tenants/{tenant_id}/checkins",
    response_model=list[CheckinWithVisitorOut],
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
    "/tenants/{tenant_id}/pending-approvals",
    response_model=list[PendingApprovalItem],
)
@document_response(
    message="Pending approvals retrieved",
    description=(
        "Unified approval queue for the receptionist UI: kiosk check-ins "
        "awaiting approval (state=pending_approval) AND scheduled "
        "appointments the host pre-vetted (state=scheduled, verified=true). "
        "Each row carries a ``source_type`` discriminator so the frontend "
        "knows which endpoint to call to action it: appointments → "
        "POST /v1/appointments/{id}/check-in, checkins → "
        "POST /v1/checkins/{id}/confirm. Sorted with appointments first "
        "(by scheduled time) then checkins (oldest waiting first)."
    ),
    summary="List unified pending approvals (checkins + scheduled appointments)",
    include_meta=True,
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
    },
)
async def list_pending_approvals_endpoint(
    tenant_id: str,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0, le=100)] = 50,
    include_appointments: Annotated[bool, Query()] = True,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "super_admin", "dept_admin")
    ),
):
    """Unified approval queue (receptionist/super_admin/dept_admin)."""
    if principal.tenant_id != tenant_id:
        from core.errors import auth_permission_denied

        raise auth_permission_denied("tenant_scope")

    rows, total = await list_pending_approvals_for_tenant(
        tenant_id=tenant_id,
        skip=skip,
        limit=limit,
        include_appointments=include_appointments,
    )
    return rows, {"total": total, "skip": skip, "limit": limit}


@router.get(
    "/checkins/{checkin_id}",
    response_model=CheckinWithVisitorOut,
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


@router.post(
    "/checkins/{checkin_id}/force-approve-pending",
    status_code=status.HTTP_200_OK,
)
@document_response(
    message="Check-in unstuck — moved to pending_approval",
    description=(
        "Manually transition a check-in that is stuck in "
        "``pending_verification`` to ``pending_approval`` so it becomes "
        "visible / actionable in the receptionist queue. Use this when a "
        "KYC widget never started, never completed, or its webhook didn't "
        "land — without it the visitor stays invisible to the queue "
        "forever. Records an audit event with the prior state. Only "
        "super_admins may call. Returns 409 if the check-in is already in "
        "any other state (already approved, rejected, etc.)."
    ),
    summary="Force-approve a stuck pending_verification check-in (super_admin)",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
        404: "Check-in not found",
        409: "Check-in is not in pending_verification",
    },
)
async def force_approve_pending_endpoint(
    checkin_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    """Unstick a KYC-parked check-in (super_admin only)."""
    request_id = getattr(request.state, "request_id", None)
    return await force_approve_pending_verification(
        checkin_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )


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


# ─── Bulk approval queue endpoints ────────────────────────────────────
#
# All three return 202 Accepted with the standard queued-write envelope
# `{ id, job_id, status: "queued" }`. Per-id success / failure lands on
# `queue_job_log.result` — poll `GET /v1/jobs/{job_id}` for the
# `{ succeeded, failed }` breakdown. The receptionist UI is expected
# to surface partial-success states (e.g. some checkins moved, others
# rejected because they were no longer in `pending_approval`).
#
# Idempotency-Key header is honored per actor + route so a flaky
# network retry does not double-action a batch.


@router.post("/checkins/bulk/approve", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Bulk approve queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk approve pending check-ins",
    description=(
        "Queue an approval for many check-ins at once. Body: "
        "``{ ids: [...], notes?: string, atomic?: bool }``. Each id must "
        "be in ``pending_approval`` — items not in that state surface in "
        "the ``failed`` array on the job result. On approve, the existing "
        "per-id flow runs (badge generation if the plan allows, host "
        "notification, visitor-badge email if configured)."
    ),
    response_codes={
        202: "Bulk approve queued",
        400: "Invalid ids payload",
        401: "Unauthorized",
        403: "Forbidden",
    },
)
async def bulk_approve_checkins(
    request: Request,
    payload: dict[str, Any] = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "super_admin")
    ),
):
    route_label = "POST /v1/checkins/bulk/approve"
    cached, actor_id, actor_role, tenant_id, scope = _checkin_bulk_invocation(
        request=request,
        payload=payload,
        idempotency_key=idempotency_key,
        principal=principal,
        route_label=route_label,
    )
    if cached is not None:
        return cached.response
    response = await enqueue_bulk_write(
        writer_key="checkin.bulk_approve",
        ids=payload.get("ids", []),
        resource_type="checkin",
        extras={
            "tenant_scope": tenant_id,
            "actor_id": actor_id,
            "notes": payload.get("notes"),
        },
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route=route_label,
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/checkins/bulk/reject", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Bulk reject queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk reject pending check-ins",
    description=(
        "Queue a rejection for many check-ins at once. Body: "
        "``{ ids: [...], reason?: string, atomic?: bool }``. The shared "
        "``reason`` is stored as ``rejection_reason`` on every check-in "
        "and surfaced in the rejection notification. Items not currently "
        "in ``pending_approval`` surface in the ``failed`` array."
    ),
    response_codes={
        202: "Bulk reject queued",
        400: "Invalid ids payload",
        401: "Unauthorized",
        403: "Forbidden",
    },
)
async def bulk_reject_checkins(
    request: Request,
    payload: dict[str, Any] = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "super_admin")
    ),
):
    route_label = "POST /v1/checkins/bulk/reject"
    cached, actor_id, actor_role, tenant_id, scope = _checkin_bulk_invocation(
        request=request,
        payload=payload,
        idempotency_key=idempotency_key,
        principal=principal,
        route_label=route_label,
    )
    if cached is not None:
        return cached.response
    response = await enqueue_bulk_write(
        writer_key="checkin.bulk_reject",
        ids=payload.get("ids", []),
        resource_type="checkin",
        extras={
            "tenant_scope": tenant_id,
            "actor_id": actor_id,
            "reason": payload.get("reason"),
        },
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route=route_label,
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post(
    "/checkins/bulk/force-approve-pending", status_code=status.HTTP_202_ACCEPTED
)
@document_response(
    message="Bulk force-approve queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk unstick KYC-parked check-ins (super_admin)",
    description=(
        "Bulk equivalent of ``POST /v1/checkins/{id}/force-approve-pending``. "
        "Moves every id from ``pending_verification`` → ``pending_approval`` "
        "so the receptionist queue picks them up. Items in any other state "
        "(already approved, rejected, or checked-out) surface in the "
        "``failed`` array with the per-id 409 error. Super_admin only — "
        "this is a manual recovery action for the KYC-webhook-lost scenario."
    ),
    response_codes={
        202: "Bulk force-approve queued",
        400: "Invalid ids payload",
        401: "Unauthorized",
        403: "Forbidden",
    },
)
async def bulk_force_approve_pending_checkins(
    request: Request,
    payload: dict[str, Any] = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    route_label = "POST /v1/checkins/bulk/force-approve-pending"
    cached, actor_id, actor_role, tenant_id, scope = _checkin_bulk_invocation(
        request=request,
        payload=payload,
        idempotency_key=idempotency_key,
        principal=principal,
        route_label=route_label,
    )
    if cached is not None:
        return cached.response
    response = await enqueue_bulk_write(
        writer_key="checkin.bulk_force_approve_pending",
        ids=payload.get("ids", []),
        resource_type="checkin",
        extras={
            "tenant_scope": tenant_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "request_id": getattr(request.state, "request_id", None),
        },
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route=route_label,
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response
