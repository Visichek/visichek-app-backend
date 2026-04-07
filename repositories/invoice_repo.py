from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId

from core.database import db
from schemas.invoice_schema import InvoiceCreate, InvoiceOut, InvoiceUpdate

logger = logging.getLogger(__name__)

COLLECTION = "invoices"
COUNTER_COLLECTION = "invoice_counters"


async def create_invoice(schema: InvoiceCreate) -> InvoiceOut:
    data = schema.model_dump()
    result = await db[COLLECTION].insert_one(data)
    data["_id"] = result.inserted_id
    return InvoiceOut(**data)


async def get_invoice(filter_dict: dict[str, Any]) -> InvoiceOut | None:
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return InvoiceOut(**doc)


async def get_invoices(
    filter_dict: dict[str, Any] | None = None,
    skip: int = 0,
    limit: int = 20,
    sort_field: str = "date_created",
    sort_direction: int = -1,
) -> list[InvoiceOut]:
    filter_dict = filter_dict or {}
    cursor = (
        db[COLLECTION]
        .find(filter_dict)
        .sort(sort_field, sort_direction)
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [InvoiceOut(**doc) for doc in docs]


async def count_invoices(filter_dict: dict[str, Any] | None = None) -> int:
    return await db[COLLECTION].count_documents(filter_dict or {})


async def update_invoice(document_id: str, data: InvoiceUpdate) -> InvoiceOut | None:
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    if not update_data:
        return await get_invoice({"_id": ObjectId(document_id)})
    result = await db[COLLECTION].find_one_and_update(
        {"_id": ObjectId(document_id)},
        {"$set": update_data},
        return_document=True,
    )
    if result is None:
        return None
    return InvoiceOut(**result)


async def get_next_invoice_number(year: int) -> str:
    """Atomically increment and return the next invoice number for a given year."""
    result = await db[COUNTER_COLLECTION].find_one_and_update(
        {"year": year},
        {"$inc": {"sequence": 1}},
        upsert=True,
        return_document=True,
    )
    seq = result["sequence"]
    return f"INV-{year}-{seq:06d}"
