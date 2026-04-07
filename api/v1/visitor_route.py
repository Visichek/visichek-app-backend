from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.visit_session_schema import CheckInRequest, CheckOutRequest
from security.auth import verify_system_user_token, verify_any_system_user_token
from security.principal import AuthPrincipal
from services.visit_session_service import (
    check_in_visitor,
    check_out_visitor,
    retrieve_active_visitors,
    retrieve_visit_session_by_id,
    retrieve_visit_sessions,
)

router = APIRouter(prefix="/visitors", tags=["Visitors"])

_checkin_roles = verify_system_user_token("receptionist", "dept_admin", "super_admin")


@router.post("/check-in")
@document_response(message="Visitor checked in successfully", status_code=status.HTTP_201_CREATED)
async def check_in(
    request: CheckInRequest,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await check_in_visitor(
        request=request,
        tenant_id=tenant_id,
        receptionist_id=principal.user_id,
    )


@router.post("/check-out")
@document_response(message="Visitor checked out successfully")
async def check_out(
    request: CheckOutRequest,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await check_out_visitor(request=request, tenant_id=tenant_id)


@router.get("/active")
@document_response(message="Active visitors fetched successfully", success_example=[])
async def list_active_visitors(
    department_id: str = None,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_active_visitors(tenant_id=tenant_id, department_id=department_id)


@router.get("/sessions")
@document_response(message="Visit sessions fetched successfully", success_example=[])
async def list_visit_sessions(
    department_id: str = None,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_system_user_token("dept_admin", "super_admin", "auditor")),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_visit_sessions(
        tenant_id=tenant_id, department_id=department_id, start=start, stop=stop
    )


@router.get("/sessions/{session_id}")
@document_response(message="Visit session fetched successfully")
async def get_visit_session_endpoint(
    session_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_visit_session_by_id(session_id=session_id, tenant_id=tenant_id)
