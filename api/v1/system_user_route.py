from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from core.response_envelope import document_response, success_payload
from core.settings import get_settings
from schemas.system_user_schema import (
    SystemUserSignupRequest,
    SystemUserTenantLogin,
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
from schemas.otp_schema import MfaAdminUpdate, MfaSettingsUpdate, OtpVerifyRequest
from security.cookie_utils import set_auth_cookies, clear_auth_cookies, REFRESH_TOKEN_COOKIE
from security.principal import AuthPrincipal
from services.system_user_service import (
    add_system_user_from_invite,
    admin_set_user_mfa,
    authenticate_system_user,
    authenticate_super_admin_global,
    authenticate_system_user_by_tenant,
    refresh_system_user_tokens,
    retrieve_system_user_by_id,
    retrieve_system_users,
    toggle_user_mfa,
    update_system_user_by_id,
    remove_system_user,
    verify_system_user_otp,
)

router = APIRouter(prefix="/system-users", tags=["Tenant Users"])


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
        "access_token": "eyJhbGci...",
        "refresh_token": "eyJhbGci...",
    },
    description="Authenticate a system user with email and password (global — not scoped to tenant). Returns access and refresh tokens.",
    summary="System user login (global)",
    response_codes={
        401: "Unauthorized - invalid credentials",
        403: "Forbidden - account not active",
        429: "Too many failed attempts - account temporarily locked",
        422: "Validation error - missing or invalid email/password",
    },
    error_examples={
        401: {"success": False, "message": "Invalid login credentials", "code": "AUTH_INVALID_TOKEN"},
        429: {"success": False, "message": "Account temporarily locked", "code": "TOO_MANY_REQUESTS"},
    },
)
async def login_system_user(request: Request, login_data: SystemUserLogin):
    result = await authenticate_system_user(login_data=login_data)
    request_id = getattr(request.state, "request_id", None)

    if isinstance(result, dict) and result.get("otp_required"):
        return JSONResponse(
            content=jsonable_encoder(success_payload(result, message="OTP verification required", request_id=request_id)),
        )

    user = result
    is_prod = get_settings().env == "production"
    response = JSONResponse(
        content=jsonable_encoder(success_payload(user, message="Login successful", request_id=request_id)),
    )
    set_auth_cookies(response, user.access_token, user.refresh_token, is_production=is_prod) # type: ignore
    return response


@router.post("/super-admin/login")
@document_response(
    message="Super admin login successful",
    success_example={
        "user": {
            "id": "64f1a2b3c4d5e6f7a8b9c0d5",
            "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "full_name": "Dr. Sarah Wilson",
            "email": "sarah.wilson@clinic.example.com",
            "role": "super_admin",
            "account_status": "ACTIVE",
            "access_token": "eyJhbGci...",
            "refresh_token": "eyJhbGci...",
        },
        "tenant": {
            "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "company_name": "Acme Clinic",
        },
        "tenant_login_url": "/v1/system-users/tenant/64f1a2b3c4d5e6f7a8b9c0d1/login",
    },
    description=(
        "Dedicated login for tenant super admins. Since super_admin emails are "
        "globally unique, no tenant_id is needed in the URL. Returns the user "
        "profile plus tenant context info including the tenant-scoped login URL. "
        "\n\n"
        "**Two login contexts for super admins:**\n"
        "1. **This endpoint** — administrative login. View tenant info, billing, "
        "manage payments, and get the tenant login URL.\n"
        "2. **Tenant-scoped login** (`/system-users/tenant/{tenant_id}/login`) — "
        "operational login. Manage visitors, departments, branding, and other "
        "day-to-day tenant operations."
    ),
    summary="Super admin login (global)",
    response_codes={
        401: "Unauthorized - invalid credentials",
        403: "Forbidden - not a super admin or account not active",
        429: "Too many failed attempts - account temporarily locked",
    },
    error_examples={
        401: {"success": False, "message": "Invalid login credentials", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "This login endpoint is reserved for tenant super admins", "code": "AUTH_PERMISSION_DENIED"},
    },
)
async def login_super_admin_global(request: Request, login_data: SystemUserLogin):
    result = await authenticate_super_admin_global(login_data=login_data)
    request_id = getattr(request.state, "request_id", None)

    # authenticate_super_admin_global calls authenticate_system_user internally
    # which may return an otp_required dict
    if isinstance(result, dict) and result.get("otp_required"):
        return JSONResponse(
            content=jsonable_encoder(success_payload(result, message="OTP verification required", request_id=request_id)),
        )

    user = result["user"]
    is_prod = get_settings().env == "production"
    response = JSONResponse(
        content=jsonable_encoder(success_payload(result, message="Super admin login successful", request_id=request_id)),
    )
    set_auth_cookies(response, user.access_token, user.refresh_token, is_production=is_prod)
    return response


