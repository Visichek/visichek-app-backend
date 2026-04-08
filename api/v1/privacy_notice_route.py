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
@document_response(
    message="Privacy notice created successfully",
    status_code=status.HTTP_201_CREATED,
    summary="Create privacy notice",
    description="Create a new privacy notice. Only super_admin and dpo roles can create notices.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "version_code": "v2.1",
        "title": "Data Processing Notice",
        "summary": "Information about how we process your personal data",
        "full_policy_url": "https://example.com/privacy-policy",
        "effective_from": 1712448000,
        "effective_to": None,
        "is_active": True,
        "date_created": 1712448000
    },
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid payload"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def create_privacy_notice_endpoint(
    notice_data: PrivacyNoticeCreate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    if principal.tenant_id:
        notice_data.tenant_id = principal.tenant_id
    return await add_privacy_notice(notice_data=notice_data)


@router.get("/active")
@document_response(
    message="Active privacy notice fetched successfully",
    summary="Get active privacy notice",
    description="Retrieve the currently active privacy notice for the tenant.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "version_code": "v2.1",
        "title": "Data Processing Notice",
        "summary": "Information about how we process your personal data",
        "full_policy_url": "https://example.com/privacy-policy",
        "effective_from": 1712448000,
        "effective_to": None,
        "is_active": True,
        "date_created": 1712448000
    },
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 404: "Notice not found"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "Privacy notice not found", "code": "RESOURCE_NOT_FOUND"}
    }
)
async def get_active_notice(
    principal: AuthPrincipal = Depends(verify_system_user_token(
        "super_admin", "dpo", "receptionist", "dept_admin"
    )),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_active_notice(tenant_id=tenant_id)


@router.get("/")
@document_response(
    message="Privacy notices fetched successfully",
    summary="List privacy notices",
    description="Retrieve all privacy notices for the tenant with pagination support.",
    success_example=[{
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "version_code": "v2.1",
        "title": "Data Processing Notice",
        "summary": "Information about how we process your personal data",
        "full_policy_url": "https://example.com/privacy-policy",
        "effective_from": 1712448000,
        "effective_to": None,
        "is_active": True,
        "date_created": 1712448000
    }],
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 422: "Invalid query"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Invalid pagination parameters", "code": "VALIDATION_FAILED"}
    }
)
async def list_privacy_notices(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_privacy_notices(tenant_id=tenant_id, start=start, stop=stop)


@router.patch("/{notice_id}")
@document_response(
    message="Privacy notice updated successfully",
    summary="Update privacy notice",
    description="Update an existing privacy notice with new content and metadata.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "tenant_001",
        "version_code": "v2.1",
        "title": "Data Processing Notice",
        "summary": "Information about how we process your personal data",
        "full_policy_url": "https://example.com/privacy-policy",
        "effective_from": 1712448000,
        "effective_to": None,
        "is_active": True,
        "date_created": 1712448000
    },
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions", 404: "Notice not found", 422: "Invalid payload"},
    error_examples={
        401: {"success": False, "message": "Token validation failed", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "Privacy notice not found", "code": "RESOURCE_NOT_FOUND"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def update_privacy_notice_endpoint(
    notice_id: str,
    notice_data: PrivacyNoticeUpdate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await update_notice_by_id(notice_id=notice_id, tenant_id=tenant_id, notice_data=notice_data)
