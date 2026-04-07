from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.system_user_repo import (
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
        user_id=new_user.id,
        role=new_user.role.value,
        tenant_id=new_user.tenant_id,
    )
    new_user.access_token = access_token
    new_user.refresh_token = refresh_token
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
    access_token, refresh_token = await issue_tokens_for_role(
        user_id=user.id,
        role=user.role.value,
        tenant_id=user.tenant_id,
    )
    user.access_token = access_token
    user.refresh_token = refresh_token
    return user


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
        user_id=user.id,
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
    result = await delete_system_user({"_id": ObjectId(user_id), "tenant_id": tenant_id})
    await delete_all_tokens_with_user_id(userId=user_id)
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="System user not found")
