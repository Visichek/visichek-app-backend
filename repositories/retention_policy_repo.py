from pymongo import ReturnDocument
from core.database import db
from typing import List, Optional
from schemas.retention_policy_schema import RetentionPolicyCreate, RetentionPolicyUpdate, RetentionPolicyOut


async def create_retention_policy(policy_data: RetentionPolicyCreate) -> RetentionPolicyOut:
    policy_dict = policy_data.model_dump()
    result = await db.retention_policies.insert_one(policy_dict)
    result = await db.retention_policies.find_one({"_id": result.inserted_id})
    return RetentionPolicyOut(**result)


async def get_retention_policy(filter_dict: dict) -> Optional[RetentionPolicyOut]:
    result = await db.retention_policies.find_one(filter_dict)
    if result is None:
        return None
    return RetentionPolicyOut(**result)


async def get_retention_policies(filter_dict: dict = {}) -> List[RetentionPolicyOut]:
    cursor = db.retention_policies.find(filter_dict)
    return [RetentionPolicyOut(**doc) async for doc in cursor]


async def update_retention_policy(filter_dict: dict, policy_data: RetentionPolicyUpdate) -> RetentionPolicyOut:
    update_dict = {k: v for k, v in policy_data.model_dump().items() if v is not None}
    result = await db.retention_policies.find_one_and_update(
        filter_dict, {"$set": update_dict}, return_document=ReturnDocument.AFTER,
    )
    return RetentionPolicyOut(**result)
