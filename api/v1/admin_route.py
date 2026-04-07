from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status

from core.response_envelope import document_response
from schemas.admin_schema import AdminBase, AdminCreate, AdminLogin, AdminOut, AdminRefresh
from schemas.tenant_schema import TenantBootstrapRequest
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_admin_refresh_token
from security.principal import AuthPrincipal
from services.admin_service import (
    add_admin,
    authenticate_admin,
    refresh_admin_tokens_reduce_number_of_logins,
    remove_admin,
    retrieve_admins,
)
from services.tenant_service import bootstrap_tenant

router = APIRouter(prefix="/admins", tags=["Admins"])


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
                        "description": "Retrieve user list"
                    }
                ]
            },
            "date_created": 1712500000,
            "last_updated": 1712500600
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
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission to perform this action", "code": "AUTH_PERMISSION_DENIED"},
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


@router.get("/profile")
@document_response(
    message="Admin profile fetched successfully",
    success_example={
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
                    "description": "Retrieve user list"
                }
            ]
        },
        "date_created": 1712500000,
        "last_updated": 1712500600
    },
    description="Retrieve the authenticated admin's profile information.",
    summary="Get admin profile",
    response_codes={
        401: "Unauthorized - invalid or missing token",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
    },
)
async def get_my_admin(admin: AdminOut = Depends(check_admin_account_status_and_permissions)):
    return admin


@router.post("/signup")
@document_response(
    message="Admin created successfully",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "full_name": "Jane Admin",
        "email": "jane.admin@example.com",
        "accountStatus": "ACTIVE",
        "permissionList": None,
        "date_created": 1712501200,
        "last_updated": 1712501200
    },
    status_code=status.HTTP_201_CREATED,
    description="Create a new admin account. Only existing admins can invite new admins.",
    summary="Create new admin",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        409: "Conflict - admin with this email already exists",
        422: "Validation error - invalid input data",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission to perform this action", "code": "AUTH_PERMISSION_DENIED"},
        409: {"success": False, "message": "Admin with this email already exists", "code": "VALIDATION_FAILED"},
        422: {"success": False, "message": "Email is required and must be valid", "code": "VALIDATION_FAILED"},
    },
)
async def signup_new_admin(
    admin_data: AdminBase,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    admin_data_dict = admin_data.model_dump()
    new_admin = AdminCreate(invited_by=admin.id, **admin_data_dict) # type: ignore
    items = await add_admin(admin_data=new_admin)
    return items


@router.post("/tenants/bootstrap")
@document_response(
    message="Tenant and super admin created successfully",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Bootstrap a new tenant along with its first super_admin system user. "
        "Only legacy admins can call this endpoint. Once a super_admin exists "
        "for a tenant, all further user management is handled by that super_admin."
    ),
    summary="Bootstrap tenant + first super admin",
    success_example={
        "tenant": {
            "id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "company_name": "Acme Corp",
            "lawful_basis": "legitimate_interest",
            "notice_display_mode": "passive",
            "retention_days": 1095,
            "default_retention_action": "anonymise",
            "dpo_contact_email": "dpo@acmecorp.com",
            "privacy_policy_url": "https://acmecorp.com/privacy",
            "country_of_hosting": "Nigeria",
            "cross_border_approved": False,
            "is_active": True,
            "date_created": 1712500000,
            "last_updated": 1712500000,
        },
        "super_admin": {
            "id": "64f1a2b3c4d5e6f7a8b9c0d2",
            "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "full_name": "Jane Doe",
            "email": "jane@acmecorp.com",
            "role": "super_admin",
            "account_status": "ACTIVE",
            "access_token": "eyJ...",
            "refresh_token": "eyJ...",
            "date_created": 1712500000,
            "last_updated": 1712500000,
        },
    },
    response_codes={
        401: "Unauthorized - invalid or missing admin token",
        403: "Forbidden - insufficient admin permissions",
        409: "Conflict - tenant company name already exists or tenant already has a super_admin",
        422: "Validation error - invalid input data",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission to perform this action", "code": "AUTH_PERMISSION_DENIED"},
        409: {"success": False, "message": "Tenant with this company name already exists", "code": "VALIDATION_FAILED"},
        422: {"success": False, "message": "admin_email must be a valid email address", "code": "VALIDATION_FAILED"},
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
    success_example={
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
                    "description": "Retrieve user list"
                }
            ]
        },
        "date_created": 1712500000,
        "last_updated": 1712500600,
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDEiLCJyb2xlIjoiYWRtaW4ifQ.abc123",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDEiLCJ0eXBlIjoicmVmcmVzaCJ9.xyz789"
    },
    description="Authenticate an admin with email and password. Returns access and refresh tokens.",
    summary="Admin login",
    response_codes={
        401: "Unauthorized - invalid credentials",
        422: "Validation error - missing or invalid email/password",
    },
    error_examples={
        401: {"success": False, "message": "Invalid email or password", "code": "AUTH_INVALID_TOKEN"},
        422: {"success": False, "message": "Email and password are required", "code": "VALIDATION_FAILED"},
    },
)
async def login_admin(admin_data: AdminLogin):
    items = await authenticate_admin(admin_data=admin_data) # type: ignore
    return items


