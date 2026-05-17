from __future__ import annotations

from typing import Optional

from core.database import db
from schemas.faq_schema import FaqOverlayCreate, FaqOverlayOut

COLLECTION = "faqs"


async def get_overlay() -> Optional[FaqOverlayOut]:
    doc = await db[COLLECTION].find_one({})
    if doc:
        return FaqOverlayOut(**doc)
    return None


async def create_overlay(data: FaqOverlayCreate) -> FaqOverlayOut:
    payload = data.model_dump()
    result = await db[COLLECTION].insert_one(payload)
    fresh = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return FaqOverlayOut(**fresh)


async def replace_overlay(payload: dict) -> Optional[FaqOverlayOut]:
    await db[COLLECTION].update_one({}, {"$set": payload}, upsert=True)
    fresh = await db[COLLECTION].find_one({})
    if fresh:
        return FaqOverlayOut(**fresh)
    return None
