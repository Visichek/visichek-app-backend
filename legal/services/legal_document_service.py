"""Legal document business logic.

Sits between the routes/writers and the repository. Owns:

* slug de-duplication on create / rename,
* the publish lifecycle (immutable version snapshot + head promotion),
* archive,
* storing the original uploaded file privately and resolving its presigned
  download URL on read.

Reads resolve ``source_file_url`` (presigned) best-effort so a missing /
unconfigured storage backend never breaks a list or detail response.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import PurePosixPath
from typing import Any, List, Optional

from fastapi import HTTPException, status

from core.errors import resource_not_found
from core.storage.manager import DocumentStorageManager
from legal.repositories import legal_document_repo as repo
from legal.schemas.imports import LegalDocStatus, excerpt_from_blocks
from legal.schemas.legal_document_schema import (
    LegalDocumentCreate,
    LegalDocumentListRow,
    LegalDocumentOut,
    LegalDocumentPublishRequest,
    LegalDocumentUpdate,
    LegalDocumentVersionOut,
    SourceFile,
)
from services.storage_url_service import try_resolve_download_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source-file storage (presigned)
# ---------------------------------------------------------------------------


def _ext_for(filename: str) -> str:
    suffix = PurePosixPath(filename or "").suffix.lower()
    return suffix if suffix else ""


async def store_source_file(
    *, file_bytes: bytes, filename: str, mime_type: str, slug: str
) -> SourceFile:
    """Persist the original upload privately and return its descriptor.

    Bytes go through ``DocumentStorageManager`` (S3 presigned in prod, local
    read-endpoint in dev) under a ``legal/{slug}/{uuid}{ext}`` object key.
    The presigned download URL is minted on read, never stored.
    """
    object_key = f"legal/{slug or 'document'}/{uuid.uuid4().hex}{_ext_for(filename)}"
    provider = DocumentStorageManager.get_instance().provider
    await asyncio.to_thread(
        provider.upload_bytes,
        object_key=object_key,
        payload=file_bytes,
        mime_type=mime_type or "application/octet-stream",
    )
    return SourceFile(
        object_key=object_key,
        file_name=filename or "document",
        mime_type=mime_type or "application/octet-stream",
        size=len(file_bytes),
    )


def _attach_source_url(doc: LegalDocumentOut) -> LegalDocumentOut:
    if doc.source_file and doc.source_file.object_key:
        doc.source_file_url = try_resolve_download_url(doc.source_file.object_key)
    return doc


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


async def _unique_slug(base_slug: str, *, exclude_id: Optional[str] = None) -> str:
    """Return ``base_slug`` or ``base_slug-2``/``-3``… if already taken."""
    candidate = base_slug
    n = 1
    while await repo.slug_exists(candidate, exclude_id=exclude_id):
        n += 1
        candidate = f"{base_slug}-{n}"
    return candidate


# ---------------------------------------------------------------------------
# Create / read / update / delete
# ---------------------------------------------------------------------------


async def add_legal_document(
    data: LegalDocumentCreate, *, preassigned_id: Optional[str] = None
) -> LegalDocumentOut:
    data.slug = await _unique_slug(data.slug or "untitled-document")
    created = await repo.create_legal_document(data, preassigned_id=preassigned_id)
    return _attach_source_url(created)


async def retrieve_legal_document_by_id(document_id: str) -> LegalDocumentOut:
    doc = await repo.get_legal_document_by_id(document_id)
    if doc is None:
        raise resource_not_found(resource="LegalDocument", resource_id=document_id)
    return _attach_source_url(doc)


async def retrieve_legal_document_by_slug(slug: str) -> Optional[LegalDocumentOut]:
    doc = await repo.get_legal_document_by_slug(slug)
    if doc is None:
        return None
    return _attach_source_url(doc)


async def retrieve_legal_documents(
    filters: Optional[dict] = None,
    start: int = 0,
    stop: int = 100,
    sort_field: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> List[LegalDocumentListRow]:
    return await repo.list_legal_documents(
        filter_dict=filters,
        start=start,
        stop=stop,
        sort_field=sort_field,
        sort_order=sort_order,
    )


async def update_legal_document_by_id(
    document_id: str, data: LegalDocumentUpdate
) -> LegalDocumentOut:
    existing = await repo.get_legal_document_by_id(document_id)
    if existing is None:
        raise resource_not_found(resource="LegalDocument", resource_id=document_id)

    set_fields: dict[str, Any] = data.model_dump(exclude_none=True)

    # Rename: keep slug unique.
    if data.slug and data.slug != existing.slug:
        set_fields["slug"] = await _unique_slug(data.slug, exclude_id=document_id)

    # Editing the body when already published means there are now changes
    # that the public site won't see until the next publish.
    if "body" in set_fields and existing.status == LegalDocStatus.published:
        set_fields["has_unpublished_changes"] = True

    # Keep summary fresh when the body changes and no explicit summary given.
    if "body" in set_fields and not data.summary:
        derived = excerpt_from_blocks(set_fields["body"])
        if derived:
            set_fields["summary"] = derived

    updated = await repo.update_legal_document({"_id": _oid(document_id)}, set_fields)
    if updated is None:
        raise resource_not_found(resource="LegalDocument", resource_id=document_id)
    return _attach_source_url(updated)


async def remove_legal_document(document_id: str) -> dict:
    existing = await repo.get_legal_document_by_id(document_id)
    if existing is None:
        raise resource_not_found(resource="LegalDocument", resource_id=document_id)
    deleted = await repo.delete_legal_document({"_id": _oid(document_id)})
    return {"id": document_id, "deleted": bool(deleted)}


# ---------------------------------------------------------------------------
# Lifecycle: publish / archive
# ---------------------------------------------------------------------------


async def publish_legal_document(
    document_id: str,
    req: LegalDocumentPublishRequest,
    *,
    actor_id: Optional[str] = None,
) -> LegalDocumentOut:
    """Snapshot the working ``body`` as a new immutable version and go live."""
    doc = await repo.get_legal_document_by_id(document_id)
    if doc is None:
        raise resource_not_found(resource="LegalDocument", resource_id=document_id)
    if not doc.body:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot publish a document with an empty body.",
        )

    now = int(time.time())
    effective_at = req.effective_at or now
    next_version = await repo.get_max_version(doc.slug or "") + 1

    version_doc = {
        "document_id": document_id,
        "slug": doc.slug,
        "title": doc.title,
        "doc_type": doc.doc_type.value if doc.doc_type else None,
        "version": next_version,
        "body": doc.body,
        "effective_at": effective_at,
        "published_at": now,
        "published_by": actor_id,
        "change_note": req.change_note,
        "source_file": doc.source_file.model_dump() if doc.source_file else None,
        "date_created": now,
    }
    await repo.insert_version(version_doc)

    updated = await repo.update_legal_document(
        {"_id": _oid(document_id)},
        {
            "status": LegalDocStatus.published.value,
            "current_version": next_version,
            "published_body": doc.body,
            "published_at": now,
            "effective_at": effective_at,
            "has_unpublished_changes": False,
            "last_updated": now,
        },
    )
    if updated is None:  # pragma: no cover - defensive
        raise resource_not_found(resource="LegalDocument", resource_id=document_id)
    return _attach_source_url(updated)


async def archive_legal_document(document_id: str) -> LegalDocumentOut:
    doc = await repo.get_legal_document_by_id(document_id)
    if doc is None:
        raise resource_not_found(resource="LegalDocument", resource_id=document_id)
    updated = await repo.update_legal_document(
        {"_id": _oid(document_id)},
        {"status": LegalDocStatus.archived.value, "last_updated": int(time.time())},
    )
    if updated is None:  # pragma: no cover - defensive
        raise resource_not_found(resource="LegalDocument", resource_id=document_id)
    return _attach_source_url(updated)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


async def retrieve_versions(
    document_id: str, *, skip: int = 0, limit: int = 100
) -> List[LegalDocumentVersionOut]:
    return await repo.list_versions(document_id, skip=skip, limit=limit)


async def retrieve_version(document_id: str, version: int) -> LegalDocumentVersionOut:
    snap = await repo.get_version(document_id, version)
    if snap is None:
        raise resource_not_found(
            resource="LegalDocumentVersion",
            resource_id=f"{document_id}:v{version}",
        )
    return snap


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _oid(document_id: str):
    from bson import ObjectId

    if not ObjectId.is_valid(document_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid legal document id format",
        )
    return ObjectId(document_id)
