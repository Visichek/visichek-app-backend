"""Legal document repository — pure Mongo access, no business logic.

Two collections:

* ``legal_documents``          — one head record per ``slug`` (editable
  working copy + currently-live ``published_body`` + lifecycle metadata).
* ``legal_document_versions``  — append-only immutable snapshots written
  on every publish (the legal audit history).

Inserts accept ``preassigned_id`` so the queued-write pipeline can route
mutations through ``enqueue_write`` with a stable pre-generated id.
"""

from __future__ import annotations

from typing import Any, List, Optional

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo import ReturnDocument

from core.database import db
from legal.schemas.legal_document_schema import (
    LegalDocumentCreate,
    LegalDocumentListRow,
    LegalDocumentOut,
    LegalDocumentVersionOut,
)

COLLECTION = "legal_documents"
VERSIONS_COLLECTION = "legal_document_versions"


# ---------------------------------------------------------------------------
# Head records
# ---------------------------------------------------------------------------


async def create_legal_document(
    data: LegalDocumentCreate, *, preassigned_id: Optional[str] = None
) -> LegalDocumentOut:
    doc = data.model_dump()
    if preassigned_id:
        try:
            doc["_id"] = ObjectId(preassigned_id)
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Invalid preassigned legal document id: {exc}",
            )
    result = await db[COLLECTION].insert_one(doc)
    saved = await db[COLLECTION].find_one({"_id": result.inserted_id})
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal document insert succeeded but doc not found",
        )
    return LegalDocumentOut(**saved)


async def get_legal_document(filter_dict: dict) -> Optional[LegalDocumentOut]:
    found = await db[COLLECTION].find_one(filter_dict)
    if found is None:
        return None
    return LegalDocumentOut(**found)


async def get_legal_document_by_id(document_id: str) -> Optional[LegalDocumentOut]:
    if not ObjectId.is_valid(document_id):
        return None
    return await get_legal_document({"_id": ObjectId(document_id)})


async def get_legal_document_by_slug(slug: str) -> Optional[LegalDocumentOut]:
    return await get_legal_document({"slug": slug})


async def slug_exists(slug: str, *, exclude_id: Optional[str] = None) -> bool:
    query: dict[str, Any] = {"slug": slug}
    if exclude_id and ObjectId.is_valid(exclude_id):
        query["_id"] = {"$ne": ObjectId(exclude_id)}
    return await db[COLLECTION].count_documents(query, limit=1) > 0


async def list_legal_documents(
    filter_dict: Optional[dict] = None,
    start: int = 0,
    stop: int = 100,
    sort_field: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> List[LegalDocumentListRow]:
    filter_dict = filter_dict or {}
    cursor = db[COLLECTION].find(filter_dict)
    if sort_field and sort_order:
        cursor = cursor.sort(sort_field, sort_order)
    else:
        cursor = cursor.sort("date_created", -1)
    cursor = cursor.skip(start).limit(max(stop - start, 0))
    rows: List[LegalDocumentListRow] = []
    async for found in cursor:
        rows.append(LegalDocumentListRow(**found))
    return rows


async def count_legal_documents(filter_dict: Optional[dict] = None) -> int:
    return await db[COLLECTION].count_documents(filter_dict or {})


async def update_legal_document(
    filter_dict: dict, set_fields: dict
) -> Optional[LegalDocumentOut]:
    """Apply a ``$set`` to a head record and return the updated doc."""
    result = await db[COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": set_fields},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return LegalDocumentOut(**result)


async def delete_legal_document(filter_dict: dict) -> int:
    res = await db[COLLECTION].delete_one(filter_dict)
    return res.deleted_count


# ---------------------------------------------------------------------------
# Immutable version snapshots
# ---------------------------------------------------------------------------


async def get_max_version(slug: str) -> int:
    """Highest existing version number for a slug (0 if none published yet)."""
    latest = await db[VERSIONS_COLLECTION].find_one(
        {"slug": slug}, sort=[("version", -1)]
    )
    if not latest:
        return 0
    return int(latest.get("version", 0) or 0)


async def insert_version(version_doc: dict) -> LegalDocumentVersionOut:
    result = await db[VERSIONS_COLLECTION].insert_one(version_doc)
    saved = await db[VERSIONS_COLLECTION].find_one({"_id": result.inserted_id})
    if saved is None:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Version insert succeeded but doc not found",
        )
    return LegalDocumentVersionOut(**saved)


async def list_versions(
    document_id: str, *, skip: int = 0, limit: int = 100
) -> List[LegalDocumentVersionOut]:
    cursor = (
        db[VERSIONS_COLLECTION]
        .find({"document_id": document_id})
        .sort("version", -1)
        .skip(skip)
        .limit(limit)
    )
    out: List[LegalDocumentVersionOut] = []
    async for found in cursor:
        out.append(LegalDocumentVersionOut(**found))
    return out


async def count_versions(document_id: str) -> int:
    return await db[VERSIONS_COLLECTION].count_documents({"document_id": document_id})


async def get_version(
    document_id: str, version: int
) -> Optional[LegalDocumentVersionOut]:
    found = await db[VERSIONS_COLLECTION].find_one(
        {"document_id": document_id, "version": version}
    )
    if found is None:
        return None
    return LegalDocumentVersionOut(**found)
