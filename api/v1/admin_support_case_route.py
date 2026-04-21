"""Application-admin facing support-case routes.

Application admins see ALL cases across tenants. Internal notes and admin
filters (assigned_admin_id, support_tier, tenant_id) are available here.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.admin_schema import AdminOut
from schemas.support_case_schema import (
    SupportCaseAssignRequest,
    SupportCaseMessageRequest,
    SupportCaseTransitionRequest,
)
from security.account_status_check import (
    check_admin_account_status_and_permissions,
)
from services.support_case_service import (
    retrieve_cases_approaching_sla,
    retrieve_messages_for_case,
    retrieve_support_case_by_id,
    retrieve_support_cases,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admins/support-cases", tags=["Application Admin Support"])


async def _load_admin_cases(_: str) -> list:
    cases = await retrieve_support_cases(start=0, stop=100)
    return [c.model_dump(mode="json", by_alias=True) for c in cases]


@router.get("")
@document_response(
    message="Support cases fetched",
    description=(
        "List every support case in the system. Page-1 with no filters is "
        "served from the global precompute cache."
    ),
    summary="List all support cases (admin)",
    include_meta=True,
)
async def admin_list_support_cases(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    status_filter: Annotated[Optional[str], Query(alias="status")] = None,
    priority: Annotated[Optional[str], Query()] = None,
    category: Annotated[Optional[str], Query()] = None,
    tenant_id: Annotated[Optional[str], Query()] = None,
    assigned_admin_id: Annotated[Optional[str], Query()] = None,
    support_tier: Annotated[Optional[str], Query()] = None,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    _ = admin
    if (
        start == 0
        and stop == 100
        and status_filter is None
        and priority is None
        and category is None
        and tenant_id is None
        and assigned_admin_id is None
        and support_tier is None
    ):
        return await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="support_cases.admin_list",
            ttl=60,
            loader=lambda: _load_admin_cases(""),
        )
    return await retrieve_support_cases(
        tenant_id=tenant_id,
        status=status_filter,
        priority=priority,
        category=category,
        assigned_admin_id=assigned_admin_id,
        support_tier=support_tier,
        start=start,
        stop=stop,
    )


@router.get("/approaching-sla")
@document_response(
    message="Support cases approaching SLA fetched",
    description="Active cases whose SLA deadline elapses within 24 hours.",
    summary="List SLA-at-risk cases",
    include_meta=True,
)
async def admin_approaching_sla(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    _ = admin
    return await retrieve_cases_approaching_sla(start=start, stop=stop)


@router.get("/{case_id}")
@document_response(
    message="Support case fetched",
    description="Full case detail including internal notes.",
    summary="Retrieve a support case (admin)",
)
async def admin_get_support_case(
    case_id: str,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    _ = admin
    return await retrieve_support_case_by_id(
        case_id, tenant_id=None, requester_role="admin"
    )


@router.get("/{case_id}/messages")
@document_response(
    message="Support case thread fetched",
    description="Full message thread including internal notes.",
    summary="List messages on a case (admin)",
    include_meta=True,
)
async def admin_list_messages(
    case_id: str,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 200,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    _ = admin
    return await retrieve_messages_for_case(
        case_id, requester_role="admin", start=start, stop=stop
    )


@router.post("/{case_id}/messages")
@document_response(
    message="Admin support case message queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Reply on a case as an admin. Admins can mark the message as an "
        "internal note, in which case tenants never see it and no tenant "
        "email goes out."
    ),
    summary="Reply on a support case (admin, async)",
)
async def admin_reply_on_support_case(
    case_id: str,
    payload: SupportCaseMessageRequest,
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    # Validate the case exists.
    await retrieve_support_case_by_id(
        case_id, tenant_id=None, requester_role="admin"
    )
    body = payload.model_dump(exclude_none=True)
    body["case_id"] = case_id
    body["author_id"] = admin.id or ""
    body["author_role"] = "admin"
    return await enqueue_write(
        writer_key="support_case.message.add",
        payload=body,
        resource_type="support_case_message",
        tenant_id=None,
        actor_id=admin.id,
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{case_id}/assign")
@document_response(
    message="Support case assignment queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Assign (or re-assign) an admin to a case. For PRIORITY-tier "
        "tenants the assigned admin gets an email ping."
    ),
    summary="Assign a case (admin, async)",
)
async def admin_assign_support_case(
    case_id: str,
    payload: SupportCaseAssignRequest,
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    await retrieve_support_case_by_id(
        case_id, tenant_id=None, requester_role="admin"
    )
    return await enqueue_write(
        writer_key="support_case.assign",
        payload={
            "admin_id": payload.admin_id,
            "actor_id": admin.id or "",
            "actor_role": "admin",
        },
        resource_type="support_case",
        resource_id=case_id,
        tenant_id=None,
        actor_id=admin.id,
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{case_id}/transition")
@document_response(
    message="Support case transition queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Admin-side state transitions (acknowledged, in_progress, awaiting_tenant, resolved).",
    summary="Transition a case (admin, async)",
)
async def admin_transition_support_case(
    case_id: str,
    payload: SupportCaseTransitionRequest,
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    await retrieve_support_case_by_id(
        case_id, tenant_id=None, requester_role="admin"
    )
    return await enqueue_write(
        writer_key="support_case.transition",
        payload={
            "status": payload.status.value,
            "actor_id": admin.id or "",
            "actor_role": "admin",
        },
        resource_type="support_case",
        resource_id=case_id,
        tenant_id=None,
        actor_id=admin.id,
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


__all__ = ["router"]
