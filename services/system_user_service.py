from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.system_user_repo import (
    count_system_users,
    create_system_user,
    get_system_user,
    get_system_users,
    update_system_user,
    delete_system_user,
)
from repositories.tokens_repo import (
    get_refresh_tokens,
    delete_access_token,
    delete_refresh_token,
    delete_all_tokens_with_user_id,
)
from schemas.system_user_schema import (
    SystemUserCreate,
    SystemUserUpdate,
    SystemUserOut,
    SystemUserLogin,
    SystemUserRefresh,
    SystemUserSignupRequest,
    SystemUserTenantLogin,
)
from schemas.imports import AccountStatus, SystemUserRole
from security.hash import check_password
from services.auth_helpers import issue_tokens_for_role
from core.email_utils import normalize_email
from config.role_permissions import get_default_permissions_for_role
from services.audit_service import record_audit_event
from services.plan_limits import enforce_entity_cap


async def _check_email_uniqueness(email: str, role: str, tenant_id: str) -> None:
    """Enforce email uniqueness rules:

    - super_admin: email must be globally unique across ALL system_users
    - other roles: email must be unique within the tenant
    """
    normalized = normalize_email(email)

    if role == SystemUserRole.SUPER_ADMIN.value or role == "super_admin":
        # Global uniqueness for super_admin
        all_users = await get_system_users(filter_dict={}, start=0, stop=50000)
        for user in all_users:
            if normalize_email(user.email) == normalized:
                raise HTTPException(
                    status_code=409,
                    detail="A user with this email already exists in the system",
                )
    else:
        # Per-tenant uniqueness for other roles
        tenant_users = await get_system_users(
            filter_dict={"tenant_id": tenant_id}, start=0, stop=50000
        )
        for user in tenant_users:
            if normalize_email(user.email) == normalized:
                raise HTTPException(
                    status_code=409,
                    detail="A user with this email already exists in this tenant",
                )


async def add_system_user(user_data: SystemUserCreate) -> SystemUserOut:
    """Create a system user with auto-assigned permissions based on role."""

    # Enforce plan cap on total system users for this tenant
    current_count = await count_system_users({"tenant_id": user_data.tenant_id})
    await enforce_entity_cap(
        tenant_id=user_data.tenant_id,
        cap_key="max_system_users",
        current_count=current_count,
        friendly_name="System user",
    )

    # Enforce email uniqueness with normalization
    await _check_email_uniqueness(
        email=user_data.email,
        role=user_data.role.value if hasattr(user_data.role, 'value') else user_data.role,
        tenant_id=user_data.tenant_id,
    )

    # Auto-assign permissions based on role
    role_str = user_data.role.value if hasattr(user_data.role, 'value') else user_data.role
    user_data.permissionList = get_default_permissions_for_role(role_str)

    new_user = await create_system_user(user_data)
    access_token, refresh_token = await issue_tokens_for_role(
        user_id=new_user.id or "",
        role=new_user.role.value,
        tenant_id=new_user.tenant_id,
    )
    new_user.access_token = access_token
    new_user.refresh_token = refresh_token

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="system_user.created",
            resource_type="system_user",
            resource_id=str(new_user.id),
            tenant_id=new_user.tenant_id,
            details={
                "email": new_user.email,
                "role": new_user.role.value if hasattr(new_user.role, 'value') else new_user.role,
                "department_id": new_user.department_id,
                "full_name": new_user.full_name,
            },
        )
    except Exception:
        pass

    return new_user


async def add_system_user_from_invite(
    signup_data: SystemUserSignupRequest, tenant_id: str
) -> SystemUserOut:
    """Create a system user from a public-facing invite request.

    - Auto-assigns account_status=ACTIVE
    - Auto-assigns permissions based on role
    - Normalizes and checks email uniqueness
    """
    role_str = signup_data.role.value if hasattr(signup_data.role, 'value') else signup_data.role

    # Build internal SystemUserCreate with system-assigned fields
    create_data = SystemUserCreate(
        tenant_id=tenant_id,
        department_id=signup_data.department_id,
        full_name=signup_data.full_name,
        email=signup_data.email,
        role=signup_data.role,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
        password_hash=signup_data.password,
    )
    return await add_system_user(create_data)


