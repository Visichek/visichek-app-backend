from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException

from core.database import db
from security.hash import hash_password, check_password


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

    # Validate new password strength
    from security.password_policy import validate_password_strength, check_password_history
    validate_password_strength(new_password)

    # Check password history
    reused = await check_password_history(admin_id, new_password, role="admin")
    if reused:
        raise HTTPException(
            status_code=422,
            detail="New password must not match any of your last 5 passwords",
        )

    hashed = hash_password(new_password)
    await db.admins.update_one(
        {"_id": ObjectId(admin_id)},
        {"$set": {"password": hashed}},
    )

    # Record in password history
    from security.password_policy import record_password_in_history
    await record_password_in_history(admin_id, hashed, role="admin")


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

    # Validate new password strength
    from security.password_policy import validate_password_strength, check_password_history
    validate_password_strength(new_password)

    # Check password history
    reused = await check_password_history(user_id, new_password, role="system_user")
    if reused:
        raise HTTPException(
            status_code=422,
            detail="New password must not match any of your last 5 passwords",
        )

    hashed = hash_password(new_password)
    await db.system_users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"password": hashed}},
    )

    # Record in password history
    from security.password_policy import record_password_in_history
    await record_password_in_history(user_id, hashed, role="system_user")
