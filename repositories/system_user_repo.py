from bson import ObjectId
from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.system_user_schema import SystemUserCreate, SystemUserUpdate, SystemUserOut


async def create_system_user(
    user_data: SystemUserCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> SystemUserOut:
    user_dict = user_data.model_dump()
    if preassigned_id:
        user_dict["_id"] = ObjectId(preassigned_id)
    result = await db.system_users.insert_one(user_dict)
    result = await db.system_users.find_one({"_id": result.inserted_id})
    return SystemUserOut(**result)


async def get_system_user(filter_dict: dict) -> Optional[SystemUserOut]:
    try:
        result = await db.system_users.find_one(filter_dict)
        if result is None:
            return None
        return SystemUserOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching system user: {str(e)}",
        )


async def get_system_users(
    filter_dict: dict = {}, start=0, stop=100
) -> List[SystemUserOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = db.system_users.find(filter_dict).skip(start).limit(stop - start)
        user_list = []
        async for doc in cursor:
            user_obj = SystemUserOut(**doc)
            user_list.append(user_obj)
        return user_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching system users: {str(e)}",
        )


async def update_system_user(
    filter_dict: dict, user_data: SystemUserUpdate
) -> SystemUserOut:
    update_dict = {k: v for k, v in user_data.model_dump().items() if v is not None}
    result = await db.system_users.find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    return SystemUserOut(**result)


async def delete_system_user(filter_dict: dict):
    return await db.system_users.delete_one(filter_dict)


async def count_system_users(filter_dict: dict | None = None) -> int:
    if filter_dict is None:
        filter_dict = {}
    return await db.system_users.count_documents(filter_dict)


async def get_raw_system_users_by_email(email: str) -> list[dict]:
    """Return raw system_user documents (with password_hash) matching an email.

    Used by login flow to verify password against every record sharing the email
    so users can be matched across tenants.
    """
    cursor = db.system_users.find({"email": email})
    return [doc async for doc in cursor]


# ---------------------------------------------------------------------------
# Main super_admin helpers
# ---------------------------------------------------------------------------


async def get_main_super_admin(tenant_id: str) -> Optional[SystemUserOut]:
    """Return the tenant's main super_admin row, or None if not set."""
    return await get_system_user({"tenant_id": tenant_id, "is_main_super_admin": True})


async def count_active_super_admins(tenant_id: str) -> int:
    """Count active (account_status=ACTIVE) super_admins in a tenant."""
    return await count_system_users(
        {
            "tenant_id": tenant_id,
            "role": "super_admin",
            "account_status": "ACTIVE",
        }
    )


async def list_active_super_admins_oldest_first(tenant_id: str) -> List[SystemUserOut]:
    """Active super_admins sorted by date_created ascending — earliest wins.

    Used by the invariant backfill to pick which row should hold the main
    flag when one is missing or duplicated.
    """
    cursor = db.system_users.find(
        {
            "tenant_id": tenant_id,
            "role": "super_admin",
            "account_status": "ACTIVE",
        }
    ).sort("date_created", 1)
    rows: List[SystemUserOut] = []
    async for doc in cursor:
        rows.append(SystemUserOut(**doc))
    return rows


async def set_main_super_admin_flag(
    user_id: str, tenant_id: str, *, value: bool
) -> None:
    """Direct $set of the main flag. Bypasses SystemUserUpdate so we don't
    expose the flag on the public update schema. Caller is responsible for
    sequencing flag flips to satisfy the partial-unique index — see
    ``services/main_super_admin_backfill.py`` and the transfer service.
    """
    await db.system_users.update_one(
        {"_id": ObjectId(user_id), "tenant_id": tenant_id},
        {"$set": {"is_main_super_admin": bool(value)}},
    )


async def clear_main_super_admin_flag_for_tenant(
    tenant_id: str, *, except_user_id: Optional[str] = None
) -> int:
    """Clear the main flag from every row in a tenant except ``except_user_id``.

    Returns the modified count. Used during the backfill collapse and the
    transfer flow to atomically clear stale True flags before setting the
    new winner.
    """
    filter_doc: dict = {
        "tenant_id": tenant_id,
        "is_main_super_admin": True,
    }
    if except_user_id and ObjectId.is_valid(except_user_id):
        filter_doc["_id"] = {"$ne": ObjectId(except_user_id)}
    result = await db.system_users.update_many(
        filter_doc, {"$set": {"is_main_super_admin": False}}
    )
    return int(getattr(result, "modified_count", 0) or 0)
