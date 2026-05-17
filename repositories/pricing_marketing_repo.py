from __future__ import annotations

from typing import Optional

from core.database import db
from schemas.pricing_marketing_schema import (
    PricingMarketingOverlayCreate,
    PricingMarketingOverlayOut,
)

COLLECTION = "pricing_marketing"


async def get_overlay() -> Optional[PricingMarketingOverlayOut]:
    """Read the singleton marketing overlay document, if any."""
    doc = await db[COLLECTION].find_one({})
    if doc:
        return PricingMarketingOverlayOut(**doc)
    return None


async def create_overlay(
    data: PricingMarketingOverlayCreate,
) -> PricingMarketingOverlayOut:
    """Insert the singleton document. Caller is expected to have ensured
    no overlay already exists."""
    payload = data.model_dump()
    result = await db[COLLECTION].insert_one(payload)
    fresh = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return PricingMarketingOverlayOut(**fresh)


async def replace_overlay(
    payload: dict,
) -> Optional[PricingMarketingOverlayOut]:
    """Atomic upsert of the singleton document by raw dict.

    Used by the writer after merging the incoming PATCH on top of the
    existing overlay. Replaces the entire document body but preserves
    the doc id by virtue of the empty filter + upsert.
    """
    await db[COLLECTION].update_one({}, {"$set": payload}, upsert=True)
    fresh = await db[COLLECTION].find_one({})
    if fresh:
        return PricingMarketingOverlayOut(**fresh)
    return None
