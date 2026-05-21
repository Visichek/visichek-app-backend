from __future__ import annotations

import hashlib
import time
from pathlib import Path
from uuid import uuid4

from core.errors import AppException, ErrorCode, resource_not_found
from core.storage import DocumentStorageManager
from core.storage.types import StorageBackend
from repositories.document_repo import (
    create_document,
    delete_document,
    get_document_by_id,
)
from schemas.document_schema import DocumentCreate, DocumentOut, DocumentWithSummaryOut
from services.storage_quota_service import enforce_storage_quota
from services.storage_url_service import resolve_download_url

# CLIENT uploads are presigned-only — see services/upload_service.py and
# api/v1/upload_route.py. This module is read/delete/enrich, plus the
# server-side persistence helper below for flows where the SERVER already
# holds the bytes (e.g. OCR / face-crop verification) and there is no client
# to presign for.

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


async def persist_server_document(
    *,
    owner_id: str,
    file_name: str,
    mime_type: str,
    payload: bytes,
    tenant_id: str | None = None,
) -> DocumentOut:
    """Persist SERVER-HELD bytes as a ready Document row.

    NOT a client upload path — reserved for pipelines that must read the bytes
    on the server (ID-document OCR, cropped-portrait storage). Client uploads
    must use the presigned ``/v1/uploads/*`` flow instead. Enforces the same
    50 MB cap and tenant storage quota as the presigned confirm step.
    """
    size = len(payload)
    if size <= 0:
        raise AppException(
            status_code=400,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="Empty file",
        )
    if size > _MAX_UPLOAD_BYTES:
        raise AppException(
            status_code=413,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="File too large",
            details={"max_size_bytes": _MAX_UPLOAD_BYTES},
        )

    await enforce_storage_quota(tenant_id, size)

    extension = Path(file_name).suffix
    object_key = f"server/{tenant_id or 'shared'}/{uuid4().hex}{extension}"
    checksum = hashlib.md5(payload).hexdigest()

    provider = DocumentStorageManager.get_instance().provider
    provider.upload_bytes(object_key=object_key, payload=payload, mime_type=mime_type)
    backend = StorageBackend(provider.backend_name).value

    now = int(time.time())
    return await create_document(
        DocumentCreate(
            owner_id=owner_id,
            tenant_id=tenant_id,
            file_name=file_name,
            object_key=object_key,
            backend=backend,
            mime_type=mime_type,
            size=size,
            checksum=checksum,
            status="ready",
            created_at=now,
            updated_at=now,
        )
    )


async def fetch_document(document_id: str) -> tuple[DocumentOut, str]:
    doc = await get_document_by_id(document_id=document_id)
    if doc is None:
        raise resource_not_found("Document", document_id)
    return doc, resolve_download_url(doc.object_key)


async def _enrich_document(doc: DocumentOut) -> DocumentWithSummaryOut:
    from services.summary_resolver import resolve_user_summary

    owner_summary = await resolve_user_summary(doc.owner_id)
    data = doc.model_dump(by_alias=False)
    data["owner_summary"] = owner_summary
    return DocumentWithSummaryOut(**data)


async def fetch_document_with_summary(
    document_id: str,
) -> tuple[DocumentWithSummaryOut, str]:
    doc, download_url = await fetch_document(document_id=document_id)
    enriched = await _enrich_document(doc)
    return enriched, download_url


async def remove_document(document_id: str) -> bool:
    doc = await get_document_by_id(document_id=document_id)
    if doc is None:
        raise resource_not_found("Document", document_id)

    provider = DocumentStorageManager.get_instance().provider
    provider.delete_object(object_key=doc.object_key)
    deleted = await delete_document(document_id=document_id)
    if not deleted:
        raise resource_not_found("Document", document_id)
    return True
