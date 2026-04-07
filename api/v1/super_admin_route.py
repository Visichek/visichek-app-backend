from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.dashboard_service import get_dashboard_stats
from services.department_service import retrieve_departments, add_department
from services.system_user_service import retrieve_system_users, add_system_user
from schemas.department_schema import DepartmentCreate
from schemas.system_user_schema import SystemUserCreate

router = APIRouter(prefix="/super-admin", tags=["Tenant Super Admin"])


@router.get("/analytics")
@document_response(
    message="Company-wide analytics fetched successfully",
    description="Retrieve company-wide analytics and dashboard statistics. Only super-admin users can view analytics.",
    summary="Get company-wide analytics",
    success_example={
        "total_tenants": 12,
        "total_departments": 48,
        "total_system_users": 156,
        "active_users_today": 89,
        "total_documents_processed": 4250,
        "documents_pending": 23,
        "system_uptime_percent": 99.8,
        "average_response_time_ms": 145,
        "gdpr_requests_this_month": 5,
        "data_retention_compliance_percent": 100.0
    },
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"}
    }
)
async def company_analytics(
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await get_dashboard_stats(tenant_id=tenant_id)


@router.get("/departments")
@document_response(
    message="All departments fetched successfully",
    description="Retrieve a paginated list of all departments across all tenants. Only super-admin users can access this endpoint.",
    summary="List all departments",
    success_example=[{
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "code": "HR-001",
        "name": "Human Resources",
        "is_active": True,
        "created_by": "64f1a2b3c4d5e6f7a8b9c0d3",
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
async def list_all_departments(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_departments(tenant_id=tenant_id, start=start, stop=stop)


@router.post("/departments")
@document_response(
    message="Department created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Create a new department for a tenant. Only super-admin users can create departments.",
    summary="Create a new department",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "code": "HR-001",
        "name": "Human Resources",
        "is_active": True,
        "created_by": "64f1a2b3c4d5e6f7a8b9c0d3",
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
async def create_department(
    dept_data: DepartmentCreate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    if principal.tenant_id:
        dept_data.tenant_id = principal.tenant_id
    return await add_department(dept_data=dept_data, created_by=principal.user_id)


@router.get("/admins")
@document_response(
    message="All system users fetched successfully",
    description="Retrieve a paginated list of all system users (admins) across all tenants. Only super-admin users can access this endpoint.",
    summary="List all system users",
    success_example=[{
        "id": "64f1a2b3c4d5e6f7a8b9c0d3",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "department_id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "full_name": "John Doe",
        "email": "john.doe@acmecorp.com",
        "role": "dept_admin",
        "account_status": "active",
        "is_active": True,
        "last_login_at": 1712500000,
        "date_created": 1712400000,
        "last_updated": 1712500000
    }],
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"}
    }
)
async def list_all_admins(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_system_users(tenant_id=tenant_id, start=start, stop=stop)


@router.post("/admins/invite")
@document_response(
    message="System user invited successfully",
    status_code=status.HTTP_201_CREATED,
    description="Invite a new system user (admin) to the platform. Only super-admin users can invite system users.",
    summary="Invite a new system user",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d4",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "department_id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "full_name": "Jane Smith",
        "email": "jane.smith@acmecorp.com",
        "role": "receptionist",
        "account_status": "active",
        "is_active": True,
        "last_login_at": None,
        "date_created": 1712500000,
        "last_updated": 1712500000,
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDQiLCJyb2xlIjoicmVjZXB0aW9uaXN0In0.X2Y3Z4A5B6C7D8E9F0G1H2I3J4K5L6M7",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDQiLCJ0eXBlIjoicmVmcmVzaCJ9.N2O3P4Q5R6S7T8U9V0W1X2Y3Z4A5B6C7"
    },
    response_codes={401: "Unauthorized", 403: "Insufficient permissions", 409: "Already exists", 422: "Validation error"},
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "Insufficient permissions", "code": "AUTH_PERMISSION_DENIED"},
        409: {"success": False, "message": "User already exists", "code": "ALREADY_EXISTS"},
        422: {"success": False, "message": "Validation error", "code": "VALIDATION_FAILED"}
    }
)
async def invite_admin(
    user_data: SystemUserCreate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    if principal.tenant_id:
        user_data.tenant_id = principal.tenant_id
    return await add_system_user(user_data=user_data)
