from bson import ObjectId
from fastapi import HTTPException
from typing import List
import time

from repositories.data_subject_request_repo import (
    create_dsr,
    get_dsr,
    get_dsrs,
    update_dsr,
)
from schemas.data_subject_request_schema import DSRCreate, DSRUpdate, DSROut


async def add_dsr(dsr_data: DSRCreate) -> DSROut:
    # Set SLA deadline to 30 days from now
    if not dsr_data.sla_deadline:
        dsr_data.sla_deadline = int(time.time()) + (30 * 86400)
    return await create_dsr(dsr_data)


async def retrieve_dsr_by_id(dsr_id: str, tenant_id: str) -> DSROut:
    if not ObjectId.is_valid(dsr_id):
        raise HTTPException(status_code=400, detail="Invalid DSR ID format")
    result = await get_dsr({"_id": ObjectId(dsr_id), "tenant_id": tenant_id})
    if not result:
        raise HTTPException(status_code=404, detail="Data subject request not found")
    return result


async def retrieve_dsrs(tenant_id: str, start=0, stop=100) -> List[DSROut]:
    return await get_dsrs(filter_dict={"tenant_id": tenant_id}, start=start, stop=stop)


async def update_dsr_by_id(dsr_id: str, tenant_id: str, dsr_data: DSRUpdate) -> DSROut:
    if not ObjectId.is_valid(dsr_id):
        raise HTTPException(status_code=400, detail="Invalid DSR ID format")
    result = await update_dsr(
        {"_id": ObjectId(dsr_id), "tenant_id": tenant_id}, dsr_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Data subject request not found or update failed"
        )
    return result
