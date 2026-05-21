from __future__ import annotations

import time

from bson import ObjectId

from core.database import db
from schemas.document_schema import DocumentCreate, DocumentOut

# Documents whose bytes have not yet been confirmed in storage. They do NOT
# count toward storage quota (size is 0 until confirm) and are swept by the
# pending-upload cleanup job if never confirmed.
PENDING_STATUS = "pending"
READY_STATUS = "ready"


async def create_document(document: DocumentCreate) -> DocumentOut:
    payload = document.model_dump()
    result = await db.documents.insert_one(payload)
    stored = await db.documents.find_one({"_id": result.inserted_id})
    return DocumentOut(**stored)


async def mark_document_ready(
    *,
    object_key: str,
    size: int,
    mime_type: str,
    checksum: str | None = None,
) -> DocumentOut | None:
    """Flip a pending document to ready with the authoritative size/type read
    back from storage at confirm time. Returns the updated row, or None if no
    document carries that object_key."""
    row = await db.documents.find_one_and_update(
        {"object_key": object_key},
        {
            "$set": {
                "status": READY_STATUS,
                "size": size,
                "mime_type": mime_type,
                "checksum": checksum,
                "updated_at": int(time.time()),
            }
        },
        return_document=True,
    )
    if row is None:
        return None
    return DocumentOut(**row)


async def list_stale_pending_documents(older_than_ts: int) -> list[DocumentOut]:
    """Pending documents created before ``older_than_ts`` (intent never
    confirmed). Used by the cleanup job to purge orphaned objects + rows."""
    cursor = db.documents.find(
        {"status": PENDING_STATUS, "created_at": {"$lt": older_than_ts}}
    )
    return [DocumentOut(**row) async for row in cursor]


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
    # Pending (unconfirmed) uploads don't consume the document-count cap.
    return await db.documents.count_documents(
        {"tenant_id": tenant_id, "status": {"$ne": PENDING_STATUS}}
    )


async def sum_document_bytes_for_tenant(tenant_id: str) -> int:
    """Total stored byte count for all committed documents owned by the tenant.

    Pending uploads are excluded — their bytes aren't in storage yet (size is
    0 until confirm), so they never inflate quota usage."""
    pipeline = [
        {"$match": {"tenant_id": tenant_id, "status": {"$ne": PENDING_STATUS}}},
        {"$group": {"_id": None, "total": {"$sum": "$size"}}},
    ]
    cursor = db.documents.aggregate(pipeline)
    async for row in cursor:
        return int(row.get("total", 0) or 0)
    return 0
