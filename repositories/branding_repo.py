from __future__ import annotations

from bson import ObjectId
from typing import Optional

from core.database import db
from schemas.branding_schema import BrandingCreate, BrandingUpdate, BrandingOut

COLLECTION = "brandings"


async def create_branding(branding_data: BrandingCreate) -> BrandingOut:
    """Insert a new branding document."""
    doc = branding_data.model_dump()
    result = await db[COLLECTION].insert_one(doc)
    new_doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return BrandingOut(**new_doc)


async def get_branding(filter_dict: dict) -> Optional[BrandingOut]:
    """Fetch a single branding document matching the filter."""
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc:
        return BrandingOut(**doc)
    return None


async def update_branding(filter_dict: dict, branding_data: BrandingUpdate) -> Optional[BrandingOut]:
    """Update a branding document. Returns updated doc or None."""
    update_fields = branding_data.model_dump(exclude_none=True)
    if not update_fields:
        return await get_branding(filter_dict)

    result = await db[COLLECTION].update_one(filter_dict, {"$set": update_fields})
    if result.matched_count == 0:
        return None
    return await get_branding(filter_dict)


async def delete_branding(filter_dict: dict):
    """Delete a branding document."""
    return await db[COLLECTION].delete_one(filter_dict)