async def authenticate_system_user(login_data: SystemUserLogin, tenant_id: str | None = None) -> SystemUserOut:
    """Authenticate a system user. If tenant_id is provided, scope the lookup to that tenant."""
    from security.password_policy import (
        check_login_lockout,
        record_failed_login,
        clear_failed_logins,
    )

    # Check lockout before anything else
    lockout = await check_login_lockout(login_data.email)
    if lockout:
        minutes = lockout["remaining_seconds"] // 60
        raise HTTPException(
            status_code=429,
            detail=f"Account temporarily locked due to too many failed login attempts. Try again in {minutes} minute(s).",
        )

    # Build filter — optionally scoped to tenant
    filter_dict: dict = {"email": login_data.email}
    if tenant_id:
        filter_dict["tenant_id"] = tenant_id

    user = await get_system_user(filter_dict)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid login credentials")

    # Retrieve the raw document to get the hashed password
    from core.database import db
    raw_filter: dict = {"email": login_data.email}
    if tenant_id:
        raw_filter["tenant_id"] = tenant_id
    raw = await db.system_users.find_one(raw_filter)
    if not raw or not check_password(password=login_data.password, hashed=raw["password_hash"]):
        lockout_status = await record_failed_login(login_data.email)
        if lockout_status.get("locked"):
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Account is temporarily locked for 15 minutes.",
            )
        remaining = lockout_status.get("attempts_remaining", "?")
        raise HTTPException(
            status_code=401,
            detail=f"Invalid login credentials. {remaining} attempt(s) remaining before lockout.",
        )

    if user.account_status.value != "ACTIVE":
        raise HTTPException(status_code=403, detail="Account is not active")

    await clear_failed_logins(login_data.email)

    # 2FA check
    from services.otp_service import is_mfa_required, create_otp_challenge
    if await is_mfa_required("system_user", user.id):  # type: ignore
        challenge_id, _code = await create_otp_challenge(
            user_id=user.id, user_type="system_user",  # type: ignore
            role=user.role.value, tenant_id=user.tenant_id,
        )
        return {"otp_required": True, "otp_challenge_id": challenge_id}  # type: ignore[return-value]

    access_token, refresh_token = await issue_tokens_for_role(
        user_id=user.id or "",
        role=user.role.value,
        tenant_id=user.tenant_id,
    )
    user.access_token = access_token
    user.refresh_token = refresh_token
    return user


async def authenticate_super_admin_global(login_data: SystemUserLogin) -> dict:
    """Authenticate a super_admin via global login (unique email).

    Returns the standard SystemUserOut plus tenant context info that the
    frontend needs to display the super_admin dashboard:
    - tenant info (company_name, tenant_id)
    - the tenant-scoped login URL path for the tenant management portal

    This is the "administrative" login — the super_admin uses it to view
    tenant metadata, billing, and the tenant login URL.  The tenant-scoped
    login at /system-users/tenant/{tenant_id}/login is used for managing
    the tenant itself (visitors, departments, branding, etc.).
    """
    # Authenticate without tenant scoping — super_admin email is globally unique
    user = await authenticate_system_user(login_data=login_data)

    # If 2FA is required, bubble the OTP challenge up to the route
    if isinstance(user, dict) and user.get("otp_required"):
        return user

    # Only super_admins get this enriched response
    role_str = user.role.value if hasattr(user.role, 'value') else user.role
    if role_str != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="This login endpoint is reserved for tenant super admins",
        )

    # Fetch tenant context
    tenant_info = None
    tenant_login_url = None
    if user.tenant_id:
        try:
            from services.tenant_service import retrieve_tenant_by_id
            tenant = await retrieve_tenant_by_id(user.tenant_id)
            tenant_info = {
                "tenant_id": tenant.id,
                "company_name": tenant.company_name,
            }
            tenant_login_url = f"/v1/system-users/tenant/{user.tenant_id}/login"
        except Exception:
            pass

    return {
        "user": user,
        "tenant": tenant_info,
        "tenant_login_url": tenant_login_url,
    }


async def authenticate_system_user_by_tenant(
    login_data: SystemUserTenantLogin, tenant_id: str
) -> SystemUserOut:
    """Authenticate a system user scoped to a specific tenant (via URL path)."""
    # Validate that the tenant exists
    from services.tenant_service import retrieve_tenant_by_id
    await retrieve_tenant_by_id(tenant_id)

    # Reuse the main auth function with tenant scoping
    login = SystemUserLogin(email=login_data.email, password=login_data.password)
    return await authenticate_system_user(login_data=login, tenant_id=tenant_id)


