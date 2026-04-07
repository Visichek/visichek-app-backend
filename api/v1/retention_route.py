from fastapi import APIRouter, Depends, status
from bson import ObjectId
from core.response_envelope import document_response
from schemas.retention_policy_schema import RetentionPolicyCreate, RetentionPolicyUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from repositories.retention_policy_repo import (
    create_retention_policy, get_retention_policies, update_retention_policy,
)

router = APIRouter(prefix="/retention-policies", tags=["Retention Policies"])
_dpo_roles = verify_system_user_token("super_admin", "dpo")


@router.post("/")
@document_response(message="Retention policy created successfully", status_code=status.HTTP_201_CREATED)
async def create_policy(policy_data: RetentionPolicyCreate, principal: AuthPrincipal = Depends(_dpo_roles)):
    if principal.tenant_id:
        policy_data.tenant_id = principal.tenant_id
    return await create_retention_policy(policy_data)


@router.get("/")
@document_response(message="Retention policies fetched successfully", success_example=[])
async def list_policies(principal: AuthPrincipal = Depends(_dpo_roles)):
    return await get_retention_policies({"tenant_id": principal.tenant_id or ""})


@router.patch("/{policy_id}")
@document_response(message="Retention policy updated successfully")
async def update_policy(
    policy_id: str, policy_data: RetentionPolicyUpdate, principal: AuthPrincipal = Depends(_dpo_roles),
):
    return await update_retention_policy(
        {"_id": ObjectId(policy_id), "tenant_id": principal.tenant_id or ""}, policy_data,
    )
