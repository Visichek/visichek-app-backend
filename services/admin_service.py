
from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.admin_repo import (
    create_admin,
    get_admin,
    get_admins,
    update_admin,
    delete_admin,
)
from schemas.admin_schema import AdminCreate, AdminUpdate, AdminOut, AdminLogin, AdminRefresh, AdminSignupRequest
from security.hash import check_password
from repositories.tokens_repo import get_refresh_tokens, delete_access_token, delete_refresh_token, delete_all_tokens_with_admin_id
from services.auth_helpers import issue_tokens_for_user
from core.email_utils import normalize_email
from config.role_permissions import get_default_permissions_for_role


async def add_admin(signup_data: AdminSignupRequest, invited_by: str) -> AdminOut:
    """Create a new admin from an invite request.

    - Normalizes email to prevent +alias / dot duplicates
    - Auto-assigns default admin permissions
    - Sets account_status to ACTIVE
    """
    normalized = normalize_email(signup_data.email)

    # Check for existing admin using normalized email
    existing = await get_admin(filter_dict={"email": signup_data.email})
    if existing:
        raise HTTPException(status_code=409, detail="Admin already exists")

    # Also check by normalized email (catches alias tricks)
    all_admins = await get_admins(start=0, stop=10000)
    for admin in all_admins:
        if normalize_email(admin.email) == normalized:
            raise HTTPException(status_code=409, detail="An admin with this email already exists")

    # Build internal schema with system-assigned fields
    permission_list = get_default_permissions_for_role("admin")
    admin_data = AdminCreate(
        full_name=signup_data.full_name,
        email=signup_data.email,
        password=signup_data.password,
        accountStatus="ACTIVE",
        permissionList=permission_list,
        invited_by=invited_by,
    )

    new_admin = await create_admin(admin_data)
    access_token, refresh_token = await issue_tokens_for_user(user_id=new_admin.id, role="admin")  # type: ignore
    new_admin.password = ""
    new_admin.access_token = access_token
    new_admin.refresh_token = refresh_token
    return new_admin


async def authenticate_admin(admin_data: AdminLogin) -> AdminOut:
    from security.password_policy import (
        check_login_lockout,
        record_failed_login,
        clear_failed_logins,
    )

    # Check lockout before anything else
    lockout = await check_login_lockout(admin_data.email)
    if lockout:
        minutes = lockout["remaining_seconds"] // 60
        raise HTTPException(
            status_code=429,
            detail=f"Account temporarily locked due to too many failed login attempts. Try again in {minutes} minute(s).",
        )

    admin = await get_admin(filter_dict={"email": admin_data.email})

    if admin is not None:
        if check_password(password=admin_data.password, hashed=admin.password):  # type: ignore
            await clear_failed_logins(admin_data.email)
            admin.password = ""
            access_token, refresh_token = await issue_tokens_for_user(user_id=admin.id, role="admin")  # type: ignore
            admin.access_token = access_token
            admin.refresh_token = refresh_token
            return admin
        else:
            lockout_status = await record_failed_login(admin_data.email)
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
    else:
        raise HTTPException(status_code=401, detail="Invalid login credentials")


async def refresh_admin_tokens_reduce_number_of_logins(admin_refresh_data: AdminRefresh, expired_access_token):
    refreshObj = await get_refresh_tokens(admin_refresh_data.refresh_token)
    if refreshObj:
        if refreshObj.previousAccessToken == expired_access_token:
            admin = await get_admin(filter_dict={"_id": ObjectId(refreshObj.userId)})

            if admin is not None:
                access_token, refresh_token = await issue_tokens_for_user(user_id=admin.id, role="admin")  # type: ignore
                admin.access_token = access_token
                admin.refresh_token = refresh_token
                await delete_access_token(accessToken=expired_access_token)
                await delete_refresh_token(refreshToken=admin_refresh_data.refresh_token)
                return admin

    raise HTTPException(status_code=404, detail="Invalid refresh token")


async def remove_admin(admin_id: str):
    if not ObjectId.is_valid(admin_id):
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    filter_dict = {"_id": ObjectId(admin_id)}
    result = await delete_admin(filter_dict)
    await delete_all_tokens_with_admin_id(adminId=admin_id)

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Admin not found")


async def retrieve_admin_by_admin_id(id: str) -> AdminOut:
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    filter_dict = {"_id": ObjectId(id)}
    result = await get_admin(filter_dict)

    if not result:
        raise HTTPException(status_code=404, detail="Admin not found")

    return result


async def retrieve_admins(start=0, stop=100) -> List[AdminOut]:
    return await get_admins(start=start, stop=stop)


async def update_admin_by_id(
    admin_id: str,
    admin_data: AdminUpdate,
    is_password_getting_changed: bool = False,
) -> AdminOut:
    from core.queue.manager import QueueManager

    if not ObjectId.is_valid(admin_id):
        raise HTTPException(status_code=400, detail="Invalid admin ID format")

    filter_dict = {"_id": ObjectId(admin_id)}
    result = await update_admin(filter_dict, admin_data)

    if not result:
        raise HTTPException(status_code=404, detail="Admin not found or update failed")

    if is_password_getting_changed:
        if admin_data.password:
            from security.password_policy import record_password_in_history
            pwd_hash = admin_data.password
            if isinstance(pwd_hash, bytes):
                pwd_hash = pwd_hash.decode("utf-8")
            await record_password_in_history(admin_id, pwd_hash, role="admin")

        QueueManager.get_instance().enqueue("delete_tokens", {"userId": admin_id})

    return result
