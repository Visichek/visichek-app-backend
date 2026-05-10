from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.response_envelope import document_response, success_payload
from core.settings import get_settings
from schemas.admin_schema import (
    AdminLogin,
    AdminOut,
    AdminRefresh,
    AdminSearchResult,
    AdminSignupRequest,
)
from schemas.session_schema import AddSuperAdminRequest, ResetPasswordRequest
from schemas.tenant_schema import TenantBootstrapRequest
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_admin_refresh_token
from security.cookie_utils import (
    build_auth_response,
    clear_auth_cookies,
    REFRESH_TOKEN_COOKIE,
)
from security.principal import AuthPrincipal
from schemas.otp_schema import OtpVerifyRequest
from services.admin_service import (
    add_admin,
    authenticate_admin,
    refresh_admin_tokens_reduce_number_of_logins,
    remove_admin,
    retrieve_admins,
    search_admins_by_query,
    verify_admin_otp,
)
from services.tenant_service import bootstrap_tenant
from services.tenant_offboarding_service import offboard_tenant, get_offboarding_summary


class OffboardingRequest(BaseModel):
    """Request to offboard (deactivate) a tenant."""

    reason: str = "admin_initiated"


class OffboardingSummary(BaseModel):
    """Summary of actions taken during offboarding."""

    tenant_id: str
    offboarded_at: int
    reason: str
    actions: dict


router = APIRouter(prefix="/admins", tags=["Application Admins"])


