from __future__ import annotations

from typing import Optional

from core.database import db
from schemas.badge_schema import BadgeCreate, BadgeOut

COLLECTION = "badges"


async def create_badge(payload: BadgeCreate) -> BadgeOut:
    """Create a new badge."""
    badge_dict = payload.model_dump()
    result = await db[COLLECTION].insert_one(badge_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return BadgeOut(**doc)


async def get_badge(filter_dict: dict) -> Optional[BadgeOut]:
    """Fetch a single badge."""
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return BadgeOut(**doc)


async def update_badge(badge_id: str, update_dict: dict) -> BadgeOut:
    """Update a badge."""
    result = await db[COLLECTION].find_one_and_update(
        {"_id": badge_id},
        {"$set": update_dict},
        return_document=True,
    )
    if result is None:
        raise ValueError(f"Badge {badge_id} not found")
    return BadgeOut(**result)


async def revoke_badge(badge_id: str) -> BadgeOut:
    """Revoke a badge."""
    import time

    update_dict = {"revoked_at": int(time.time())}
    return await update_badge(badge_id, update_dict)


async def get_badge_by_qr_value(qr_code_value: str) -> Optional[BadgeOut]:
    """Fetch a badge by its QR code value."""
    doc = await db[COLLECTION].find_one({"qr_code_value": qr_code_value})
    if doc is None:
        return None
    return BadgeOut(**doc)


async def get_badges_by_checkin_ids(
    tenant_id: str, checkin_ids: list[str]
) -> list[BadgeOut]:
    """Fetch active badges for approved check-ins."""
    if not checkin_ids:
        return []
    cursor = (
        db[COLLECTION]
        .find(
            {
                "tenant_id": tenant_id,
                "checkin_id": {"$in": checkin_ids},
                "revoked_at": None,
            }
        )
        .sort("issued_at", -1)
    )
    badges: list[BadgeOut] = []
    async for doc in cursor:
        badges.append(BadgeOut(**doc))
    return badges
