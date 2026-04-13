from __future__ import annotations

from fastapi import HTTPException, status

from repositories import tokens_repo as token_repo
from schemas.tokens_schema import accessTokenCreate, refreshTokenCreate
from security.encrypting_jwt import create_jwt_role_token


APP_ROLE_ALIASES = {"member": "user"}


def _normalize_role(role: str) -> str:
    return APP_ROLE_ALIASES.get(role.strip().lower(), role.strip().lower())


TENANT_USER_ROLES = (
    "receptionist", "dept_admin", "super_admin",
    "auditor", "security_officer", "dpo",
)


async def _issue_access_token(user_id: str, role: str):
    role = _normalize_role(role)

    # VisiChek system user roles use the generic system user token function
    if role in TENANT_USER_ROLES:
        return await token_repo.add_system_user_access_tokens(
            token_data=accessTokenCreate(userId=user_id),
            role=role,
        )

    role_function = f"add_{role}_access_token"
    add_token = getattr(token_repo, role_function, None)

    if add_token is None:
        if role == "user":
            add_token = getattr(token_repo, "add_access_tokens", None)
        elif role == "admin":
            add_token = getattr(token_repo, "add_admin_access_tokens", None)

    if add_token is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Token repository role function not found",
                "role": role,
                "expected_function": role_function,
            },
        )

    return await add_token(token_data=accessTokenCreate(userId=user_id))


async def issue_tokens_for_role(user_id: str, role: str, tenant_id: str | None = None) -> tuple[str, str]:
    normalized_role = _normalize_role(role)
    access_token = await _issue_access_token(user_id=user_id, role=normalized_role)

    # Store tenant_id on the access token record for system users
    if tenant_id and access_token.accesstoken:
        from core.database import db
        from bson import ObjectId
        await db.accessToken.update_one(
            {"_id": ObjectId(access_token.accesstoken)},
            {"$set": {"tenant_id": tenant_id}},
        )

    jwt_token = await create_jwt_role_token(
        token=access_token.accesstoken,
        user_id=user_id,
        role=normalized_role,
    )

    refresh_token = await token_repo.add_refresh_tokens(
        token_data=refreshTokenCreate(
            userId=user_id,
            previousAccessToken=access_token.accesstoken,
        )
    )

    return jwt_token, refresh_token.refreshtoken or ""


async def issue_tokens_for_user(user_id: str, role: str) -> tuple[str, str]:
    return await issue_tokens_for_role(user_id=user_id, role=role)
