from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Request, status
from fastapi.encoders import jsonable_encoder as _fastapi_jsonable_encoder
from fastapi.responses import JSONResponse

from core.bulk import enqueue_bulk_write
from core.database import db
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
from core.response_envelope import document_response, success_payload
from schemas.system_user_schema import (
    SystemUserSignupRequest,
    SystemUserTenantLogin,
    SystemUserUpdate,
    SystemUserLogin,
    SystemUserRefresh,
    SystemUserProfileOut,
    TenantProfileSummary,
    TenantSelectionRequest,
)
from security.auth import (
    verify_any_system_user_token,
    verify_super_admin_token,
    verify_system_user_refresh_token,
)
from schemas.otp_schema import MfaSettingsUpdate, OtpVerifyRequest
from schemas.session_schema import ResetPasswordRequest
from security.cookie_utils import (
    build_auth_response,
    clear_auth_cookies,
    REFRESH_TOKEN_COOKIE,
)
from security.principal import AuthPrincipal
from services.system_user_service import (
    add_system_user_from_invite,
    authenticate_system_user,
    authenticate_super_admin_global,
    authenticate_system_user_by_tenant,
    complete_login_after_tenant_selection,
    refresh_system_user_tokens,
    retrieve_system_user_by_id,
    retrieve_system_users,
    toggle_user_mfa,
    verify_system_user_otp,
)
from services.tenant_service import retrieve_tenant_by_id


def jsonable_encoder(obj, **kwargs):
    """Local wrapper that defaults by_alias=False so response bodies use
    field names (e.g. ``id``) instead of MongoDB aliases (e.g. ``_id``)."""
    kwargs.setdefault("by_alias", False)
    return _fastapi_jsonable_encoder(obj, **kwargs)


