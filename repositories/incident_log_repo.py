from pymongo import ReturnDocument
from core.database import db
from typing import List, Optional
from schemas.incident_log_schema import IncidentLogCreate, IncidentLogUpdate, IncidentLogOut


async def create_incident_log(log_data: IncidentLogCreate) -> IncidentLogOut:
    log_dict = log_data.model_dump()
    result = await db.incident_logs.insert_one(log_dict)
    result = await db.incident_logs.find_one({"_id": result.inserted_id})
    return IncidentLogOut(**result)


async def get_incident_log(filter_dict: dict) -> Optional[IncidentLogOut]:
    result = await db.incident_logs.find_one(filter_dict)
    if result is None:
        return None
    return IncidentLogOut(**result)


async def get_incident_logs(filter_dict: dict = {}, start=0, stop=100) -> List[IncidentLogOut]:
    cursor = db.incident_logs.find(filter_dict).sort("date_created", -1).skip(start).limit(stop - start)
    return [IncidentLogOut(**doc) async for doc in cursor]


async def update_incident_log(filter_dict: dict, log_data: IncidentLogUpdate) -> IncidentLogOut:
    update_dict = {k: v for k, v in log_data.model_dump().items() if v is not None}
    result = await db.incident_logs.find_one_and_update(
        filter_dict, {"$set": update_dict}, return_document=ReturnDocument.AFTER,
    )
    return IncidentLogOut(**result)
