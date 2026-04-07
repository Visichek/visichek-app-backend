from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.system_user_schema import (
    SystemUserCreate,
    SystemUserUpdate,
    SystemUserOut,
    SystemUserLogin,
    SystemUserRefresh,
)
from security.auth import (
    verify_any_system_user_token,
    verify_super_admin_token,
    verify_system_user_refresh_token,
)
from security.principal import AuthPrincipal
from services.system_user_service import (
    add_system_user,
    authenticate_system_user,
    refresh_system_user_tokens,
    retrieve_system_user_by_id,
    retrieve_system_users,
    update_system_user_by_id,
    remove_system_user,
)

router = APIRouter(prefix="/system-users", tags=["System Users"])


@router.post("/login")
@document_response(
    message="Login successful",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d5",
        "tenant_id": "tenant-123",
        "department_id": "dept-456",
        "full_name": "Dr. Sarah Wilson",
        "email": "sarah.wilson@clinic.example.com",
        "role": "receptionist",
        "account_status": "ACTIVE",
        "is_active": True,
        "last_login_at": 1712520000,
        "date_created": 1712500000,
        "last_updated": 1712520000,
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDUiLCJyb2xlIjoicmVjZXB0aW9uaXN0In0.jkl345",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDUiLCJ0eXBlIjoicmVmcmVzaCJ9.mno678"
    },
    description="Authenticate a system user with email and password. Returns access and refresh tokens.",
    summary="System user login",
    response_codes={
        401: "Unauthorized - invalid credentials",
        422: "Validation error - missing or invalid email/password",
    },
    error_examples={
        401: {"success": False, "message": "Invalid email or password", "code": "AUTH_INVALID_TOKEN"},
        422: {"success": False, "message": "Email and password are required", "code": "VALIDATION_FAILED"},
    },
)
async def login_system_user(login_data: SystemUserLogin):
    return await authenticate_system_user(login_data=login_data)


@router.post("/signup")
@document_response(
    message="System user created successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d6",
        "tenant_id": "tenant-123",
        "department_id": "dept-456",
        "full_name": "Dr. Michael Brown",
        "email": "michael.brown@clinic.example.com",
        "role": "dept_admin",
        "account_status": "ACTIVE",
        "is_active": True,
        "last_login_at": None,
        "date_created": 1712521000,
        "last_updated": 1712521000
    },
    status_code=status.HTTP_201_CREATED,
    description="Create a new system user account. Only super admins can create system users. The tenant_id is automatically set from the super admin's tenant.",
    summary="Create system user",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions (must be super admin)",
        409: "Conflict - system user with this email already exists",
        422: "Validation error - invalid input data",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission to perform this action", "code": "AUTH_PERMISSION_DENIED"},
        409: {"success": False, "message": "System user with this email already exists", "code": "VALIDATION_FAILED"},
        422: {"success": False, "message": "Email and password are required", "code": "VALIDATION_FAILED"},
    },
)
async def signup_system_user(
    user_data: SystemUserCreate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    # Ensure tenant_id matches the super admin's tenant
    if principal.tenant_id:
        user_data.tenant_id = principal.tenant_id
    return await add_system_user(user_data=user_data)


@router.post("/refresh")
@document_response(
    message="Tokens refreshed successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d5",
        "tenant_id": "tenant-123",
        "department_id": "dept-456",
        "full_name": "Dr. Sarah Wilson",
        "email": "sarah.wilson@clinic.example.com",
        "role": "receptionist",
        "account_status": "ACTIVE",
        "is_active": True,
        "last_login_at": 1712520000,
        "date_created": 1712500000,
        "last_updated": 1712520000,
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDUiLCJyb2xlIjoicmVjZXB0aW9uaXN0In0.jkl345",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDUiLCJ0eXBlIjoicmVmcmVzaCJ9.mno678"
    },
    description="Refresh expired access tokens using a valid refresh token. Expired access token must be provided in Authorization header.",
    summary="Refresh system user tokens",
    response_codes={
        401: "Unauthorized - invalid or mismatched tokens",
        422: "Validation error - missing refresh token",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired refresh token", "code": "AUTH_INVALID_TOKEN"},
        422: {"success": False, "message": "Refresh token is required", "code": "VALIDATION_FAILED"},
    },
)
async def refresh_tokens(
    refresh_data: SystemUserRefresh,
    principal: AuthPrincipal = Depends(verify_system_user_refresh_token),
):
    return await refresh_system_user_tokens(
        refresh_data=refresh_data,
        expired_access_token=principal.access_token_id,
    )


