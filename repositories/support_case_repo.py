from __future__ import annotations

import time
from typing import List, Optional

from bson import ObjectId
from pymongo import ReturnDocument

from core.database import db
from schemas.imports import SupportCaseStatus
from schemas.support_case_schema import (
    SupportCaseCreate,
    SupportCaseOut,
    SupportCaseUpdate,
)

COLLECTION = "support_cases"


async def create_support_case(
    case: SupportCaseCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> SupportCaseOut:
    doc = case.model_dump()
    if preassigned_id and ObjectId.is_valid(preassigned_id):
        doc["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(doc)
    stored = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return SupportCaseOut(**stored)  # type: ignore[arg-type]


async def get_support_case_by_id(
    case_id: str, tenant_id: Optional[str] = None
) -> Optional[SupportCaseOut]:
    if not ObjectId.is_valid(case_id):
        return None
    filter_dict: dict = {"_id": ObjectId(case_id)}
    if tenant_id:
        filter_dict["tenant_id"] = tenant_id
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return SupportCaseOut(**doc)


async def list_support_cases(
    tenant_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    assigned_admin_id: Optional[str] = None,
    support_tier: Optional[str] = None,  # reserved for admin list join (not queried here)
    start: int = 0,
    stop: int = 100,
) -> List[SupportCaseOut]:
    _ = support_tier  # accepted for call-site symmetry; filter applied at service layer
    filter_dict: dict = {}
    if tenant_id:
        filter_dict["tenant_id"] = tenant_id
    if status:
        filter_dict["status"] = status
    if priority:
        filter_dict["priority"] = priority
    if category:
        filter_dict["category"] = category
    if assigned_admin_id:
        filter_dict["assigned_admin_id"] = assigned_admin_id

    limit = max(stop - start, 0)
    cursor = (
        db[COLLECTION]
        .find(filter_dict)
        .sort("last_message_at", -1)
        .skip(start)
        .limit(limit)
    )
    return [SupportCaseOut(**doc) async for doc in cursor]


async def update_support_case(
    case_id: str, update: SupportCaseUpdate
) -> Optional[SupportCaseOut]:
    if not ObjectId.is_valid(case_id):
        return None
    update_dict = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_dict:
        existing = await db[COLLECTION].find_one({"_id": ObjectId(case_id)})
        return SupportCaseOut(**existing) if existing else None
    result = await db[COLLECTION].find_one_and_update(
        {"_id": ObjectId(case_id)},
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        return None
    return SupportCaseOut(**result)


async def increment_message_count(case_id: str, attachments_added: int = 0) -> None:
    if not ObjectId.is_valid(case_id):
        return
    await db[COLLECTION].update_one(
        {"_id": ObjectId(case_id)},
        {
            "$inc": {
                "message_count": 1,
                "attachment_count": int(max(attachments_added, 0)),
            },
            "$set": {
                "last_message_at": int(time.time()),
                "last_updated": int(time.time()),
            },
        },
    )


async def count_support_cases(filter_dict: dict) -> int:
    return await db[COLLECTION].count_documents(filter_dict)


async def count_open_cases_for_tenant(tenant_id: str) -> int:
    """Count a tenant's cases that still hold a slot in the 10-open cap.

    Anything that is not CLOSED counts — see schemas.support_case_schema.OPEN_STATUSES.
    """
    return await db[COLLECTION].count_documents(
        {
            "tenant_id": tenant_id,
            "status": {"$ne": SupportCaseStatus.CLOSED.value},
        }
    )


async def list_resolved_stale_cases(
    inactive_since_ts: int, start: int = 0, stop: int = 500
) -> List[SupportCaseOut]:
    """Cases in RESOLVED whose last activity is older than ``inactive_since_ts``.

    Used by the auto-close cron to bulk close resolved cases the tenant never confirmed.
    """
    cursor = (
        db[COLLECTION]
        .find(
            {
                "status": SupportCaseStatus.RESOLVED.value,
                "$or": [
                    {"last_message_at": {"$lte": inactive_since_ts}},
                    {
                        "last_message_at": None,
                        "resolved_at": {"$lte": inactive_since_ts},
                    },
                ],
            }
        )
        .sort("resolved_at", 1)
        .skip(start)
        .limit(max(stop - start, 0))
    )
    return [SupportCaseOut(**doc) async for doc in cursor]


async def list_awaiting_tenant_stale_cases(
    older_than_ts: int, start: int = 0, stop: int = 500
) -> List[SupportCaseOut]:
    """AWAITING_TENANT cases whose last tenant activity is older than ``older_than_ts``."""
    cursor = (
        db[COLLECTION]
        .find(
            {
                "status": SupportCaseStatus.AWAITING_TENANT.value,
                "last_message_at": {"$lte": older_than_ts},
            }
        )
        .sort("last_message_at", 1)
        .skip(start)
        .limit(max(stop - start, 0))
    )
    return [SupportCaseOut(**doc) async for doc in cursor]


async def list_sla_breached_cases(
    now_ts: int, start: int = 0, stop: int = 500
) -> List[SupportCaseOut]:
    """Active cases whose SLA deadline has passed."""
    cursor = (
        db[COLLECTION]
        .find(
            {
                "status": {
                    "$in": [
                        SupportCaseStatus.OPEN.value,
                        SupportCaseStatus.ACKNOWLEDGED.value,
                        SupportCaseStatus.IN_PROGRESS.value,
                        SupportCaseStatus.REOPENED.value,
                    ]
                },
                "sla_due_at": {"$lte": now_ts},
            }
        )
        .sort("sla_due_at", 1)
        .skip(start)
        .limit(max(stop - start, 0))
    )
    return [SupportCaseOut(**doc) async for doc in cursor]
