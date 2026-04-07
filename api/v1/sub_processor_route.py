from fastapi import APIRouter, Depends, status
from core.response_envelope import document_response
from schemas.sub_processor_schema import SubProcessorCreate, SubProcessorUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.sub_processor_service import (
    add_sub_processor, retrieve_sub_processors, update_sub_processor_by_id, remove_sub_processor,
)

router = APIRouter(prefix="/sub-processors", tags=["Sub-Processors"])
_dpo_roles = verify_system_user_token("super_admin", "dpo")


@router.post("/")
@document_response(message="Sub-processor created successfully", status_code=status.HTTP_201_CREATED)
async def create_sp(sp_data: SubProcessorCreate, principal: AuthPrincipal = Depends(_dpo_roles)):
    if principal.tenant_id:
        sp_data.tenant_id = principal.tenant_id
    return await add_sub_processor(sp_data=sp_data)


@router.get("/")
@document_response(message="Sub-processors fetched successfully", success_example=[])
async def list_sps(principal: AuthPrincipal = Depends(_dpo_roles)):
    return await retrieve_sub_processors(tenant_id=principal.tenant_id or "")


@router.patch("/{sp_id}")
@document_response(message="Sub-processor updated successfully")
async def update_sp(sp_id: str, sp_data: SubProcessorUpdate, principal: AuthPrincipal = Depends(_dpo_roles)):
    return await update_sub_processor_by_id(sp_id=sp_id, tenant_id=principal.tenant_id or "", sp_data=sp_data)


@router.delete("/{sp_id}")
@document_response(message="Sub-processor deleted successfully")
async def delete_sp(sp_id: str, principal: AuthPrincipal = Depends(_dpo_roles)):
    return await remove_sub_processor(sp_id=sp_id, tenant_id=principal.tenant_id or "")
