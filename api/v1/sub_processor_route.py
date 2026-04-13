from fastapi import APIRouter, Depends, status
from core.response_envelope import document_response
from schemas.sub_processor_schema import SubProcessorCreate, SubProcessorUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.sub_processor_service import (
    add_sub_processor,
    retrieve_sub_processors,
    update_sub_processor_by_id,
    remove_sub_processor,
)

router = APIRouter(prefix="/sub-processors", tags=["Sub-Processors"])
_dpo_roles = verify_system_user_token("super_admin", "dpo")


@router.post("/")
@document_response(
    message="Sub-processor created successfully",
    status_code=status.HTTP_201_CREATED,
    summary="Create sub-processor",
    description="Register a new sub-processor for data processing activities documentation.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "provider": "AWS",
        "purpose": "Cloud hosting",
        "jurisdiction": "US",
        "dpa_signed": True,
        "uses_data_for_training": False,
        "date_created": 1712448000,
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        409: "Provider already exists",
        422: "Invalid payload",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Token validation failed",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
        409: {
            "success": False,
            "message": "Sub-processor already exists",
            "code": "RESOURCE_CONFLICT",
        },
        422: {
            "success": False,
            "message": "Validation error",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def create_sp(
    sp_data: SubProcessorCreate, principal: AuthPrincipal = Depends(_dpo_roles)
):
    if principal.tenant_id:
        sp_data.tenant_id = principal.tenant_id
    return await add_sub_processor(sp_data=sp_data)


@router.get("/")
@document_response(
    message="Sub-processors fetched successfully",
    summary="List sub-processors",
    description="Retrieve all registered sub-processors for the tenant.",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "tenant_001",
            "provider": "AWS",
            "purpose": "Cloud hosting",
            "jurisdiction": "US",
            "dpa_signed": True,
            "uses_data_for_training": False,
            "date_created": 1712448000,
        }
    ],
    include_meta=True,
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        422: "Invalid query",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Token validation failed",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
        422: {
            "success": False,
            "message": "Validation error",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def list_sps(principal: AuthPrincipal = Depends(_dpo_roles)):
    return await retrieve_sub_processors(tenant_id=principal.tenant_id or "")


@router.patch("/{sp_id}")
@document_response(
    message="Sub-processor updated successfully",
    summary="Update sub-processor",
    description="Modify an existing sub-processor's details and contact information.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "provider": "AWS",
        "purpose": "Cloud hosting",
        "jurisdiction": "US",
        "dpa_signed": True,
        "uses_data_for_training": False,
        "date_created": 1712448000,
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        404: "Sub-processor not found",
        422: "Invalid payload",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Token validation failed",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
        404: {
            "success": False,
            "message": "Sub-processor not found",
            "code": "RESOURCE_NOT_FOUND",
        },
        422: {
            "success": False,
            "message": "Validation error",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def update_sp(
    sp_id: str,
    sp_data: SubProcessorUpdate,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    return await update_sub_processor_by_id(
        sp_id=sp_id, tenant_id=principal.tenant_id or "", sp_data=sp_data
    )


@router.delete("/{sp_id}")
@document_response(
    message="Sub-processor deleted successfully",
    summary="Delete sub-processor",
    description="Remove a sub-processor from the system and audit trail.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "provider": "AWS",
        "purpose": "Cloud hosting",
        "jurisdiction": "US",
        "dpa_signed": True,
        "uses_data_for_training": False,
        "date_created": 1712448000,
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        404: "Sub-processor not found",
        422: "Invalid payload",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Token validation failed",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
        404: {
            "success": False,
            "message": "Sub-processor not found",
            "code": "RESOURCE_NOT_FOUND",
        },
        422: {
            "success": False,
            "message": "Validation error",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def delete_sp(sp_id: str, principal: AuthPrincipal = Depends(_dpo_roles)):
    return await remove_sub_processor(sp_id=sp_id, tenant_id=principal.tenant_id or "")
