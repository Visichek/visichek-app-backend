from __future__ import annotations

from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.subscription_schema import (
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionOut,
)


COLLECTION = "subscriptions"


async def create_subscription(sub_data: SubscriptionCreate) -> SubscriptionOut:
    sub_dict = sub_data.model_dump(mode="json")
    result = await db[COLLECTION].insert_one(sub_dict)
    result = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return SubscriptionOut(**result)


async def get_subscription(filter_dict: dict) -> Optional[SubscriptionOut]:
    try:
        result = await db[COLLECTION].find_one(filter_dict)
        if result is None:
            return None
        return SubscriptionOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching subscription: {str(e)}",
        )


async def get_subscriptions(
    filter_dict: dict = {}, start: int = 0, stop: int = 100
) -> List[SubscriptionOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = db[COLLECTION].find(filter_dict).skip(start).limit(stop - start)
        sub_list = []
        async for doc in cursor:
            sub_list.append(SubscriptionOut(**doc))
        return sub_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching subscriptions: {str(e)}",
        )


async def update_subscription(
    filter_dict: dict, sub_data: SubscriptionUpdate
) -> Optional[SubscriptionOut]:
    update_dict = {
        k: v for k, v in sub_data.model_dump(mode="json").items() if v is not None
    }
    if not update_dict:
        return await get_subscription(filter_dict)
    result = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return SubscriptionOut(**result)


async def delete_subscription(filter_dict: dict):
    return await db[COLLECTION].delete_one(filter_dict)
