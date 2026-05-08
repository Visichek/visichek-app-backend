from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException

from core.database import db
from core.security_policy import get_security_policy
from security.hash import hash_password, check_password
from security.password_policy import (
    check_password_history,
    record_password_in_history,
    validate_password_strength,
)


async def _enforce_new_password_policy(
    user_id: str,
    new_password: str,
    role: str,
) -> bytes:
    """Validate and hash a new password against the platform security policy.

    Raises 422 with the policy's error list when validation fails, and 422
    when the password matches one of the user's last N hashes.
    """
    policy = await get_security_policy()
    result = validate_password_strength(new_password, policy=policy)
    if not result.is_valid:
        raise HTTPException(status_code=422, detail="; ".join(result.errors))

    safe = await check_password_history(
        user_id,
        new_password,
        role=role,
        history_count=policy.password_history_count,
    )
    if not safe:
        raise HTTPException(
            status_code=422,
            detail=(
                "New password must not match any of your last "
                f"{policy.password_history_count} passwords"
            ),
        )

    return hash_password(new_password)


async def change_admin_password(
    admin_id: str,
    current_password: str,
    new_password: str,
) -> None:
    """Change an admin's password after verifying the current one."""
    admin = await db.admins.find_one({"_id": ObjectId(admin_id)})
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    if not check_password(current_password, admin["password"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    hashed = await _enforce_new_password_policy(admin_id, new_password, role="admin")

    await db.admins.update_one(
        {"_id": ObjectId(admin_id)},
        {"$set": {"password": hashed}},
    )

    policy = await get_security_policy()
    await record_password_in_history(
        admin_id, hashed, role="admin", history_count=policy.password_history_count
    )


async def change_system_user_password(
    user_id: str,
    current_password: str,
    new_password: str,
) -> None:
    """Change a system user's password after verifying the current one."""
    user = await db.system_users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not check_password(current_password, user["password"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    hashed = await _enforce_new_password_policy(
        user_id, new_password, role="system_user"
    )

    await db.system_users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"password": hashed}},
    )

    policy = await get_security_policy()
    await record_password_in_history(
        user_id,
        hashed,
        role="system_user",
        history_count=policy.password_history_count,
    )