def _attr_or_key(obj, key):
    """Return obj[key] for dicts or obj.key for objects. Returns None if missing."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


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
    description=(
        "Authenticate a system user with email and password.\n\n"
        "The response can take three shapes — branch on the discriminator key:\n"
        "1. **Login complete** — standard payload with `access_token` + `refresh_token`.\n"
        "2. **2FA required** — `{ otp_required: true, otp_challenge_id }`. Continue at `POST /v1/system-users/verify-otp`.\n"
        "3. **Tenant selection required** — `{ tenant_selection_required: true, selection_token, tenants: [...] }` "
        "when the email matches more than one tenant. Continue at `POST /v1/system-users/select-tenant` with the user's choice."
    ),
    summary="System user login (global)",
    response_codes={
        401: "Unauthorized - invalid credentials",
        403: "Forbidden - account not active",
        429: "Too many failed attempts - account temporarily locked",
        422: "Validation error - missing or invalid email/password",
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
async def login_system_user(request: Request, login_data: SystemUserLogin):
    result = await authenticate_system_user(login_data=login_data)
    request_id = getattr(request.state, "request_id", None)

    if isinstance(result, dict) and result.get("otp_required"):
        return JSONResponse(
            content=jsonable_encoder(
                success_payload(
                    result, message="OTP verification required", request_id=request_id
                )
            ),
        )

    if isinstance(result, dict) and result.get("tenant_selection_required"):
        return JSONResponse(
            content=jsonable_encoder(
                success_payload(
                    result,
                    message="Tenant selection required",
                    request_id=request_id,
                )
            ),
        )

    user = result
    return build_auth_response(
        request=request,
        payload=user,
        message="Login successful",
        access_token=_attr_or_key(user, "access_token") or "",
        refresh_token=_attr_or_key(user, "refresh_token") or "",
    )


@router.post("/select-tenant")
@document_response(
    message="Login successful",
    description=(
        "Stage 2 of multi-tenant login. Submit the `selection_token` returned "
        "by `POST /v1/system-users/login` along with the chosen `tenant_id`. "
        "The token is single-use and expires after 5 minutes.\n\n"
        "On success the response is either:\n"
        "* the standard login payload with access/refresh tokens, OR\n"
        "* `{ otp_required: true, otp_challenge_id }` if the chosen tenant "
        "user has 2FA enabled — continue at `POST /v1/system-users/verify-otp`."
    ),
    summary="Select tenant after login",
    response_codes={
        401: "Unauthorized - invalid, used, or expired selection token",
        403: "Forbidden - selected tenant not associated with this login attempt or account not active",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired tenant selection token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Selected tenant is not available for this login attempt",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def select_tenant_after_login(
    request: Request, payload: TenantSelectionRequest
):
    result = await complete_login_after_tenant_selection(
        selection_token=payload.selection_token,
        tenant_id=payload.tenant_id,
    )
    request_id = getattr(request.state, "request_id", None)

    if isinstance(result, dict) and result.get("otp_required"):
        return JSONResponse(
            content=jsonable_encoder(
                success_payload(
                    result, message="OTP verification required", request_id=request_id
                )
            ),
        )

    user = result
    return build_auth_response(
        request=request,
        payload=user,
        message="Login successful",
        access_token=_attr_or_key(user, "access_token") or "",
        refresh_token=_attr_or_key(user, "refresh_token") or "",
    )


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
        "Dedicated login for tenant super admins; no tenant_id is needed in the URL. "
        "Returns the user profile plus tenant context info including the tenant-scoped "
        "login URL.\n\n"
        "Response can take three shapes — branch on the discriminator key:\n"
        "1. **Login complete** — `{ user, tenant, tenant_login_url }` with tokens on `user`.\n"
        "2. **2FA required** — `{ otp_required: true, otp_challenge_id }`. Continue at "
        "`POST /v1/system-users/verify-otp`.\n"
        "3. **Tenant selection required** — `{ tenant_selection_required: true, "
        "selection_token, tenants: [...] }` when the same email exists as a super_admin "
        "in more than one tenant. Continue at `POST /v1/system-users/select-tenant`.\n\n"
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
        401: {
            "success": False,
            "message": "Invalid login credentials",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "This login endpoint is reserved for tenant super admins",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def login_super_admin_global(request: Request, login_data: SystemUserLogin):
    result = await authenticate_super_admin_global(login_data=login_data)
    request_id = getattr(request.state, "request_id", None)

    # authenticate_super_admin_global calls authenticate_system_user internally
    # which may return an otp_required dict
    if isinstance(result, dict) and result.get("otp_required"):
        return JSONResponse(
            content=jsonable_encoder(
                success_payload(
                    result, message="OTP verification required", request_id=request_id
                )
            ),
        )

    # Same email exists in more than one tenant — forward the selection
    # challenge; the FE finishes at POST /v1/system-users/select-tenant.
    if isinstance(result, dict) and result.get("tenant_selection_required"):
        return JSONResponse(
            content=jsonable_encoder(
                success_payload(
                    result,
                    message="Tenant selection required",
                    request_id=request_id,
                )
            ),
        )

    user = result["user"]
    return build_auth_response(
        request=request,
        payload=result,
        message="Super admin login successful",
        access_token=_attr_or_key(user, "access_token") or "",
        refresh_token=_attr_or_key(user, "refresh_token") or "",
    )


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
        401: {
            "success": False,
            "message": "Invalid login credentials",
            "code": "AUTH_INVALID_TOKEN",
        },
        404: {
            "success": False,
            "message": "Tenant not found",
            "code": "RESOURCE_NOT_FOUND",
        },
        429: {
            "success": False,
            "message": "Account temporarily locked",
            "code": "TOO_MANY_REQUESTS",
        },
    },
)
async def login_system_user_by_tenant(
    request: Request, tenant_id: str, login_data: SystemUserTenantLogin
):
    result = await authenticate_system_user_by_tenant(
        login_data=login_data, tenant_id=tenant_id
    )
    request_id = getattr(request.state, "request_id", None)

    if isinstance(result, dict) and result.get("otp_required"):
        return JSONResponse(
            content=jsonable_encoder(
                success_payload(
                    result, message="OTP verification required", request_id=request_id
                )
            ),
        )

    user = result
    return build_auth_response(
        request=request,
        payload=user,
        message="Login successful",
        access_token=_attr_or_key(user, "access_token") or "",
        refresh_token=_attr_or_key(user, "refresh_token") or "",
    )


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
        409: {
            "success": False,
            "message": "A user with this email already exists in this tenant",
            "code": "VALIDATION_FAILED",
        },
        422: {
            "success": False,
            "message": "Password does not meet strength requirements",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def signup_system_user(
    signup_data: SystemUserSignupRequest,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await add_system_user_from_invite(
        signup_data=signup_data, tenant_id=tenant_id
    )


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
        401: {
            "success": False,
            "message": "Invalid or expired refresh token",
            "code": "AUTH_INVALID_TOKEN",
        },
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

    return build_auth_response(
        request=request,
        payload=user,
        message="Tokens refreshed successfully",
        access_token=_attr_or_key(user, "access_token") or "",
        refresh_token=_attr_or_key(user, "refresh_token") or "",
    )


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

    return build_auth_response(
        request=request,
        payload=user,
        message="OTP verified, login successful",
        access_token=_attr_or_key(user, "access_token") or "",
        refresh_token=_attr_or_key(user, "refresh_token") or "",
    )


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
    request_id = getattr(request.state, "request_id", None)
    response = JSONResponse(
        content=jsonable_encoder(
            success_payload(
                None, message="Logged out successfully", request_id=request_id
            )
        ),
    )
    clear_auth_cookies(response)
    return response




@router.post("/bulk/reset-password")
@document_response(
    message="Bulk password reset queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Force a password reset across multiple users. Each target's "
        "tokens are revoked. The new password is generated server-side "
        "and emailed to the user â€” the response never contains the "
        "plaintext password."
    ),
    summary="Bulk force password reset",
)
async def bulk_reset_system_users_password(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/system-users/bulk/reset-password",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="system_user.bulk_reset_password",
        ids=payload.get("ids", []),
        resource_type="system_user",
        extras=_su_bulk_extras(principal),
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/system-users/bulk/reset-password",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/{user_id}/reset-password")
@document_response(
    message="Password reset successfully",
    description=(
        "Super_admin-driven password reset for any system user inside the same "
        "tenant. The actor's tenant is taken from their token; the target must "
        "belong to that tenant or a 404 is returned. The target's tokens are "
        "all revoked so they're forced to log in again. Super admins cannot "
        "reset their own password through this endpoint — use "
        "/v1/auth/change-password instead."
    ),
    summary="Super admin reset of another tenant user's password",
    response_codes={
        400: "Validation error or actor cannot reset their own password here",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - only super_admins",
        404: "Target system user not found in this tenant",
        422: "Password does not meet policy or matches a recent password",
    },
)
async def super_admin_reset_user_password_endpoint(
    user_id: str,
    payload: ResetPasswordRequest,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    """Super_admin can reset any tenant user's password (including other
    super_admins inside the same tenant)."""
    from services.password_change_service import (
        reset_system_user_password_by_authority,
    )

    await reset_system_user_password_by_authority(
        target_user_id=user_id,
        new_password=payload.new_password,
        actor_id=principal.user_id,
        actor_role="super_admin",
        scope_tenant_id=principal.tenant_id or "",
    )
    return {"id": user_id, "password_reset": True}


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
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
    },
)
async def get_my_profile(
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> SystemUserProfileOut:
    user = await retrieve_system_user_by_id(user_id=principal.user_id)
    tenant_summary = None
    if principal.tenant_id:
        try:
            tenant = await retrieve_tenant_by_id(principal.tenant_id)
            tenant_summary = TenantProfileSummary(
                id=tenant.id,
                company_name=tenant.company_name,
                lawful_basis=tenant.lawful_basis,
                notice_display_mode=tenant.notice_display_mode,
                dpo_contact_email=tenant.dpo_contact_email,
                privacy_policy_url=tenant.privacy_policy_url,
                country_of_hosting=tenant.country_of_hosting,
                cross_border_approved=tenant.cross_border_approved,
                is_active=tenant.is_active,
                enable_repeat_visitor_recognition=tenant.enable_repeat_visitor_recognition,
                mfa_default_for_users=tenant.mfa_default_for_users,
                mfa_user_override_allowed=tenant.mfa_user_override_allowed,
            )
        except Exception:
            pass
    return SystemUserProfileOut(**user.model_dump(), tenant=tenant_summary)


@router.get("")
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
async def list_system_users(
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    from core.queue.precompute import PrecomputeScope, get_or_compute

    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return {"items": [], "meta": {"total": 0, "skip": 0, "limit": 25, "hasMore": False}}

    spec = SYSTEM_USERS_LIST_SPEC
    if _is_default_su_listing(request):
        async def _load() -> list:
            users = await retrieve_system_users(tenant_id=tenant_id, start=0, stop=100)
            return [
                u.model_dump(mode="json", by_alias=True)
                if hasattr(u, "model_dump")
                else u
                for u in users
            ]

        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="system_users.list",
            ttl=60,
            loader=_load,
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: spec.default_limit]
        return {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": spec.default_limit,
                "hasMore": len(items) > spec.default_limit,
            },
        }
    query = parse_list_query(request, spec)
    return await run_list(
        collection=db.system_users,
        query=query,
        base_filter={"tenant_id": tenant_id},
        map_doc=_map_su_doc,
        facet_runner=_su_facet,
    )


_SU_ROLES = frozenset(
    {"super_admin", "dept_admin", "receptionist", "auditor", "security_officer", "dpo"}
)
_SU_STATUSES = frozenset({"ACTIVE", "INACTIVE", "SUSPENDED"})


def _su_status_builder(values):
    if "all" in values:
        return {}
    if len(values) == 1:
        return {"account_status": values[0]}
    return {"account_status": {"$in": list(values)}}


SYSTEM_USERS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"full_name", "email", "role", "date_created", "last_login_at"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("full_name", "email"),
    filters={
        "role": FilterDef(name="role", multi=True, allowed_values=_SU_ROLES),
        "branchId": FilterDef(name="branchId", mongo_field="branch_ids"),
        "departmentId": FilterDef(name="departmentId", mongo_field="department_id"),
        "accountStatus": FilterDef(
            name="accountStatus",
            multi=True,
            allowed_values=frozenset({"ACTIVE", "INACTIVE", "SUSPENDED", "all"}),
            builder=_su_status_builder,
        ),
    },
    facet_fields=frozenset({"role", "accountStatus"}),
)


def _is_default_su_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(SYSTEM_USERS_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(SYSTEM_USERS_LIST_SPEC.default_limit)


def _map_su_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    # Strip password_hash defensively even though it's not normally returned.
    doc.pop("password_hash", None)
    return doc


async def _su_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field == "accountStatus":
        base = {k: v for k, v in filter_doc.items() if k != "account_status"}
        out: dict[str, int] = {}
        for v in _SU_STATUSES:
            out[v] = await collection.count_documents({**base, "account_status": v})
        out["all"] = sum(out.values())
        return out
    if field == "role":
        base = {k: v for k, v in filter_doc.items() if k != "role"}
        out = {}
        for v in _SU_ROLES:
            out[v] = await collection.count_documents({**base, "role": v})
        return out
    return {}


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
            "message": "System user not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def update_system_user_endpoint(
    user_id: str,
    user_data: SystemUserUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    from core.queue.write_pipeline import enqueue_write

    tenant_id = principal.tenant_id or ""
    payload = user_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    return await enqueue_write(
        writer_key="system_user.update",
        payload=payload,
        resource_type="system_user",
        resource_id=user_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
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
            "message": "System user not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def delete_system_user_endpoint(
    user_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    from core.queue.write_pipeline import enqueue_write

    tenant_id = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="system_user.delete",
        payload={"tenant_id": tenant_id},
        resource_type="system_user",
        resource_id=user_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


# ─── Bulk endpoints ───────────────────────────────────────────────────


def _su_bulk_extras(principal: AuthPrincipal) -> dict[str, Any]:
    """Wrap actor + tenant into the bulk writer's extras envelope.

    The writer must enforce same-tenant scoping per id (defence in depth)
    so we always pass `actor_user_id` (block self-mutation in delete) and
    `tenant_scope` (block cross-tenant attempts).
    """
    return {
        "actor_user_id": principal.user_id,
        "tenant_scope": principal.tenant_id or "",
    }


@router.post("/bulk/delete")
@document_response(
    message="Bulk system-user delete queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk delete system users",
)
async def bulk_delete_system_users(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/system-users/bulk/delete",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="system_user.bulk_delete",
        ids=payload.get("ids", []),
        resource_type="system_user",
        extras=_su_bulk_extras(principal),
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/system-users/bulk/delete",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/deactivate")
@document_response(
    message="Bulk system-user deactivate queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk deactivate system users",
)
async def bulk_deactivate_system_users(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/system-users/bulk/deactivate",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="system_user.bulk_deactivate",
        ids=payload.get("ids", []),
        resource_type="system_user",
        extras=_su_bulk_extras(principal),
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/system-users/bulk/deactivate",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response
