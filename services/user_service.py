
from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.user_repo import (
    create_user,
    get_user,
    get_users,
    update_user,
    delete_user,
)
from schemas.user_schema import UserCreate, UserUpdate, UserOut, UserBase, UserLogin, UserRefresh, UserSignupRequest
from security.hash import check_password
from repositories.tokens_repo import get_refresh_tokens, delete_access_token, delete_refresh_token, delete_all_tokens_with_user_id
from services.auth_helpers import issue_tokens_for_user
from core.email_utils import normalize_email
from config.role_permissions import get_default_permissions_for_role
from schemas.imports import AccountStatus
from authlib.integrations.starlette_client import OAuth  # type: ignore[import-untyped]
import os
from dotenv import load_dotenv


load_dotenv()


oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)


async def add_user_from_signup(signup_data: UserSignupRequest) -> UserOut:
    """Create a new user from a public-facing signup request.

    - Normalizes email to prevent +alias / dot duplicates
    - Auto-assigns default user permissions
    - Sets account_status to ACTIVE
    """
    normalized = normalize_email(signup_data.email)

    # Check by exact email
    existing = await get_user(filter_dict={"email": signup_data.email})
    if existing:
        raise HTTPException(status_code=409, detail="User already exists")

    # Also check by normalized email (catches alias tricks)
    all_users = await get_users(start=0, stop=50000)
    for u in all_users:
        if normalize_email(u.email) == normalized:
            raise HTTPException(status_code=409, detail="A user with this email already exists")

    # Auto-assign permissions based on role
    permission_list = get_default_permissions_for_role("user")

    # Build internal UserCreate with system-assigned fields
    user_data = UserCreate(
        firstName=signup_data.firstName,
        lastName=signup_data.lastName,
        email=signup_data.email,
        password=signup_data.password,
        loginType=signup_data.loginType,
        accountStatus=AccountStatus("ACTIVE"),
        permissionList=permission_list,
    )

    new_user = await create_user(user_data)
    access_token, refresh_token = await issue_tokens_for_user(user_id=new_user.id, role="user")  # type: ignore
    new_user.password = ""
    new_user.access_token = access_token
    new_user.refresh_token = refresh_token
    return new_user


async def add_user(user_data: UserCreate) -> UserOut:
    """Internal: adds an entry of UserCreate to the database.

    Used by Google OAuth flow and other internal callers that already
    constructed the full UserCreate object.
    """
    user = await get_user(filter_dict={"email": user_data.email})
    if user is None:
        new_user = await create_user(user_data)
        access_token, refresh_token = await issue_tokens_for_user(user_id=new_user.id, role="user")  # type: ignore
        new_user.password = ""
        new_user.access_token = access_token
        new_user.refresh_token = refresh_token
        return new_user
    else:
        raise HTTPException(status_code=409, detail="User Already exists")


async def authenticate_user(login_data: UserLogin) -> UserOut:
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

    user = await get_user(filter_dict={"email": login_data.email})

    if user is not None:
        if check_password(password=login_data.password, hashed=user.password):  # type: ignore
            await clear_failed_logins(login_data.email)
            user.password = ""
            access_token, refresh_token = await issue_tokens_for_user(user_id=user.id, role="user")  # type: ignore
            user.access_token = access_token
            user.refresh_token = refresh_token
            return user
        else:
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
    else:
        raise HTTPException(status_code=401, detail="Invalid login credentials")


async def refresh_user_tokens_reduce_number_of_logins(user_refresh_data: UserRefresh, expired_access_token):
    refreshObj = await get_refresh_tokens(user_refresh_data.refresh_token)
    if refreshObj:
        if refreshObj.previousAccessToken == expired_access_token:
            user = await get_user(filter_dict={"_id": ObjectId(refreshObj.userId)})

            if user is not None:
                access_token, refresh_token = await issue_tokens_for_user(user_id=user.id, role="user")  # type: ignore
                user.access_token = access_token
                user.refresh_token = refresh_token
                await delete_access_token(accessToken=expired_access_token)
                await delete_refresh_token(refreshToken=user_refresh_data.refresh_token)
                return user

        await delete_refresh_token(refreshToken=user_refresh_data.refresh_token)
        await delete_access_token(accessToken=expired_access_token)

    raise HTTPException(status_code=404, detail="Invalid refresh token")


async def remove_user(user_id: str):
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    filter_dict = {"_id": ObjectId(user_id)}
    result = await delete_user(filter_dict)
    await delete_all_tokens_with_user_id(userId=user_id)

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")


async def retrieve_user_by_user_id(id: str) -> UserOut:
    if not ObjectId.is_valid(id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    filter_dict = {"_id": ObjectId(id)}
    result = await get_user(filter_dict)

    if not result:
        raise HTTPException(status_code=404, detail="User not found")

    return result


async def retrieve_users(start=0, stop=100) -> List[UserOut]:
    return await get_users(start=start, stop=stop)


async def update_user_by_id(user_id: str, user_data: UserUpdate, is_password_getting_changed: bool = False) -> UserOut:
    from core.queue.manager import QueueManager
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    filter_dict = {"_id": ObjectId(user_id)}
    result = await update_user(filter_dict, user_data)

    if not result:
        raise HTTPException(status_code=404, detail="User not found or update failed")
    if is_password_getting_changed is True:
        QueueManager.get_instance().enqueue("delete_tokens", {"userId": user_id})
    return result


async def authenticate_user_google(user_data: UserBase) -> UserOut:
    user = await get_user(filter_dict={"email": user_data.email})

    if user is None:
        # Auto-assign permissions for new Google OAuth users
        permission_list = get_default_permissions_for_role("user")
        create_data = UserCreate(
            firstName=user_data.firstName,
            lastName=user_data.lastName,
            email=user_data.email,
            password=user_data.password,
            loginType=user_data.loginType,
            accountStatus=AccountStatus("ACTIVE"),
            permissionList=permission_list,
        )
        new_user = await create_user(create_data)
        user = new_user

    access_token, refresh_token = await issue_tokens_for_user(user_id=user.id, role="user")  # type: ignore
    user.password = ""
    user.access_token = access_token
    user.refresh_token = refresh_token
    return user