@router.post(
    "/refresh",
)
@document_response(
    message="Admin tokens refreshed successfully",
    success_example={
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
                    "description": "Retrieve user list"
                }
            ]
        },
        "date_created": 1712500000,
        "last_updated": 1712500600,
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDEiLCJyb2xlIjoiYWRtaW4ifQ.abc123",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2NGYxYTJiM2M0ZDVlNmY3YThiOWMwZDEiLCJ0eXBlIjoicmVmcmVzaCJ9.xyz789"
    },
    description="Refresh expired access tokens using a valid refresh token. Expired access token must be provided in Authorization header.",
    summary="Refresh admin tokens",
    response_codes={
        401: "Unauthorized - invalid or mismatched tokens",
        422: "Validation error - missing refresh token",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired refresh token", "code": "AUTH_INVALID_TOKEN"},
        422: {"success": False, "message": "Refresh token is required", "code": "VALIDATION_FAILED"},
    },
)
async def refresh_admin_tokens(
    admin_data: Annotated[
        AdminRefresh,
        Body(
            openapi_examples={
                "successful_refresh": {
                    "summary": "Successful Token Refresh",
                    "description": (
                        "The correct payload for refreshing tokens. "
                        "The expired access token is provided in the Authorization header."
                    ),
                    "value": {"refresh_token": "valid.long.lived.refresh.token.98765"},
                },
                "invalid_refresh_token": {
                    "summary": "Invalid Refresh Token",
                    "description": (
                        "Payload that fails refresh because the refresh token is invalid or expired."
                    ),
                    "value": {"refresh_token": "expired.or.malformed.refresh.token.00000"},
                },
                "mismatched_tokens": {
                    "summary": "Tokens Belong to Different Admins",
                    "description": (
                        "Refresh token in the body does not match the admin ID from the expired access token."
                    ),
                    "value": {"refresh_token": "refresh.token.of.different.admin.77777"},
                },
            }
        ),
    ],
    principal: AuthPrincipal = Depends(verify_admin_refresh_token),
):
    items = await refresh_admin_tokens_reduce_number_of_logins(
        admin_refresh_data=admin_data,
        expired_access_token=principal.access_token_id,
    )

    items.password = ""
    return items


@router.delete("/account")
@document_response(
    message="Admin account deleted successfully",
    success_example={"deleted": True},
    description="Delete the authenticated admin's account. This action is irreversible.",
    summary="Delete admin account",
    response_codes={
        401: "Unauthorized - invalid or missing token",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
    },
)
async def delete_admin_account(admin: AdminOut = Depends(check_admin_account_status_and_permissions)):
    result = await remove_admin(admin_id=admin.id) # type: ignore
    return result
