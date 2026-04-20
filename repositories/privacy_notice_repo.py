from bson import ObjectId
from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.privacy_notice_schema import (
    PrivacyNoticeCreate,
    PrivacyNoticeUpdate,
    PrivacyNoticeOut,
)


async def create_privacy_notice(
    notice_data: PrivacyNoticeCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> PrivacyNoticeOut:
    notice_dict = notice_data.model_dump()
    if preassigned_id:
        notice_dict["_id"] = ObjectId(preassigned_id)
    result = await db.privacy_notice_versions.insert_one(notice_dict)
    result = await db.privacy_notice_versions.find_one({"_id": result.inserted_id})
    return PrivacyNoticeOut(**result)


async def get_privacy_notice(filter_dict: dict) -> Optional[PrivacyNoticeOut]:
    try:
        result = await db.privacy_notice_versions.find_one(filter_dict)
        if result is None:
            return None
        return PrivacyNoticeOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching privacy notice: {str(e)}",
        )


async def get_active_notice_for_tenant(tenant_id: str) -> Optional[PrivacyNoticeOut]:
    return await get_privacy_notice({"tenant_id": tenant_id, "is_active": True})


async def get_privacy_notices(
    filter_dict: dict = {}, start=0, stop=100
) -> List[PrivacyNoticeOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = (
            db.privacy_notice_versions.find(filter_dict)
            .sort("date_created", -1)
            .skip(start)
            .limit(stop - start)
        )
        notice_list = []
        async for doc in cursor:
            notice_list.append(PrivacyNoticeOut(**doc))
        return notice_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching privacy notices: {str(e)}",
        )


async def update_privacy_notice(
    filter_dict: dict, notice_data: PrivacyNoticeUpdate
) -> PrivacyNoticeOut:
    update_dict = {k: v for k, v in notice_data.model_dump().items() if v is not None}
    result = await db.privacy_notice_versions.find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    return PrivacyNoticeOut(**result)
