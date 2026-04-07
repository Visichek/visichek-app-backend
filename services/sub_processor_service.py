from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.sub_processor_repo import (
    create_sub_processor, get_sub_processor, get_sub_processors,
    update_sub_processor, delete_sub_processor,
)
from schemas.sub_processor_schema import SubProcessorCreate, SubProcessorUpdate, SubProcessorOut


async def add_sub_processor(sp_data: SubProcessorCreate) -> SubProcessorOut:
    return await create_sub_processor(sp_data)


async def retrieve_sub_processors(tenant_id: str) -> List[SubProcessorOut]:
    return await get_sub_processors(filter_dict={"tenant_id": tenant_id})


async def update_sub_processor_by_id(sp_id: str, tenant_id: str, sp_data: SubProcessorUpdate) -> SubProcessorOut:
    if not ObjectId.is_valid(sp_id):
        raise HTTPException(status_code=400, detail="Invalid sub-processor ID format")
    result = await update_sub_processor({"_id": ObjectId(sp_id), "tenant_id": tenant_id}, sp_data)
    if not result:
        raise HTTPException(status_code=404, detail="Sub-processor not found")
    return result


async def remove_sub_processor(sp_id: str, tenant_id: str):
    if not ObjectId.is_valid(sp_id):
        raise HTTPException(status_code=400, detail="Invalid sub-processor ID format")
    result = await delete_sub_processor({"_id": ObjectId(sp_id), "tenant_id": tenant_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Sub-processor not found")
