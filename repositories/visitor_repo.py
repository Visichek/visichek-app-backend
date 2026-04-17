from __future__ import annotations

from typing import Optional

from core.database import db
from schemas.visitor_schema import VisitorCreate, VisitorOut, VisitorUpdate

COLLECTION = "visitors"


async def create_visitor(payload: VisitorCreate) -> VisitorOut:
    """Create a new visitor."""
    visitor_dict = payload.model_dump()
    result = await db[COLLECTION].insert_one(visitor_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return VisitorOut(**doc)


async def get_visitor(filter_dict: dict) -> Optional[VisitorOut]:
    """Fetch a single visitor."""
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return VisitorOut(**doc)


async def find_visitor_by_email_or_phone(
    tenant_id: str, email: Optional[str] = None, phone: Optional[str] = None
) -> Optional[VisitorOut]:
    """Find a visitor by email and/or phone within a tenant.

    If both email and phone are provided, validates they match the same visitor.
    Returns the visitor or None if not found.
    """
    filters = {"tenant_id": tenant_id}

    if email:
        filters["email"] = email
    if phone:
        filters["phone"] = phone

    doc = await db[COLLECTION].find_one(filters)
    if doc is None:
        return None
    return VisitorOut(**doc)


async def update_visitor(visitor_id: str, data: VisitorUpdate) -> VisitorOut:
    """Update a visitor."""
    update_dict = data.model_dump(exclude_unset=True)
    result = await db[COLLECTION].find_one_and_update(
        {"_id": visitor_id},
        {"$set": update_dict},
        return_document=True,
    )
    if result is None:
        raise ValueError(f"Visitor {visitor_id} not found")
    return VisitorOut(**result)
