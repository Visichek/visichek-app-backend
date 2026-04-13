from __future__ import annotations

from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.discount_schema import DiscountCreate, DiscountUpdate, DiscountOut


COLLECTION = "discounts"


async def create_discount(discount_data: DiscountCreate) -> DiscountOut:
    discount_dict = discount_data.model_dump(mode="json")
    result = await db[COLLECTION].insert_one(discount_dict)
    result = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return DiscountOut(**result)


async def get_discount(filter_dict: dict) -> Optional[DiscountOut]:
    try:
        result = await db[COLLECTION].find_one(filter_dict)
        if result is None:
            return None
        return DiscountOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching discount: {str(e)}",
        )


async def get_discounts(
    filter_dict: dict = {}, start: int = 0, stop: int = 100
) -> List[DiscountOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = db[COLLECTION].find(filter_dict).skip(start).limit(stop - start)
        discount_list = []
        async for doc in cursor:
            discount_list.append(DiscountOut(**doc))
        return discount_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching discounts: {str(e)}",
        )


async def update_discount(
    filter_dict: dict, discount_data: DiscountUpdate
) -> Optional[DiscountOut]:
    update_dict = {
        k: v for k, v in discount_data.model_dump(mode="json").items() if v is not None
    }
    if not update_dict:
        return await get_discount(filter_dict)
    result = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return DiscountOut(**result)


async def delete_discount(filter_dict: dict):
    return await db[COLLECTION].delete_one(filter_dict)


async def increment_redemptions(filter_dict: dict) -> Optional[DiscountOut]:
    """Atomically increment the redemption counter."""
    result = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$inc": {"current_redemptions": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return DiscountOut(**result)
