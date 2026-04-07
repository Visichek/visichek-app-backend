from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import BaseModel

from core.response_envelope import document_response
from schemas.admin_schema import AdminLogin, AdminOut, AdminRefresh, AdminSignupRequest
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
from services.tenant_offboarding_service import offboard_tenant, get_offboarding_summary


class OffboardingRequest(BaseModel):
    """Request to offboard (deactivate) a tenant."""
    reason: str


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
    description="Retrieve the authenticated admin's profile information.",
    summary="Get admin profile",
    response_codes={401: "Unauthorized - invalid or missing token"},
    error_examples={401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"}},
)
async def get_my_admin(admin: AdminOut = Depends(check_admin_account_status_and_permissions)):
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
        409: {"success": False, "message": "Admin already exists", "code": "VALIDATION_FAILED"},
        422: {"success": False, "message": "Password does not meet strength requirements", "code": "VALIDATION_FAILED"},
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
        409: {"success": False, "message": "Tenant with this company name already exists", "code": "VALIDATION_FAILED"},
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
        401: {"success": False, "message": "Invalid login credentials", "code": "AUTH_INVALID_TOKEN"},
        429: {"success": False, "message": "Account temporarily locked", "code": "TOO_MANY_REQUESTS"},
    },
)
async def login_admin(admin_data: AdminLogin):
    items = await authenticate_admin(admin_data=admin_data)  # type: ignore
    return items


@router.post("/refresh")
@document_response(
    message="Admin tokens refreshed successfully",
    description="Refresh expired access tokens using a valid refresh token.",
    summary="Refresh admin tokens",
    response_codes={
        401: "Unauthorized - invalid or mismatched tokens",
    },
    error_examples={
        401: {"success": False, "message": "Invalid or expired refresh token", "code": "AUTH_INVALID_TOKEN"},
    },
)
async def refresh_admin_tokens(
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
    response_codes={401: "Unauthorized - invalid or missing token"},
    error_examples={401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"}},
)
async def delete_admin_account(admin: AdminOut = Depends(check_admin_account_status_and_permissions)):
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
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        403: {"success": False, "message": "You do not have permission to perform this action", "code": "AUTH_PERMISSION_DENIED"},
        404: {"success": False, "message": "Tenant not found", "code": "RESOURCE_NOT_FOUND"},
    },
)
async def offboard_tenant_endpoint(
    tenant_id: str,
    payload: OffboardingRequest,
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
        401: {"success": False, "message": "Invalid or expired token", "code": "AUTH_INVALID_TOKEN"},
        404: {"success": False, "message": "Tenant not found", "code": "RESOURCE_NOT_FOUND"},
    },
)
async def get_tenant_offboarding_summary_endpoint(
    tenant_id: str,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> dict:
    """Get offboarding assessment summary for a tenant."""
    summary = await get_offboarding_summary(tenant_id=tenant_id)
    return summary
