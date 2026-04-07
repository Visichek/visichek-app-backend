from core.database import db
from typing import List
from schemas.deletion_log_schema import DeletionLogCreate, DeletionLogOut


async def create_deletion_log(log_data: DeletionLogCreate) -> DeletionLogOut:
    log_dict = log_data.model_dump()
    result = await db.deletion_logs.insert_one(log_dict)
    result = await db.deletion_logs.find_one({"_id": result.inserted_id})
    return DeletionLogOut(**result)


async def get_deletion_logs(filter_dict: dict = {}, start=0, stop=100) -> List[DeletionLogOut]:
    cursor = db.deletion_logs.find(filter_dict).sort("timestamp", -1).skip(start).limit(stop - start)
    return [DeletionLogOut(**doc) async for doc in cursor]
