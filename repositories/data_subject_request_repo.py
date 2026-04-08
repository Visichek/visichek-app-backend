from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.data_subject_request_schema import DSRCreate, DSRUpdate, DSROut


async def create_dsr(dsr_data: DSRCreate) -> DSROut:
    dsr_dict = dsr_data.model_dump()
    result = await db.data_subject_requests.insert_one(dsr_dict)
    result = await db.data_subject_requests.find_one({"_id": result.inserted_id})
    return DSROut(**result)


async def get_dsr(filter_dict: dict) -> Optional[DSROut]:
    result = await db.data_subject_requests.find_one(filter_dict)
    if result is None:
        return None
    return DSROut(**result)


async def get_dsrs(filter_dict: dict = {}, start=0, stop=100) -> List[DSROut]:
    cursor = db.data_subject_requests.find(filter_dict).sort("date_created", -1).skip(start).limit(stop - start)
    dsr_list = []
    async for doc in cursor:
        dsr_list.append(DSROut(**doc))
    return dsr_list


async def update_dsr(filter_dict: dict, dsr_data: DSRUpdate) -> DSROut:
    update_dict = {k: v for k, v in dsr_data.model_dump().items() if v is not None}
    result = await db.data_subject_requests.find_one_and_update(
        filter_dict, {"$set": update_dict}, return_document=ReturnDocument.AFTER,
    )
    return DSROut(**result)
