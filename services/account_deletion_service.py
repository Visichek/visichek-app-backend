from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException

from core.database import db
from security.hash import check_password


async def delete_admin_account(admin_id: str, password: str) -> None:
    """Delete an admin account after password confirmation.

    - Verifies password
    - Revokes all sessions
    - Deletes all tokens
    - Marks the account as INACTIVE
    """
    admin = await db.admins.find_one({"_id": ObjectId(admin_id)})
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    if not check_password(password, admin["password"]):
        raise HTTPException(status_code=401, detail="Invalid password")

    # Mark as inactive (soft delete)
    await db.admins.update_one(
        {"_id": ObjectId(admin_id)},
        {"$set": {"accountStatus": "INACTIVE"}},
    )

    # Revoke all sessions
    await db["sessions"].delete_many({"user_id": admin_id, "user_type": "admin"})

    # Delete all tokens
    from repositories.tokens_repo import delete_all_tokens_with_admin_id
    await delete_all_tokens_with_admin_id(adminId=admin_id)

    # Record audit event (fire-and-forget)
    try:
        from services.audit_service import record_audit_event
        await record_audit_event(
            actor_id=admin_id,
            actor_role="admin",
            action="admin.account_deleted",
            resource_type="admin",
            resource_id=admin_id,
            details={"method": "self_deletion"},
        )
    except Exception:
        pass


async def delete_system_user_account(user_id: str, password: str, tenant_id: str) -> None:
    """Delete a system user account after password confirmation.

    - Verifies password
    - Checks user is not the sole super_admin
    - Checks tenant doesn't have an active subscription (for super_admins)
    - Revokes all sessions and tokens
    - Marks account as INACTIVE
    """
    user = await db.system_users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not check_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid password")

    # If super_admin, check they're not the last one
    if user.get("role") == "super_admin":
        super_admin_count = await db.system_users.count_documents({
            "tenant_id": tenant_id,
            "role": "super_admin",
            "account_status": "ACTIVE",
        })
        if super_admin_count <= 1:
            raise HTTPException(
                status_code=403,
                detail="Cannot delete the sole super admin of a tenant. Transfer ownership first.",
            )

        # Check for active subscription
        active_sub = await db["subscriptions"].find_one({
            "tenant_id": tenant_id,
            "status": {"$in": ["active", "trialing"]},
        })
        if active_sub:
            raise HTTPException(
                status_code=403,
                detail="Cannot delete super admin while the tenant has an active subscription. Cancel the subscription first.",
            )

    # Mark as inactive (soft delete)
    await db.system_users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"account_status": "INACTIVE", "is_active": False}},
    )

    # Revoke all sessions
    await db["sessions"].delete_many({"user_id": user_id, "user_type": "system_user"})

    # Delete all tokens
    from repositories.tokens_repo import delete_all_tokens_with_user_id
    await delete_all_tokens_with_user_id(userId=user_id)

    # Record audit event (fire-and-forget)
    try:
        from services.audit_service import record_audit_event
        await record_audit_event(
            actor_id=user_id,
            actor_role=user.get("role", "system_user"),
            action="system_user.account_deleted",
            resource_type="system_user",
            resource_id=user_id,
            tenant_id=tenant_id,
            details={"method": "self_deletion"},
        )
    except Exception:
        pass
