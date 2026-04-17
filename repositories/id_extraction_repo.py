from __future__ import annotations

from typing import Optional

from core.database import db
from schemas.id_extraction_schema import IDExtractionCreate, IDExtractionOut

COLLECTION = "id_extractions"


async def create_id_extraction(payload: IDExtractionCreate) -> IDExtractionOut:
    """Create a new ID extraction record."""
    extraction_dict = payload.model_dump()
    result = await db[COLLECTION].insert_one(extraction_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return IDExtractionOut(**doc)


async def get_id_extraction(filter_dict: dict) -> Optional[IDExtractionOut]:
    """Fetch a single ID extraction record."""
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return IDExtractionOut(**doc)
