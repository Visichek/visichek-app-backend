from pymongo import ReturnDocument
from core.database import db
from typing import List, Optional
from schemas.sub_processor_schema import SubProcessorCreate, SubProcessorUpdate, SubProcessorOut


async def create_sub_processor(sp_data: SubProcessorCreate) -> SubProcessorOut:
    sp_dict = sp_data.model_dump()
    result = await db.sub_processors.insert_one(sp_dict)
    result = await db.sub_processors.find_one({"_id": result.inserted_id})
    return SubProcessorOut(**result)


async def get_sub_processor(filter_dict: dict) -> Optional[SubProcessorOut]:
    result = await db.sub_processors.find_one(filter_dict)
    if result is None:
        return None
    return SubProcessorOut(**result)


async def get_sub_processors(filter_dict: dict = {}) -> List[SubProcessorOut]:
    cursor = db.sub_processors.find(filter_dict)
    return [SubProcessorOut(**doc) async for doc in cursor]


async def update_sub_processor(filter_dict: dict, sp_data: SubProcessorUpdate) -> SubProcessorOut:
    update_dict = {k: v for k, v in sp_data.model_dump().items() if v is not None}
    result = await db.sub_processors.find_one_and_update(
        filter_dict, {"$set": update_dict}, return_document=ReturnDocument.AFTER,
    )
    return SubProcessorOut(**result)


async def delete_sub_processor(filter_dict: dict):
    return await db.sub_processors.delete_one(filter_dict)
