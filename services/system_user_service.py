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
)
from security.hash import check_password
from services.auth_helpers import issue_tokens_for_role


async def add_system_user(user_data: SystemUserCreate) -> SystemUserOut:
    existing = await get_system_user({
        "tenant_id": user_data.tenant_id,
        "email": user_data.email,
    })
    if existing:
        raise HTTPException(status_code=409, detail="System user with this email already exists in tenant")
    new_user = await create_system_user(user_data)
    access_token, refresh_token = await issue_tokens_for_role(
        user_id=new_user.id,
        role=new_user.role.value,
        tenant_id=new_user.tenant_id,
    )
    new_user.access_token = access_token
    new_user.refresh_token = refresh_token
    return new_user


async def authenticate_system_user(login_data: SystemUserLogin) -> SystemUserOut:
    user = await get_system_user({"email": login_data.email})
    if not user:
        raise HTTPException(status_code=404, detail="System user not found")

    # Retrieve the raw document to get the hashed password
    from core.database import db
    raw = await db.system_users.find_one({"email": login_data.email})
    if not raw or not check_password(password=login_data.password, hashed=raw["password"]):
        raise HTTPException(status_code=401, detail="Invalid login credentials")

    if user.account_status.value != "ACTIVE":
        raise HTTPException(status_code=403, detail="Account is not active")

    access_token, refresh_token = await issue_tokens_for_role(
        user_id=user.id,
        role=user.role.value,
        tenant_id=user.tenant_id,
    )
    user.access_token = access_token
    user.refresh_token = refresh_token
    return user


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
