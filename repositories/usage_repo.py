from __future__ import annotations

import time
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.usage_schema import (
    UsageRecordCreate,
    UsageRecordOut,
    UsageAggregateOut,
)


RECORDS_COLLECTION = "usage_records"
AGGREGATES_COLLECTION = "usage_aggregates"


# --- Usage Records (raw event log) ---

async def create_usage_record(record_data: UsageRecordCreate) -> UsageRecordOut:
    record_dict = record_data.model_dump(mode="json")
    result = await db[RECORDS_COLLECTION].insert_one(record_dict)
    result = await db[RECORDS_COLLECTION].find_one({"_id": result.inserted_id})
    return UsageRecordOut(**result)


async def get_usage_records(filter_dict: dict = {}, start: int = 0, stop: int = 100) -> List[UsageRecordOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = db[RECORDS_COLLECTION].find(filter_dict).skip(start).limit(stop - start)
        records = []
        async for doc in cursor:
            records.append(UsageRecordOut(**doc))
        return records
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching usage records: {str(e)}",
        )


async def count_usage_records(filter_dict: dict) -> int:
    """Count documents matching the filter."""
    return await db[RECORDS_COLLECTION].count_documents(filter_dict)


async def delete_usage_records(filter_dict: dict):
    """Bulk delete usage records (for retention/cleanup)."""
    return await db[RECORDS_COLLECTION].delete_many(filter_dict)


# --- Usage Aggregates (pre-computed counters for fast quota checks) ---

async def get_usage_aggregate(filter_dict: dict) -> Optional[UsageAggregateOut]:
    try:
        result = await db[AGGREGATES_COLLECTION].find_one(filter_dict)
        if result is None:
            return None
        return UsageAggregateOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching usage aggregate: {str(e)}",
        )


async def get_usage_aggregates(filter_dict: dict = {}, start: int = 0, stop: int = 100) -> List[UsageAggregateOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = db[AGGREGATES_COLLECTION].find(filter_dict).skip(start).limit(stop - start)
        aggregates = []
        async for doc in cursor:
            aggregates.append(UsageAggregateOut(**doc))
        return aggregates
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching usage aggregates: {str(e)}",
        )


async def increment_usage_aggregate(
    tenant_id: str,
    subscription_id: str,
    collection: str,
    operation: str,
    period_key: str,
) -> UsageAggregateOut:
    """Atomically increment or create usage aggregate counter.
    Uses upsert so it's always a single atomic operation.
    """
    from pymongo import ReturnDocument

    filter_dict = {
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
        "collection": collection,
        "operation": operation,
        "period_key": period_key,
    }
    now = int(time.time())
    result = await db[AGGREGATES_COLLECTION].find_one_and_update(
        filter_dict,
        {
            "$inc": {"count": 1},
            "$set": {"last_updated": now},
            "$setOnInsert": {"date_created": now},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return UsageAggregateOut(**result)


async def get_current_count(
    tenant_id: str,
    subscription_id: str,
    collection: str,
    operation: str,
    period_key: str,
) -> int:
    """Get current usage count for quota checking."""
    agg = await get_usage_aggregate({
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
        "collection": collection,
        "operation": operation,
        "period_key": period_key,
    })
    return agg.count if agg else 0


async def reset_usage_aggregates(filter_dict: dict):
    """Reset aggregate counters (set count to 0) for matching documents."""
    return await db[AGGREGATES_COLLECTION].update_many(
        filter_dict,
        {"$set": {"count": 0, "last_updated": int(time.time())}},
    )


async def delete_usage_aggregates(filter_dict: dict):
    """Bulk delete usage aggregates (for retention/cleanup)."""
    return await db[AGGREGATES_COLLECTION].delete_many(filter_dict)