@router.get(
    "/",
    dependencies=[
        Depends(check_admin_account_status_and_permissions),
    ],
)
@document_response(
    message="Admins fetched successfully",
    success_example=[
        {
            "id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "full_name": "John Admin",
            "email": "admin@example.com",
            "accountStatus": "ACTIVE",
            "permissionList": {
                "permissions": [
                    {
                        "name": "Read Users",
                        "methods": ["GET"],
                        "path": "/v1/users",
                        "key": "users_read",
                        "description": "Retrieve user list",
                    }
                ]
            },
            "date_created": 1712500000,
            "last_updated": 1712500600,
        }
    ],
    description="Retrieve a paginated list of all admins in the system.",
    summary="List all admins",
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "You do not have permission to perform this action",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def list_admins(
    start: Annotated[
        int,
        Query(ge=0, description="The starting index (offset) for the list of admins."),
    ],
    stop: Annotated[
        int,
        Query(gt=0, description="The ending index for the list of admins (limit)."),
    ],
):
    items = await retrieve_admins(start=start, stop=stop)
    return items


@router.get("/search")
@document_response(
    message="Admin search results",
    description=(
        "Search application admins by id, email, or full name. Case-insensitive, "
        "substring match against email and name; exact match when the query is a "
        "valid ObjectId. Returns a trimmed view with only the fields needed to "
        "identify an admin — no passwords, tokens, or permission lists."
    ),
    summary="Search admins by id, email, or name",
    include_meta=True,
    success_example=[
        {
            "id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "full_name": "John Admin",
            "email": "admin@example.com",
            "account_status": "ACTIVE",
            "mfa_enabled": True,
            "date_created": 1712500000,
            "last_updated": 1712500600,
        }
    ],
    response_codes={
        400: "Bad Request - empty search query",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        400: {
            "success": False,
            "message": "Search query 'q' must not be empty",
            "code": "VALIDATION_FAILED",
        },
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "You do not have permission to perform this action",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def search_admins_endpoint(
    q: Annotated[
        str,
        Query(
            min_length=1,
            description="Search term — matched against admin id, email, or full name.",
        ),
    ],
    start: Annotated[
        int,
        Query(ge=0, description="Pagination offset."),
    ] = 0,
    stop: Annotated[
        int,
        Query(gt=0, le=200, description="Pagination limit (exclusive end index)."),
    ] = 50,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> list[AdminSearchResult]:
    return await search_admins_by_query(query=q, start=start, stop=stop)


@router.get("/profile")
@document_response(
    message="Admin profile fetched successfully",
    description="Retrieve the authenticated admin's profile information.",
    summary="Get admin profile",
    response_codes={401: "Unauthorized - invalid or missing token"},
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        }
    },
)
async def get_my_admin(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    return admin


@router.post("/signup")
@document_response(
    message="Admin created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Invite a new admin. Account status and permissions are system-assigned.",
    summary="Invite new admin",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        409: "Conflict - admin with this email already exists",
        422: "Validation error - invalid input data or weak password",
    },
    error_examples={
        409: {
            "success": False,
            "message": "Admin already exists",
            "code": "VALIDATION_FAILED",
        },
        422: {
            "success": False,
            "message": "Password does not meet strength requirements",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def signup_new_admin(
    signup_data: AdminSignupRequest,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    items = await add_admin(signup_data=signup_data, invited_by=admin.id)  # type: ignore
    return items


@router.post("/tenants/bootstrap")
@document_response(
    message="Tenant and super admin created successfully",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Bootstrap a new tenant along with its first super_admin system user. "
        "Only application admins can call this endpoint. Once a super_admin exists "
        "for a tenant, all further user management is handled by that super_admin."
    ),
    summary="Bootstrap tenant + first super admin",
    success_example={
        "tenant": {
            "id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "company_name": "Acme Corp",
            "lawful_basis": "legitimate_interest",
        },
        "super_admin": {
            "id": "64f1a2b3c4d5e6f7a8b9c0d2",
            "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "full_name": "Jane Doe",
            "email": "jane@acmecorp.com",
            "role": "super_admin",
            "account_status": "ACTIVE",
        },
    },
    response_codes={
        409: "Conflict - tenant company name already exists",
        422: "Validation error - invalid input data",
    },
    error_examples={
        409: {
            "success": False,
            "message": "Tenant with this company name already exists",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def bootstrap_tenant_endpoint(
    payload: TenantBootstrapRequest,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    """Create a new tenant and its first super_admin in a single request."""
    return await bootstrap_tenant(payload)


@router.post("/login")
@document_response(
    message="Admin login successful",
    description="Authenticate an admin with email and password. Returns access and refresh tokens.",
    summary="Admin login",
    response_codes={
        401: "Unauthorized - invalid credentials",
        429: "Too many failed attempts - account temporarily locked",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid login credentials",
            "code": "AUTH_INVALID_TOKEN",
        },
        429: {
            "success": False,
            "message": "Account temporarily locked",
            "code": "TOO_MANY_REQUESTS",
        },
    },
)
async def login_admin(request: Request, admin_data: AdminLogin):
    result = await authenticate_admin(admin_data=admin_data)  # type: ignore
    request_id = getattr(request.state, "request_id", None)

    # 2FA required — return challenge, no tokens
    if isinstance(result, dict) and result.get("otp_required"):
        return JSONResponse(
            content=jsonable_encoder(
                success_payload(
                    result, message="OTP verification required", request_id=request_id
                )
            ),
        )

    # No 2FA — tokens are set as httpOnly cookies; JSON body includes them
    # only when the caller sends ``X-Auth-Include-Tokens: 1`` (see
    # security/cookie_utils.build_auth_response).
    admin = result
    is_prod = get_settings().env == "production"
    return build_auth_response(
        request=request,
        payload=admin,
        message="Admin login successful",
        is_production=is_prod,
    )


@router.post("/refresh")
@document_response(
    message="Admin tokens refreshed successfully",
    description="Refresh expired access tokens using a valid refresh token.",
    summary="Refresh admin tokens",
    response_codes={
        401: "Unauthorized - invalid or mismatched tokens",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired refresh token",
            "code": "AUTH_INVALID_TOKEN",
        },
    },
)
async def refresh_admin_tokens(
    request: Request,
    admin_data: Annotated[
        AdminRefresh,
        Body(
            openapi_examples={
                "successful_refresh": {
                    "summary": "Successful Token Refresh",
                    "value": {"refresh_token": "valid.long.lived.refresh.token.98765"},
                },
            }
        ),
    ],
    principal: AuthPrincipal = Depends(verify_admin_refresh_token),
):
    # Allow refresh token from cookie if not provided in body
    if not admin_data.refresh_token:
        admin_data.refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE, "")

    admin = await refresh_admin_tokens_reduce_number_of_logins(
        admin_refresh_data=admin_data,
        expired_access_token=principal.access_token_id,
    )
    admin.password = ""

    is_prod = get_settings().env == "production"
    return build_auth_response(
        request=request,
        payload=admin,
        message="Admin tokens refreshed successfully",
        is_production=is_prod,
    )


@router.post("/verify-otp")
@document_response(
    message="OTP verified, login successful",
    description="Step 2 of 2FA login. Verify the OTP code and receive access/refresh tokens.",
    summary="Verify admin OTP",
    response_codes={
        401: "Unauthorized - invalid or expired OTP",
        429: "Too many OTP attempts",
    },
)
async def verify_admin_otp_endpoint(request: Request, otp_data: OtpVerifyRequest):
    admin = await verify_admin_otp(otp_data.otp_challenge_id, otp_data.otp_code)

    is_prod = get_settings().env == "production"
    return build_auth_response(
        request=request,
        payload=admin,
        message="OTP verified, login successful",
        is_production=is_prod,
    )


@router.post("/logout")
@document_response(
    message="Logged out successfully",
    description="Clear auth cookies and invalidate the current session.",
    summary="Admin logout",
)
async def logout_admin(request: Request):
    is_prod = get_settings().env == "production"
    request_id = getattr(request.state, "request_id", None)
    response = JSONResponse(
        content=jsonable_encoder(
            success_payload(
                None, message="Logged out successfully", request_id=request_id
            )
        ),
    )
    clear_auth_cookies(response, is_production=is_prod)
    return response


@router.delete("/account")
@document_response(
    message="Admin account deleted successfully",
    success_example={"deleted": True},
    description="Delete the authenticated admin's account. This action is irreversible.",
    summary="Delete admin account",
    response_codes={401: "Unauthorized - invalid or missing token"},
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        }
    },
)
async def delete_admin_account(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    result = await remove_admin(admin_id=admin.id)  # type: ignore
    return result


@router.post("/tenants/{tenant_id}/offboard")
@document_response(
    message="Tenant offboarded successfully",
    status_code=status.HTTP_200_OK,
    description=(
        "Offboard (deactivate) a tenant. This action: "
        "cancels active subscriptions, deactivates all system users, "
        "marks the tenant as inactive, and records the action in audit trail."
    ),
    summary="Offboard tenant",
    success_example={
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "offboarded_at": 1712500000,
        "reason": "customer_request",
        "actions": {
            "subscription_cancelled": True,
            "users_deactivated_count": 5,
            "tenant_marked_inactive": True,
        },
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Tenant not found",
        500: "Internal server error",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "You do not have permission to perform this action",
            "code": "AUTH_PERMISSION_DENIED",
        },
        404: {
            "success": False,
            "message": "Tenant not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def offboard_tenant_endpoint(
    tenant_id: str,
    payload: OffboardingRequest = Body(default_factory=OffboardingRequest),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> OffboardingSummary:
    """Offboard a tenant and clean up associated resources."""
    result = await offboard_tenant(
        tenant_id=tenant_id,
        reason=payload.reason,
        admin_id=admin.id,  # type: ignore
    )
    return OffboardingSummary(**result)


@router.get("/tenants/{tenant_id}/offboarding-summary")
@document_response(
    message="Offboarding summary retrieved successfully",
    description="Get the current state of a tenant for offboarding assessment.",
    summary="Get tenant offboarding summary",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Tenant not found",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        404: {
            "success": False,
            "message": "Tenant not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def get_tenant_offboarding_summary_endpoint(
    tenant_id: str,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    """Get offboarding assessment summary for a tenant."""
    summary = await get_offboarding_summary(tenant_id=tenant_id)
    return summary


@router.post("/tenants/{tenant_id}/super-admins")
@document_response(
    message="Super admin added to tenant",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Add a super_admin system user to an existing tenant. Distinct from "
        "/tenants/bootstrap which creates the tenant and its first super_admin "
        "together. Use this to recover a tenant whose super_admin was offboarded, "
        "or to add a secondary super_admin for redundancy."
    ),
    summary="Add super admin to existing tenant",
    response_codes={
        400: "Tenant is inactive or super_admin add validation failed",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - only application admins can add super_admins",
        404: "Tenant not found",
        409: "Email already in use by another system user in this tenant",
    },
)
async def add_super_admin_to_tenant_endpoint(
    tenant_id: str,
    payload: AddSuperAdminRequest,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    """Application-admin only path to add a super_admin to a live tenant."""
    from services.system_user_service import add_super_admin_to_tenant

    return await add_super_admin_to_tenant(
        tenant_id=tenant_id,
        full_name=payload.full_name,
        email=payload.email,
        password=payload.password,
        branch_ids=payload.branch_ids,
    )


@router.post("/system-users/{user_id}/reset-password")
@document_response(
    message="Password reset successfully",
    description=(
        "Application-admin password reset for any system user (including "
        "super_admins of any tenant). All of the target user's tokens are "
        "revoked so they must log in again with the new password."
    ),
    summary="Admin reset of a system user's password",
    response_codes={
        400: "Validation error or actor cannot reset their own password here",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - only application admins",
        404: "Target system user not found",
        422: "Password does not meet policy or matches a recent password",
    },
)
async def admin_reset_system_user_password_endpoint(
    user_id: str,
    payload: ResetPasswordRequest,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    """Reset another system user's password without knowing the old one."""
    from services.password_change_service import (
        reset_system_user_password_by_authority,
    )

    await reset_system_user_password_by_authority(
        target_user_id=user_id,
        new_password=payload.new_password,
        actor_id=admin.id or "",  # type: ignore[arg-type]
        actor_role="admin",
        scope_tenant_id=None,
    )
    return {"id": user_id, "password_reset": True}
