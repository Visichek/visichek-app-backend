"""CRUD for the queue_job_log audit trail.

Every queued write records an entry here so operators can trace the full
lifecycle of a mutation (queued -> processing -> succeeded/failed) without
reading Celery internals. Entries are keyed by task_id so the route and the
worker can update the same record.
"""

from __future__ import annotations

import time
from typing import Optional

from bson import ObjectId

from core.database import db
from schemas.queue_job_log_schema import (
    QueueJobLogCreate,
    QueueJobLogOut,
    QueueJobLogUpdate,
    QueueJobStatus,
)

COLLECTION = "queue_job_log"


async def insert_job_log(entry: QueueJobLogCreate) -> QueueJobLogOut:
    payload = entry.model_dump()
    result = await db[COLLECTION].insert_one(payload)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return QueueJobLogOut(**doc)


async def get_job_log_by_task_id(task_id: str) -> Optional[QueueJobLogOut]:
    doc = await db[COLLECTION].find_one({"task_id": task_id})
    if not doc:
        return None
    return QueueJobLogOut(**doc)


async def get_job_log_by_id(log_id: str) -> Optional[QueueJobLogOut]:
    if not ObjectId.is_valid(log_id):
        return None
    doc = await db[COLLECTION].find_one({"_id": ObjectId(log_id)})
    if not doc:
        return None
    return QueueJobLogOut(**doc)


async def update_job_log_by_task_id(
    task_id: str, update: QueueJobLogUpdate
) -> Optional[QueueJobLogOut]:
    update_dict = {k: v for k, v in update.model_dump().items() if v is not None}
    update_dict["last_updated"] = int(time.time())
    doc = await db[COLLECTION].find_one_and_update(
        {"task_id": task_id},
        {"$set": update_dict},
        return_document=True,
    )
    if not doc:
        return None
    return QueueJobLogOut(**doc)


async def mark_processing(task_id: str) -> None:
    await db[COLLECTION].update_one(
        {"task_id": task_id},
        {
            "$set": {
                "status": QueueJobStatus.PROCESSING.value,
                "last_updated": int(time.time()),
            }
        },
    )


async def mark_succeeded(task_id: str, result: Optional[dict] = None) -> None:
    update: dict = {
        "status": QueueJobStatus.SUCCEEDED.value,
        "last_updated": int(time.time()),
    }
    if result is not None:
        update["result"] = result
    await db[COLLECTION].update_one({"task_id": task_id}, {"$set": update})


async def mark_failed(task_id: str, error: str) -> None:
    await db[COLLECTION].update_one(
        {"task_id": task_id},
        {
            "$set": {
                "status": QueueJobStatus.FAILED.value,
                "error": error[:2000],
                "last_updated": int(time.time()),
            }
        },
    )


async def list_job_logs_for_tenant(
    tenant_id: str,
    start: int = 0,
    stop: int = 100,
    extra_filter: Optional[dict] = None,
) -> list[QueueJobLogOut]:
    query = {"tenant_id": tenant_id, **(extra_filter or {})}
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("date_created", -1)
        .skip(start)
        .limit(stop - start)
    )
    return [QueueJobLogOut(**doc) async for doc in cursor]


async def count_job_logs_for_tenant(
    tenant_id: str, extra_filter: Optional[dict] = None
) -> int:
    return await db[COLLECTION].count_documents(
        {"tenant_id": tenant_id, **(extra_filter or {})}
    )


async def list_job_logs_for_actor(
    actor_id: str,
    start: int = 0,
    stop: int = 100,
    extra_filter: Optional[dict] = None,
) -> list[QueueJobLogOut]:
    query = {"actor_id": actor_id, **(extra_filter or {})}
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("date_created", -1)
        .skip(start)
        .limit(stop - start)
    )
    return [QueueJobLogOut(**doc) async for doc in cursor]


async def count_job_logs_for_actor(
    actor_id: str, extra_filter: Optional[dict] = None
) -> int:
    return await db[COLLECTION].count_documents(
        {"actor_id": actor_id, **(extra_filter or {})}
    )
