"""CRUD for provider-agnostic checkout sessions."""

from __future__ import annotations

from typing import List, Optional

from bson import ObjectId
from pymongo import ReturnDocument

from core.database import db
from schemas.checkout_schema import (
    CheckoutSessionCreate,
    CheckoutSessionOut,
    CheckoutSessionUpdate,
    CheckoutStatus,
)

COLLECTION = "checkout_sessions"


async def create_checkout(data: CheckoutSessionCreate) -> CheckoutSessionOut:
    payload = data.model_dump(mode="json")
    result = await db[COLLECTION].insert_one(payload)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    assert doc is not None
    return CheckoutSessionOut(**doc)


async def get_checkout(filter_dict: dict) -> Optional[CheckoutSessionOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    if not doc:
        return None
    return CheckoutSessionOut(**doc)


async def get_checkout_by_id(checkout_id: str) -> Optional[CheckoutSessionOut]:
    if not ObjectId.is_valid(checkout_id):
        return None
    return await get_checkout({"_id": ObjectId(checkout_id)})


async def get_checkout_by_reference(reference: str) -> Optional[CheckoutSessionOut]:
    return await get_checkout({"provider_reference": reference})


async def list_checkouts_for_tenant(
    tenant_id: str,
    status: Optional[CheckoutStatus] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[CheckoutSessionOut]:
    filter_dict: dict = {"tenant_id": tenant_id}
    if status is not None:
        filter_dict["status"] = status.value
    cursor = (
        db[COLLECTION]
        .find(filter_dict)
        .sort("date_created", -1)
        .skip(skip)
        .limit(limit)
    )
    return [CheckoutSessionOut(**doc) async for doc in cursor]


async def count_checkouts_for_tenant(
    tenant_id: str,
    status: Optional[CheckoutStatus] = None,
) -> int:
    filter_dict: dict = {"tenant_id": tenant_id}
    if status is not None:
        filter_dict["status"] = status.value
    return await db[COLLECTION].count_documents(filter_dict)


async def update_checkout(
    checkout_id: str, update_data: CheckoutSessionUpdate
) -> Optional[CheckoutSessionOut]:
    if not ObjectId.is_valid(checkout_id):
        return None
    update_dict = {
        k: v for k, v in update_data.model_dump(mode="json").items() if v is not None
    }
    if not update_dict:
        return await get_checkout_by_id(checkout_id)
    doc = await db[COLLECTION].find_one_and_update(
        {"_id": ObjectId(checkout_id)},
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if not doc:
        return None
    return CheckoutSessionOut(**doc)
