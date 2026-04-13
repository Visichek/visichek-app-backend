from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.privacy_notice_repo import (
    create_privacy_notice,
    get_active_notice_for_tenant,
    get_privacy_notices,
    update_privacy_notice,
)
from schemas.privacy_notice_schema import (
    PrivacyNoticeCreate,
    PrivacyNoticeUpdate,
    PrivacyNoticeOut,
)


async def add_privacy_notice(notice_data: PrivacyNoticeCreate) -> PrivacyNoticeOut:
    # Deactivate any existing active notice for this tenant
    existing_active = await get_active_notice_for_tenant(notice_data.tenant_id)
    if existing_active and notice_data.is_active:
        await update_privacy_notice(
            {"_id": ObjectId(existing_active.id)},
            PrivacyNoticeUpdate(is_active=False),
        )
    return await create_privacy_notice(notice_data)


async def retrieve_active_notice(tenant_id: str) -> PrivacyNoticeOut:
    result = await get_active_notice_for_tenant(tenant_id)
    if not result:
        raise HTTPException(
            status_code=404, detail="No active privacy notice found for tenant"
        )
    return result


async def retrieve_privacy_notices(
    tenant_id: str, start=0, stop=100
) -> List[PrivacyNoticeOut]:
    return await get_privacy_notices(
        filter_dict={"tenant_id": tenant_id}, start=start, stop=stop
    )


async def update_notice_by_id(
    notice_id: str, tenant_id: str, notice_data: PrivacyNoticeUpdate
) -> PrivacyNoticeOut:
    if not ObjectId.is_valid(notice_id):
        raise HTTPException(status_code=400, detail="Invalid notice ID format")
    result = await update_privacy_notice(
        {"_id": ObjectId(notice_id), "tenant_id": tenant_id}, notice_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Privacy notice not found or update failed"
        )
    return result
