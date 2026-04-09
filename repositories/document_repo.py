from __future__ import annotations

from bson import ObjectId

from core.database import db
from schemas.document_schema import DocumentCreate, DocumentOut


async def create_document(document: DocumentCreate) -> DocumentOut:
    payload = document.model_dump()
    result = await db.documents.insert_one(payload)
    stored = await db.documents.find_one({"_id": result.inserted_id})
    return DocumentOut(**stored)


async def get_document_by_id(document_id: str) -> DocumentOut | None:
    if not ObjectId.is_valid(document_id):
        return None
    row = await db.documents.find_one({"_id": ObjectId(document_id)})
    if row is None:
        return None
    return DocumentOut(**row)


async def get_document_by_key(object_key: str) -> DocumentOut | None:
    row = await db.documents.find_one({"object_key": object_key})
    if row is None:
        return None
    return DocumentOut(**row)


async def delete_document(document_id: str) -> bool:
    if not ObjectId.is_valid(document_id):
        return False
    result = await db.documents.delete_one({"_id": ObjectId(document_id)})
    return bool(result.deleted_count)


async def count_documents_for_tenant(tenant_id: str) -> int:
    return await db.documents.count_documents({"tenant_id": tenant_id})


async def sum_document_bytes_for_tenant(tenant_id: str) -> int:
    """Total stored byte count for all documents owned by the tenant."""
    pipeline = [
        {"$match": {"tenant_id": tenant_id}},
        {"$group": {"_id": None, "total": {"$sum": "$size"}}},
    ]
    cursor = db.documents.aggregate(pipeline)
    async for row in cursor:
        return int(row.get("total", 0) or 0)
    return 0