async def refresh_system_user_tokens(refresh_data: SystemUserRefresh, expired_access_token: str):
    refresh_obj = await get_refresh_tokens(refresh_data.refresh_token)
    if not refresh_obj:
        raise HTTPException(status_code=404, detail="Invalid refresh token")

    if refresh_obj.previousAccessToken != expired_access_token:
        await delete_refresh_token(refreshToken=refresh_data.refresh_token)
        await delete_access_token(accessToken=expired_access_token)
        raise HTTPException(status_code=404, detail="Invalid refresh token")

    user = await get_system_user({"_id": ObjectId(refresh_obj.userId)})
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    access_token, refresh_token = await issue_tokens_for_role(
        user_id=user.id or "",
        role=user.role.value,
        tenant_id=user.tenant_id,
    )
    user.access_token = access_token
    user.refresh_token = refresh_token
    await delete_access_token(accessToken=expired_access_token)
    await delete_refresh_token(refreshToken=refresh_data.refresh_token)
    return user


async def retrieve_system_user_by_id(user_id: str) -> SystemUserOut:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")
    result = await get_system_user({"_id": ObjectId(user_id)})
    if not result:
        raise HTTPException(status_code=404, detail="System user not found")
    return result


async def retrieve_system_users(tenant_id: str, start=0, stop=100) -> List[SystemUserOut]:
    return await get_system_users(filter_dict={"tenant_id": tenant_id}, start=start, stop=stop)


async def update_system_user_by_id(
    user_id: str, tenant_id: str, user_data: SystemUserUpdate
) -> SystemUserOut:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")
    result = await update_system_user(
        {"_id": ObjectId(user_id), "tenant_id": tenant_id}, user_data
    )
    if not result:
        raise HTTPException(status_code=404, detail="System user not found or update failed")
    return result


async def remove_system_user(user_id: str, tenant_id: str):
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    # Fetch user before deletion for audit logging
    user = await get_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    result = await delete_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    await delete_all_tokens_with_user_id(userId=user_id)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="System user not found")

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="system_user.deleted",
            resource_type="system_user",
            resource_id=user_id,
            tenant_id=tenant_id,
            details={
                "email": user.email,
                "role": user.role.value if hasattr(user.role, 'value') else user.role,
                "full_name": user.full_name,
            },
        )
    except Exception:
        pass


async def verify_system_user_otp(challenge_id: str, otp_code: str):
    """Step 2 of system user 2FA login — verify OTP and issue tokens."""
    from services.otp_service import verify_otp_challenge

    result = await verify_otp_challenge(challenge_id, otp_code)
    user = await get_system_user({"_id": ObjectId(result["user_id"])})
    if not user:
        raise HTTPException(status_code=401, detail="System user not found")

    access_token, refresh_token = await issue_tokens_for_role(
        user_id=user.id or "",
        role=user.role.value,
        tenant_id=user.tenant_id,
    )
    user.access_token = access_token
    user.refresh_token = refresh_token
    return user


async def toggle_user_mfa(
    user_id: str,
    tenant_id: str,
    mfa_enabled: bool,
) -> SystemUserOut:
    """Allow a tenant user to toggle their own MFA (subject to locks)."""
    user = await get_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    if getattr(user, "mfa_locked_by_admin", False):
        raise HTTPException(status_code=403, detail="MFA setting is locked by your administrator")

    from services.tenant_service import retrieve_tenant_by_id
    tenant = await retrieve_tenant_by_id(tenant_id)
    if not getattr(tenant, "mfa_user_override_allowed", True):
        raise HTTPException(status_code=403, detail="MFA settings are managed by your administrator")

    update_data = SystemUserUpdate(mfa_enabled=mfa_enabled)
    updated = await update_system_user(
        {"_id": ObjectId(user_id), "tenant_id": tenant_id},
        update_data,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update MFA setting")
    return updated


async def admin_set_user_mfa(
    user_id: str,
    tenant_id: str,
    mfa_enabled: bool,
    mfa_locked_by_admin: bool = False,
) -> SystemUserOut:
    """Super admin sets MFA + lock for a tenant user."""
    user = await get_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    update_data = SystemUserUpdate(
        mfa_enabled=mfa_enabled,
        mfa_locked_by_admin=mfa_locked_by_admin,
    )
    updated = await update_system_user(
        {"_id": ObjectId(user_id), "tenant_id": tenant_id},
        update_data,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update MFA setting")
    return updated