@router.get("/me")
@document_response(
    message="Profile fetched successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d5",
        "tenant_id": "tenant-123",
        "department_id": "dept-456",
        "full_name": "Dr. Sarah Wilson",
        "email": "sarah.wilson@clinic.example.com",
        "role": "receptionist",
        "account_status": "ACTIVE",
        "is_active": True,
        "last_login_at": 1712520000,
        "date_created": 1712500000,
        "last_updated": 1712520000
    },
    description="Retrieve the authenticated system user's profile information.",
    summary="Get system user profile",
    response_codes={
        401: "Unauthorized - invalid or missing token",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
    },
)
async def get_my_profile(
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    return await retrieve_system_user_by_id(user_id=principal.user_id)


@router.get("/")
@document_response(
    message="System users fetched successfully",
    success_example=[
        {
            "id": "64f1a2b3c4d5e6f7a8b9c0d5",
            "tenant_id": "tenant-123",
            "department_id": "dept-456",
            "full_name": "Dr. Sarah Wilson",
            "email": "sarah.wilson@clinic.example.com",
            "role": "receptionist",
            "account_status": "ACTIVE",
            "is_active": True,
            "last_login_at": 1712520000,
            "date_created": 1712500000,
            "last_updated": 1712520000
        }
    ],
    description="Retrieve a paginated list of system users belonging to the super admin's tenant.",
    summary="List system users",
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions (must be super admin)",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission to perform this action", "code": "AUTH_PERMISSION_DENIED"},
    },
)
async def list_system_users(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_system_users(tenant_id=tenant_id, start=start, stop=stop)


@router.patch("/{user_id}")
@document_response(
    message="System user updated successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d5",
        "tenant_id": "tenant-123",
        "department_id": "dept-789",
        "full_name": "Dr. Sarah Wilson",
        "email": "sarah.wilson@clinic.example.com",
        "role": "dept_admin",
        "account_status": "ACTIVE",
        "is_active": True,
        "last_login_at": 1712520000,
        "date_created": 1712500000,
        "last_updated": 1712521500
    },
    description="Update an existing system user's information. Only super admins can update system users within their tenant.",
    summary="Update system user",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions (must be super admin)",
        404: "Not found - system user does not exist",
        422: "Validation error - invalid input data",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission to perform this action", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "System user not found", "code": "RESOURCE_NOT_FOUND"},
        422: {"success": False, "message": "Invalid input data", "code": "VALIDATION_FAILED"},
    },
)
async def update_system_user_endpoint(
    user_id: str,
    user_data: SystemUserUpdate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await update_system_user_by_id(
        user_id=user_id, tenant_id=tenant_id, user_data=user_data
    )


@router.delete("/{user_id}")
@document_response(
    message="System user deleted successfully",
    success_example={"deleted": True},
    description="Delete a system user. Only super admins can delete system users within their tenant. This action is irreversible.",
    summary="Delete system user",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions (must be super admin)",
        404: "Not found - system user does not exist",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission to perform this action", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "System user not found", "code": "RESOURCE_NOT_FOUND"},
    },
)
async def delete_system_user_endpoint(
    user_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await remove_system_user(user_id=user_id, tenant_id=tenant_id)
