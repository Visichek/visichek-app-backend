from typing import Annotated
from fastapi import APIRouter, Depends, Query
from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from repositories.data_processing_register_repo import get_dpr_entries, create_dpr_entry
from repositories.deletion_log_repo import get_deletion_logs
from schemas.data_processing_register_schema import DPRCreate

router = APIRouter(prefix="/compliance", tags=["Compliance"])
_compliance_roles = verify_system_user_token("super_admin", "dpo", "auditor")


@router.get("/register")
@document_response(message="Data processing register fetched successfully", success_example=[])
async def get_register(principal: AuthPrincipal = Depends(_compliance_roles)):
    return await get_dpr_entries({"tenant_id": principal.tenant_id or ""})


@router.post("/register")
@document_response(message="Register entry created successfully")
async def add_register_entry(dpr_data: DPRCreate, principal: AuthPrincipal = Depends(_compliance_roles)):
    if principal.tenant_id:
        dpr_data.tenant_id = principal.tenant_id
    return await create_dpr_entry(dpr_data)


@router.get("/deletion-logs")
@document_response(message="Deletion logs fetched successfully", success_example=[])
async def list_deletion_logs(
    start: Annotated[int, Query(ge=0)] = 0, stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_compliance_roles),
):
    return await get_deletion_logs({"tenant_id": principal.tenant_id or ""}, start=start, stop=stop)
