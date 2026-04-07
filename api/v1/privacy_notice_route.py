from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.privacy_notice_schema import PrivacyNoticeCreate, PrivacyNoticeUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.privacy_notice_service import (
    add_privacy_notice,
    retrieve_active_notice,
    retrieve_privacy_notices,
    update_notice_by_id,
)

router = APIRouter(prefix="/privacy-notices", tags=["Privacy Notices"])

_admin_roles = verify_system_user_token("super_admin", "dpo")


@router.post("/")
@document_response(message="Privacy notice created successfully", status_code=status.HTTP_201_CREATED)
async def create_privacy_notice_endpoint(
    notice_data: PrivacyNoticeCreate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    if principal.tenant_id:
        notice_data.tenant_id = principal.tenant_id
    return await add_privacy_notice(notice_data=notice_data)


@router.get("/active")
@document_response(message="Active privacy notice fetched successfully")
async def get_active_notice(
    principal: AuthPrincipal = Depends(verify_system_user_token(
        "super_admin", "dpo", "receptionist", "dept_admin"
    )),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_active_notice(tenant_id=tenant_id)


@router.get("/")
@document_response(message="Privacy notices fetched successfully", success_example=[])
async def list_privacy_notices(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_privacy_notices(tenant_id=tenant_id, start=start, stop=stop)


@router.patch("/{notice_id}")
@document_response(message="Privacy notice updated successfully")
async def update_privacy_notice_endpoint(
    notice_id: str,
    notice_data: PrivacyNoticeUpdate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await update_notice_by_id(notice_id=notice_id, tenant_id=tenant_id, notice_data=notice_data)
