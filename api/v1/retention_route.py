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
@document_response(
    message="Retention policy created successfully",
    status_code=status.HTTP_201_CREATED,
    summary="Create retention policy",
    description="Create a new data retention policy defining how long data is retained.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "scope": "visitor_profiles",
        "retention_days": 365,
        "action": "anonymise",
        "date_created": 1712448000
    },
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid payload"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def create_policy(policy_data: RetentionPolicyCreate, principal: AuthPrincipal = Depends(_dpo_roles)):
    if principal.tenant_id:
        policy_data.tenant_id = principal.tenant_id
    return await create_retention_policy(policy_data)


@router.get("/")
@document_response(
    message="Retention policies fetched successfully",
    summary="List retention policies",
    description="Retrieve all data retention policies configured for the tenant.",
    success_example=[{
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "scope": "visitor_profiles",
        "retention_days": 365,
        "action": "anonymise",
        "date_created": 1712448000
    }],
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid query"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def list_policies(principal: AuthPrincipal = Depends(_dpo_roles)):
    return await get_retention_policies({"tenant_id": principal.tenant_id or ""})


@router.patch("/{policy_id}")
@document_response(
    message="Retention policy updated successfully",
    summary="Update retention policy",
    description="Modify an existing data retention policy with new retention periods.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "scope": "visitor_profiles",
        "retention_days": 365,
        "action": "anonymise",
        "date_created": 1712448000
    },
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 404: "Policy not found", 422: "Invalid payload"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "Retention policy not found", "code": "RESOURCE_NOT_FOUND"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def update_policy(
    policy_id: str, policy_data: RetentionPolicyUpdate, principal: AuthPrincipal = Depends(_dpo_roles),
):
    return await update_retention_policy(
        {"_id": ObjectId(policy_id), "tenant_id": principal.tenant_id or ""}, policy_data,
    )
