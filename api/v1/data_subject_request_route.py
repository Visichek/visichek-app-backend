from typing import Annotated
from fastapi import APIRouter, Depends, Query, status
from core.response_envelope import document_response
from schemas.data_subject_request_schema import DSRCreate, DSRUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.data_subject_request_service import add_dsr, retrieve_dsr_by_id, retrieve_dsrs, update_dsr_by_id

router = APIRouter(prefix="/dsr", tags=["Data Subject Requests"])
_dpo_roles = verify_system_user_token("super_admin", "dpo")


@router.post("/")
@document_response(message="DSR created successfully", status_code=status.HTTP_201_CREATED)
async def create_dsr_endpoint(dsr_data: DSRCreate, principal: AuthPrincipal = Depends(_dpo_roles)):
    if principal.tenant_id:
        dsr_data.tenant_id = principal.tenant_id
    dsr_data.admin_id = principal.user_id
    return await add_dsr(dsr_data=dsr_data)


@router.get("/")
@document_response(message="DSRs fetched successfully", success_example=[])
async def list_dsrs(
    start: Annotated[int, Query(ge=0)] = 0, stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    return await retrieve_dsrs(tenant_id=principal.tenant_id or "", start=start, stop=stop)


@router.get("/{dsr_id}")
@document_response(message="DSR fetched successfully")
async def get_dsr_endpoint(dsr_id: str, principal: AuthPrincipal = Depends(_dpo_roles)):
    return await retrieve_dsr_by_id(dsr_id=dsr_id, tenant_id=principal.tenant_id or "")


@router.patch("/{dsr_id}")
@document_response(message="DSR updated successfully")
async def update_dsr_endpoint(dsr_id: str, dsr_data: DSRUpdate, principal: AuthPrincipal = Depends(_dpo_roles)):
    return await update_dsr_by_id(dsr_id=dsr_id, tenant_id=principal.tenant_id or "", dsr_data=dsr_data)
