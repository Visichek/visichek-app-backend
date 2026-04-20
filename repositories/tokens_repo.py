from core.database import db
from core import token_cache

from schemas.tokens_schema import (
    accessTokenCreate,
    refreshTokenCreate,
    accessTokenOut,
    refreshTokenOut,
)
from datetime import datetime, timezone, timedelta
from dateutil import parser  # type: ignore[import-untyped]
from bson import ObjectId, errors
from fastapi import HTTPException
from repositories.admin_repo import get_admin
from security.encrypting_jwt import (
    decode_jwt_token,
    decode_jwt_token_without_expiration,
)


async def add_access_tokens(token_data: accessTokenCreate) -> accessTokenOut:
    token = token_data.model_dump()
    token["role"] = "member"
    result = await db.accessToken.insert_one(token)
    tokn = await db.accessToken.find_one({"_id": result.inserted_id})
    accessToken = accessTokenOut(**tokn)

    return accessToken


async def add_admin_access_tokens(token_data: accessTokenCreate) -> accessTokenOut:
    token = token_data.model_dump()
    token["role"] = "admin"
    token["status"] = "active"
    result = await db.accessToken.insert_one(token)
    tokn = await db.accessToken.find_one({"_id": result.inserted_id})
    accessToken = accessTokenOut(**tokn)

    return accessToken


async def add_system_user_access_tokens(
    token_data: accessTokenCreate, role: str
) -> accessTokenOut:
    """Add access token for VisiChek system user roles (receptionist, dept_admin, etc.)."""
    token = token_data.model_dump()
    token["role"] = role
    token["status"] = "active"
    result = await db.accessToken.insert_one(token)
    tokn = await db.accessToken.find_one({"_id": result.inserted_id})
    return accessTokenOut(**tokn)


async def update_admin_access_tokens(token: str) -> accessTokenOut:
    updatedToken = await db.accessToken.find_one_and_update(
        filter={"_id": ObjectId(token)},
        update={"$set": {"status": "active"}},
        return_document=True,
    )
    accessToken = accessTokenOut(**updatedToken)
    return accessToken


async def add_refresh_tokens(token_data: refreshTokenCreate) -> refreshTokenOut:
    token = token_data.model_dump()
    result = await db.refreshToken.insert_one(token)
    tokn = await db.refreshToken.find_one({"_id": result.inserted_id})
    refreshToken = refreshTokenOut(**tokn)
    return refreshToken


async def delete_access_token(accessToken):
    # await db.refreshToken.delete_many({"previousAccessToken":accessToken})
    await db.accessToken.find_one_and_delete({"_id": ObjectId(accessToken)})
    # Drop any in-process cache entries that point at this token so a
    # logout on the same worker takes effect immediately. Cross-worker
    # invalidation is bounded by the cache TTL.
    token_cache.invalidate_by_token_id(str(accessToken))


async def delete_refresh_token(refreshToken: str):
    try:
        obj_id = ObjectId(refreshToken)
    except errors.InvalidId:
        raise HTTPException(status_code=401, detail="Invalid Refresh Id")
    result = await db.refreshToken.find_one_and_delete({"_id": obj_id})
    if result:
        return True


def is_older_than_days(date_value, days=10):
    """
    Accepts either an ISO-8601 string or a UNIX timestamp (int/float).
    Returns True if older than `days` days.
    """
    # Determine type and parse accordingly
    if isinstance(date_value, (int, float)):
        # It's a UNIX timestamp (seconds)
        created_date = datetime.fromtimestamp(date_value, tz=timezone.utc)
    else:
        # Assume ISO string
        created_date = parser.isoparse(str(date_value))

    # Get the current time in UTC (with same tzinfo)
    now = datetime.now(timezone.utc)

    # Check if the difference is greater than the given number of days
    return (now - created_date) > timedelta(days=days)


async def _resolve_access_token_id(accessToken: str, allow_expired: bool) -> str | None:
    decoded = (
        await decode_jwt_token_without_expiration(accessToken)
        if allow_expired
        else await decode_jwt_token(accessToken)
    )
    if decoded and decoded.get("accessToken"):
        return decoded["accessToken"]

    try:
        ObjectId(accessToken)
        return accessToken
    except errors.InvalidId:
        return None