@router.post("/tenant/{tenant_id}/login")
@document_response(
    message="Login successful",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d5",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "department_id": "dept-456",
        "full_name": "Dr. Sarah Wilson",
        "email": "sarah.wilson@clinic.example.com",
        "role": "receptionist",
        "account_status": "ACTIVE",
        "is_active": True,
        "last_login_at": 1712520000,
        "date_created": 1712500000,
        "last_updated": 1712520000,
        "access_token": "eyJhbGci...",
        "refresh_token": "eyJhbGci...",
    },
    description=(
        "Authenticate a system user scoped to a specific tenant. "
        "The tenant_id in the URL path restricts the lookup so users "
        "with the same email in different tenants don't collide. "
        "Returns access and refresh tokens."
    ),
    summary="System user login (tenant-scoped)",
    response_codes={
        401: "Unauthorized - invalid credentials",
        403: "Forbidden - account not active",
        404: "Not found - tenant does not exist",
        429: "Too many failed attempts - account temporarily locked",
        422: "Validation error - missing or invalid email/password",
    },
    error_examples={
        401: {"success": False, "message": "Invalid login credentials", "code": "AUTH_INVALID_TOKEN"},
        404: {"success": False, "message": "Tenant not found", "code": "RESOURCE_NOT_FOUND"},
        429: {"success": False, "message": "Account temporarily locked", "code": "TOO_MANY_REQUESTS"},
    },
)
async def login_system_user_by_tenant(request: Request, tenant_id: str, login_data: SystemUserTenantLogin):
    result = await authenticate_system_user_by_tenant(login_data=login_data, tenant_id=tenant_id)
    request_id = getattr(request.state, "request_id", None)

    if isinstance(result, dict) and result.get("otp_required"):
        return JSONResponse(
            content=jsonable_encoder(success_payload(result, message="OTP verification required", request_id=request_id)),
        )

    user = result
    is_prod = get_settings().env == "production"
    response = JSONResponse(
        content=jsonable_encoder(success_payload(user, message="Login successful", request_id=request_id)),
    )
    set_auth_cookies(response, user.access_token, user.refresh_token, is_production=is_prod) # type: ignore
    return response


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
        "last_updated": 1712521000,
    },
    status_code=status.HTTP_201_CREATED,
    description=(
        "Invite a new system user. Only super admins can create system users. "
        "The tenant_id is automatically set from the super admin's tenant. "
        "Account status and permissions are system-assigned based on the chosen role."
    ),
    summary="Invite system user",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions (must be super admin)",
        409: "Conflict - system user with this email already exists in this tenant",
        422: "Validation error - invalid input data or weak password",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission to perform this action", "code": "AUTH_PERMISSION_DENIED"},
        409: {"success": False, "message": "A user with this email already exists in this tenant", "code": "VALIDATION_FAILED"},
        422: {"success": False, "message": "Password does not meet strength requirements", "code": "VALIDATION_FAILED"},
    },
)
async def signup_system_user(
    signup_data: SystemUserSignupRequest,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await add_system_user_from_invite(signup_data=signup_data, tenant_id=tenant_id)


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
        "access_token": "eyJhbGci...",
        "refresh_token": "eyJhbGci...",
    },
    description="Refresh expired access tokens using a valid refresh token. Expired access token must be provided in Authorization header.",
    summary="Refresh system user tokens",
    response_codes={
        401: "Unauthorized - invalid or mismatched tokens",
        422: "Validation error - missing refresh token",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired refresh token", "code": "AUTH_INVALID_TOKEN"},
    },
)
async def refresh_tokens(
    request: Request,
    refresh_data: SystemUserRefresh,
    principal: AuthPrincipal = Depends(verify_system_user_refresh_token),
):
    if not refresh_data.refresh_token:
        refresh_data.refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE, "")

    user = await refresh_system_user_tokens(
        refresh_data=refresh_data,
        expired_access_token=principal.access_token_id,
    )

    is_prod = get_settings().env == "production"
    request_id = getattr(request.state, "request_id", None)
    response = JSONResponse(
        content=jsonable_encoder(success_payload(user, message="Tokens refreshed successfully", request_id=request_id)),
    )
    set_auth_cookies(response, user.access_token, user.refresh_token, is_production=is_prod) # type: ignore
    return response


@router.post("/verify-otp")
@document_response(
    message="OTP verified, login successful",
    description="Step 2 of 2FA login. Verify the OTP code and receive access/refresh tokens.",
    summary="Verify system user OTP",
    response_codes={
        401: "Unauthorized - invalid or expired OTP",
        429: "Too many OTP attempts",
    },
)
async def verify_system_user_otp_endpoint(request: Request, otp_data: OtpVerifyRequest):
    user = await verify_system_user_otp(otp_data.otp_challenge_id, otp_data.otp_code)

    is_prod = get_settings().env == "production"
    request_id = getattr(request.state, "request_id", None)
    response = JSONResponse(
        content=jsonable_encoder(success_payload(user, message="OTP verified, login successful", request_id=request_id)),
    )
    set_auth_cookies(response, user.access_token, user.refresh_token, is_production=is_prod) # type: ignore
    return response


@router.patch("/me/mfa")
@document_response(
    message="MFA setting updated",
    description="Toggle your own 2FA setting. Blocked if locked by admin or tenant policy.",
    summary="Toggle own MFA",
    response_codes={403: "Forbidden - MFA locked by admin or tenant policy"},
)
async def toggle_my_mfa(
    settings: MfaSettingsUpdate,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    return await toggle_user_mfa(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id or "",
        mfa_enabled=settings.mfa_enabled,
    )


@router.post("/logout")
@document_response(
    message="Logged out successfully",
    description="Clear auth cookies and invalidate the current session.",
    summary="System user logout",
)
async def logout_system_user(request: Request):
    is_prod = get_settings().env == "production"
    request_id = getattr(request.state, "request_id", None)
    response = JSONResponse(
        content=jsonable_encoder(success_payload(None, message="Logged out successfully", request_id=request_id)),
    )
    clear_auth_cookies(response, is_production=is_prod)
    return response


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
        "last_updated": 1712520000,
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
            "last_updated": 1712520000,
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
        "last_updated": 1712521500,
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
