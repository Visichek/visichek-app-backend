from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.system_user_schema import SystemUserCreate, SystemUserUpdate, SystemUserOut


async def create_system_user(user_data: SystemUserCreate) -> SystemUserOut:
    user_dict = user_data.model_dump()
    result = await db.system_users.insert_one(user_dict)
    result = await db.system_users.find_one({"_id": result.inserted_id})
    return SystemUserOut(**result)


async def get_system_user(filter_dict: dict) -> Optional[SystemUserOut]:
    try:
        result = await db.system_users.find_one(filter_dict)
        if result is None:
            return None
        return SystemUserOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching system user: {str(e)}",
        )


async def get_system_users(filter_dict: dict = {}, start=0, stop=100) -> List[SystemUserOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = db.system_users.find(filter_dict).skip(start).limit(stop - start)
        user_list = []
        async for doc in cursor:
            user_obj = SystemUserOut(**doc)
            user_list.append(user_obj)
        return user_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching system users: {str(e)}",
        )


async def update_system_user(filter_dict: dict, user_data: SystemUserUpdate) -> SystemUserOut:
    update_dict = {k: v for k, v in user_data.model_dump().items() if v is not None}
    result = await db.system_users.find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    return SystemUserOut(**result)


async def delete_system_user(filter_dict: dict):
    return await db.system_users.delete_one(filter_dict)