def _mark_tenant_active_safe(tenant_id: str | None) -> None:
    """Signal to the precompute worker that ``tenant_id`` has live traffic.

    Swallows every failure — this is best-effort telemetry, not a security
    check. Kept inline so get_access_token stays the one chokepoint where
    we can observe every authenticated request.
    """
    if not tenant_id:
        return
    try:
        from core.queue.precompute import mark_tenant_active

        mark_tenant_active(tenant_id)
    except Exception:
        pass


def _mark_user_active_safe(user_id: str | None, tenant_id: str | None) -> None:
    """Signal that ``user_id`` has live traffic (for per-user precompute).

    Mirrors :func:`_mark_tenant_active_safe` and is best-effort; failures
    are silent. Tenant id is recorded alongside the user so the precompute
    fanout routes the refresh task to the right tenant context.
    """
    if not user_id:
        return
    try:
        from core.queue.precompute import mark_user_active

        mark_user_active(user_id, tenant_id)
    except Exception:
        pass


async def get_access_token(
    accessToken: str, allow_expired: bool = False
) -> accessTokenOut | None:
    # Fast path: serve from the in-process cache when we're doing a
    # standard (non-expired) lookup. The expired-read path is rare and
    # bypasses the cache so stale state can't mask rotation issues.
    if not allow_expired:
        cached = token_cache.get(accessToken)
        if cached is not None:
            _mark_tenant_active_safe(cached.tenant_id)
            _mark_user_active_safe(cached.userId, cached.tenant_id)
            return cached

    token_id = await _resolve_access_token_id(
        accessToken=accessToken, allow_expired=allow_expired
    )
    if not token_id:
        return None

    token = await db.accessToken.find_one({"_id": ObjectId(token_id)})
    if not token:
        return None

    if not allow_expired and is_older_than_days(date_value=token["dateCreated"]):
        await delete_access_token(accessToken=str(token["_id"]))
        return None

    if token.get("role") == "admin" and token.get("status") != "active":
        return None

    result = accessTokenOut(**token)
    if not allow_expired:
        token_cache.put(accessToken, result)
    _mark_tenant_active_safe(result.tenant_id)
    _mark_user_active_safe(result.userId, result.tenant_id)
    return result


async def get_access_tokens(accessToken: str) -> accessTokenOut | None:
    return await get_access_token(accessToken=accessToken, allow_expired=False)


async def get_admin_access_tokens(accessToken: str) -> accessTokenOut | None:
    token = await get_access_token(accessToken=accessToken, allow_expired=False)
    if not token or token.role != "admin":
        return None

    user_id = token.userId
    if not user_id:
        return None

    if await get_admin(filter_dict={"_id": ObjectId(user_id)}):
        return token
    return None


async def get_inactive_access_token(token_id: str) -> accessTokenOut | None:
    try:
        obj_id = ObjectId(token_id)
    except errors.InvalidId:
        return None

    token = await db.accessToken.find_one({"_id": obj_id, "status": "inactive"})
    if token:
        return accessTokenOut(**token)
    return None


async def get_access_token_allow_expired(accessToken: str) -> accessTokenOut | None:
    return await get_access_token(accessToken=accessToken, allow_expired=True)


async def get_refresh_tokens(refreshToken: str) -> refreshTokenOut | None:
    token = await db.refreshToken.find_one({"_id": ObjectId(refreshToken)})
    if token:
        tokn = refreshTokenOut(**token)
        return tokn

    else:
        return None


async def delete_access_and_refresh_token_with_user_id(userId: str) -> bool:
    result = await db.refreshToken.delete_many({"userId": userId})
    result1 = await db.accessToken.delete_many({"userId": userId})
    return result.acknowledged and result1.acknowledged


async def delete_all_tokens_with_user_id(userId: str):
    await db.refreshToken.delete_many(filter={"userId": userId})
    await db.accessToken.delete_many(filter={"userId": userId})
    # Bulk revocation: clearing the whole in-process cache is cheaper and
    # simpler than tracking which entries belong to this user. The cache
    # rebuilds from the next few requests.
    token_cache.clear()


async def delete_all_tokens_with_admin_id(adminId: str):
    await db.refreshToken.delete_many(filter={"userId": adminId})
    await db.accessToken.delete_many(filter={"userId": adminId})
    token_cache.clear()
