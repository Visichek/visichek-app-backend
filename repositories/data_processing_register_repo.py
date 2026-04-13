from core.database import db
from typing import List
from schemas.data_processing_register_schema import DPRCreate, DPROut


async def create_dpr_entry(dpr_data: DPRCreate) -> DPROut:
    dpr_dict = dpr_data.model_dump()
    result = await db.data_processing_register.insert_one(dpr_dict)
    result = await db.data_processing_register.find_one({"_id": result.inserted_id})
    return DPROut(**result)


async def get_dpr_entries(filter_dict: dict = {}) -> List[DPROut]:
    cursor = db.data_processing_register.find(filter_dict)
    return [DPROut(**doc) async for doc in cursor]
