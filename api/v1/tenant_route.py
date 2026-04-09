from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.errors import auth_permission_denied, auth_role_mismatch
from core.response_envelope import document_response
from schemas.tenant_schema import TenantCreate, TenantUpdate, TenantOut, TenantWithSummaryOut
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_any_token, verify_super_admin_token
from security.principal import AuthPrincipal
from services.tenant_service import (
    add_tenant,
    retrieve_tenant_by_id,
    retrieve_tenant_by_id_with_summary,
    retrieve_tenants,
    retrieve_tenants_with_summary,
    update_tenant_by_id,
)

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.post("/")
@document_response(
    message="Tenant created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Create a new tenant in the system. Only application admins can create tenants. For bootstrapping a tenant with its first super_admin, use POST /admins/tenants/bootstrap instead.",
    summary="Create a new tenant (application admin only)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "company_name": "Acme Corp",
        "lawful_basis": "legitimate_interest",
        "notice_display_mode": "passive",
        "retention_days": 1095,
        "default_retention_action": "anonymise",
        "dpo_contact_email": "dpo@acmecorp.com",
        "privacy_policy_url": "https://acmecorp.com/privacy",
        "country_of_hosting": "United States",
        "cross_border_approved": False,
        "is_active": True,
        "active_notice_version": "1.0",
        "date_created": 1712500000,
        "last_updated": 1712500000
    },
    response_codes={401: "Unauthorized", 403: "Insufficient permissions", 422: "Validation error"},
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def create_tenant_endpoint(
    tenant_data: TenantCreate,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await add_tenant(tenant_data=tenant_data)


@router.get("/")
@document_response(
    message="Tenants fetched successfully",
    description="Retrieve a paginated list of all tenants. Only super-admin users can list tenants.",
    summary="List all tenants",
    success_example=[{
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "company_name": "Acme Corp",
        "lawful_basis": "legitimate_interest",
        "notice_display_mode": "passive",
        "retention_days": 1095,
        "default_retention_action": "anonymise",
        "dpo_contact_email": "dpo@acmecorp.com",
        "privacy_policy_url": "https://acmecorp.com/privacy",
        "country_of_hosting": "United States",
        "cross_border_approved": False,
        "is_active": True,
        "active_notice_version": "1.0",
        "date_created": 1712500000,
        "last_updated": 1712500000
    }],
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"}
    }
)
async def list_tenants(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await retrieve_tenants_with_summary(start=start, stop=stop)


@router.get("/{tenant_id}")
@document_response(
    message="Tenant fetched successfully",
    description="Retrieve a specific tenant by ID. Only super-admin users can view tenant details.",
    summary="Retrieve tenant by ID",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "company_name": "Acme Corp",
        "lawful_basis": "legitimate_interest",
        "notice_display_mode": "passive",
        "retention_days": 1095,
        "default_retention_action": "anonymise",
        "dpo_contact_email": "dpo@acmecorp.com",
        "privacy_policy_url": "https://acmecorp.com/privacy",
        "country_of_hosting": "United States",
        "cross_border_approved": False,
        "is_active": True,
        "active_notice_version": "1.0",
        "date_created": 1712500000,
        "last_updated": 1712500000
    },
    response_codes={401: "Unauthorized", 403: "Insufficient permissions", 404: "Tenant not found"},
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "Tenant not found", "code": "RESOURCE_NOT_FOUND"}
    }
)
async def get_tenant_endpoint(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    if principal.role == "admin":
        pass  # application admin can view any tenant
    elif principal.role == "super_admin":
        if principal.tenant_id != tenant_id:
            raise auth_permission_denied()
    else:
        raise auth_role_mismatch(required_role="admin", actual_role=principal.role)
    return await retrieve_tenant_by_id_with_summary(tenant_id=tenant_id)


@router.patch("/{tenant_id}")
@document_response(
    message="Tenant updated successfully",
    description="Update tenant details partially. Only super-admin users can update tenants.",
    summary="Update tenant by ID",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "company_name": "Acme Corp Updated",
        "lawful_basis": "legitimate_interest",
        "notice_display_mode": "active",
        "retention_days": 1095,
        "default_retention_action": "anonymise",
        "dpo_contact_email": "dpo@acmecorp.com",
        "privacy_policy_url": "https://acmecorp.com/privacy",
        "country_of_hosting": "United States",
        "cross_border_approved": False,
        "is_active": True,
        "active_notice_version": "1.1",
        "date_created": 1712500000,
        "last_updated": 1712600000
    },
    response_codes={401: "Unauthorized", 403: "Insufficient permissions", 404: "Tenant not found", 422: "Validation error"},
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "Tenant not found", "code": "RESOURCE_NOT_FOUND"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def update_tenant_endpoint(
    tenant_id: str,
    tenant_data: TenantUpdate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    return await update_tenant_by_id(tenant_id=tenant_id, tenant_data=tenant_data)
