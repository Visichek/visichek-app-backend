from __future__ import annotations

from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.plan_schema import PlanCreate, PlanUpdate, PlanOut


COLLECTION = "plans"


async def create_plan(plan_data: PlanCreate) -> PlanOut:
    plan_dict = plan_data.model_dump(mode="json")
    result = await db[COLLECTION].insert_one(plan_dict)
    result = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return PlanOut(**result)


async def get_plan(filter_dict: dict) -> Optional[PlanOut]:
    try:
        result = await db[COLLECTION].find_one(filter_dict)
        if result is None:
            return None
        return PlanOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching plan: {str(e)}",
        )


async def get_plans(filter_dict: dict = {}, start: int = 0, stop: int = 100) -> List[PlanOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = db[COLLECTION].find(filter_dict).skip(start).limit(stop - start)
        plan_list = []
        async for doc in cursor:
            plan_list.append(PlanOut(**doc))
        return plan_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching plans: {str(e)}",
        )


async def update_plan(filter_dict: dict, plan_data: PlanUpdate) -> Optional[PlanOut]:
    update_dict = {k: v for k, v in plan_data.model_dump(mode="json").items() if v is not None}
    if not update_dict:
        return await get_plan(filter_dict)
    result = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return PlanOut(**result)


async def delete_plan(filter_dict: dict):
    return await db[COLLECTION].delete_one(filter_dict)
