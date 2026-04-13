from typing import Annotated
from fastapi import APIRouter, Depends, Query, status
from core.response_envelope import document_response
from schemas.data_subject_request_schema import DSRCreate, DSRUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.data_subject_request_service import (
    add_dsr,
    retrieve_dsr_by_id,
    retrieve_dsrs,
    update_dsr_by_id,
)

router = APIRouter(prefix="/dsr", tags=["Data Subject Requests"])
_dpo_roles = verify_system_user_token("super_admin", "dpo")


@router.post("/")
@document_response(
    message="DSR created successfully",
    status_code=status.HTTP_201_CREATED,
    summary="Create data subject request",
    description="Create a new data subject request (access, deletion, portability, etc.).",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "visitor_profile_id": "visitor_123",
        "admin_id": "admin_001",
        "visit_session_id": None,
        "request_type": "access",
        "status": "pending",
        "identity_verified": False,
        "sla_deadline": 1714953600,
        "notes": "Subject requested data access on 2026-04-07",
        "received_at": 1712448000,
        "resolved_at": None,
        "date_created": 1712448000,
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
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
        422: {
            "success": False,
            "message": "Validation error",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def create_dsr_endpoint(
    dsr_data: DSRCreate, principal: AuthPrincipal = Depends(_dpo_roles)
):
    if principal.tenant_id:
        dsr_data.tenant_id = principal.tenant_id
    dsr_data.admin_id = principal.user_id
    return await add_dsr(dsr_data=dsr_data)


@router.get("/")
@document_response(
    message="DSRs fetched successfully",
    summary="List data subject requests",
    description="Retrieve all data subject requests for the tenant with pagination support.",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "tenant_001",
            "visitor_profile_id": "visitor_123",
            "admin_id": "admin_001",
            "visit_session_id": None,
            "request_type": "access",
            "status": "pending",
            "identity_verified": False,
            "sla_deadline": 1714953600,
            "notes": "Subject requested data access on 2026-04-07",
            "received_at": 1712448000,
            "resolved_at": None,
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
            "message": "Invalid pagination parameters",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def list_dsrs(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    return await retrieve_dsrs(
        tenant_id=principal.tenant_id or "", start=start, stop=stop
    )


@router.get("/{dsr_id}")
@document_response(
    message="DSR fetched successfully",
    summary="Get data subject request",
    description="Retrieve a specific data subject request by ID with full details.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "visitor_profile_id": "visitor_123",
        "admin_id": "admin_001",
        "visit_session_id": None,
        "request_type": "access",
        "status": "pending",
        "identity_verified": False,
        "sla_deadline": 1714953600,
        "notes": "Subject requested data access on 2026-04-07",
        "received_at": 1712448000,
        "resolved_at": None,
        "date_created": 1712448000,
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        404: "DSR not found",
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
            "message": "Data subject request not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def get_dsr_endpoint(dsr_id: str, principal: AuthPrincipal = Depends(_dpo_roles)):
    return await retrieve_dsr_by_id(dsr_id=dsr_id, tenant_id=principal.tenant_id or "")


@router.patch("/{dsr_id}")
@document_response(
    message="DSR updated successfully",
    summary="Update data subject request",
    description="Update the status and details of an existing data subject request.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "visitor_profile_id": "visitor_123",
        "admin_id": "admin_001",
        "visit_session_id": None,
        "request_type": "access",
        "status": "pending",
        "identity_verified": False,
        "sla_deadline": 1714953600,
        "notes": "Subject requested data access on 2026-04-07",
        "received_at": 1712448000,
        "resolved_at": None,
        "date_created": 1712448000,
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        404: "DSR not found",
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
            "message": "Data subject request not found",
            "code": "RESOURCE_NOT_FOUND",
        },
        422: {
            "success": False,
            "message": "Validation error",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def update_dsr_endpoint(
    dsr_id: str, dsr_data: DSRUpdate, principal: AuthPrincipal = Depends(_dpo_roles)
):
    return await update_dsr_by_id(
        dsr_id=dsr_id, tenant_id=principal.tenant_id or "", dsr_data=dsr_data
    )
