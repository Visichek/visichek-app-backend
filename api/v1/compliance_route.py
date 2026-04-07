from typing import Annotated
from fastapi import APIRouter, Depends, Query, status
from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from repositories.data_processing_register_repo import get_dpr_entries, create_dpr_entry
from repositories.deletion_log_repo import get_deletion_logs
from schemas.data_processing_register_schema import DPRCreate

router = APIRouter(prefix="/compliance", tags=["Compliance"])
_compliance_roles = verify_system_user_token("super_admin", "dpo", "auditor")


@router.get("/register")
@document_response(
    message="Data processing register fetched successfully",
    summary="Get data processing register",
    description="Retrieve the complete data processing register (DPR) for compliance documentation.",
    success_example=[{
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "field_name": "visitor_email",
        "purpose": "Visitor identification",
        "lawful_basis": "consent",
        "retention_period": "30 days",
        "sub_processor_id": None,
        "crosses_borders": False,
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
async def get_register(principal: AuthPrincipal = Depends(_compliance_roles)):
    return await get_dpr_entries({"tenant_id": principal.tenant_id or ""})


@router.post("/register")
@document_response(
    message="Register entry created successfully",
    status_code=status.HTTP_201_CREATED,
    summary="Add data processing register entry",
    description="Create a new entry in the data processing register documenting processing activities.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "field_name": "visitor_email",
        "purpose": "Visitor identification",
        "lawful_basis": "consent",
        "retention_period": "30 days",
        "sub_processor_id": None,
        "crosses_borders": False,
        "date_created": 1712448000
    },
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid payload"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def add_register_entry(dpr_data: DPRCreate, principal: AuthPrincipal = Depends(_compliance_roles)):
    if principal.tenant_id:
        dpr_data.tenant_id = principal.tenant_id
    return await create_dpr_entry(dpr_data)


@router.get("/deletion-logs")
@document_response(
    message="Deletion logs fetched successfully",
    summary="List deletion logs",
    description="Retrieve audit logs of all data deletion operations performed on the system.",
    success_example=[{
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "entity_type": "visitor_profile",
        "entity_id": "visitor_123",
        "reason": "Retention policy expired",
        "action": "anonymise",
        "performed_by": "admin_001",
        "timestamp": 1712448000
    }],
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid query"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Invalid pagination parameters", "code": "VALIDATION_FAILED"}
    }
)
async def list_deletion_logs(
    start: Annotated[int, Query(ge=0)] = 0, stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_compliance_roles),
):
    return await get_deletion_logs({"tenant_id": principal.tenant_id or ""}, start=start, stop=stop)
