from __future__ import annotations

import hashlib
import time
from pathlib import Path
from uuid import uuid4

from core.errors import AppException, ErrorCode, resource_not_found
from core.storage import DocumentMetadata, DocumentStorageManager
from core.storage.types import StorageBackend
from repositories.document_repo import (
    count_documents_for_tenant,
    create_document,
    delete_document,
    get_document_by_id,
    sum_document_bytes_for_tenant,
)
from schemas.document_schema import (
    CompleteUploadRequest,
    DocumentCreate,
    DocumentOut,
    DocumentWithSummaryOut,
    UploadIntentRequest,
)
from services.plan_limits import enforce_storage_limits


def _epoch() -> int:
    return int(time.time())


async def _enforce_tenant_storage_limits(tenant_id: str | None, new_file_bytes: int) -> None:
    """Check the tenant's plan storage limits before creating a document.

    App admins / users without a tenant bypass plan storage checks.
    """
    if not tenant_id:
        return
    current_count = await count_documents_for_tenant(tenant_id)
    current_bytes = await sum_document_bytes_for_tenant(tenant_id)
    await enforce_storage_limits(
        tenant_id=tenant_id,
        current_document_count=current_count,
        current_total_bytes=current_bytes,
        new_file_bytes=new_file_bytes,
    )


async def direct_upload(
    *,
    owner_id: str,
    file_name: str,
    mime_type: str,
    payload: bytes,
    tenant_id: str | None = None,
) -> DocumentOut:
    size = len(payload)
    if size > 50 * 1024 * 1024:
        raise AppException(
            status_code=413,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="File too large",
            details={"max_size_bytes": 50 * 1024 * 1024},
        )

    await _enforce_tenant_storage_limits(tenant_id, size)

    extension = Path(file_name).suffix
    object_key = f"{uuid4().hex}{extension}"
    checksum = hashlib.md5(payload).hexdigest()

    provider = DocumentStorageManager.get_instance().provider
    provider.upload_bytes(object_key=object_key, payload=payload, mime_type=mime_type)

    backend = StorageBackend(provider.backend_name).value

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
            created_at=_epoch(),
            updated_at=_epoch(),
        )
    )


async def create_upload_intent(
    *,
    owner_id: str,
    payload: UploadIntentRequest,
    tenant_id: str | None = None,
):
    # Enforce storage limits up-front so clients don't upload bytes
    # that will be rejected on the complete step.
    await _enforce_tenant_storage_limits(tenant_id, payload.size)

    metadata = DocumentMetadata(
        owner_id=owner_id,
        file_name=payload.file_name,
        mime_type=payload.mime_type,
        size=payload.size,
    )
    provider = DocumentStorageManager.get_instance().provider
    return provider.create_upload_intent(metadata=metadata)


async def complete_upload(
    *,
    owner_id: str,
    payload: CompleteUploadRequest,
    tenant_id: str | None = None,
) -> DocumentOut:
    if payload.size > 50 * 1024 * 1024:
        raise AppException(
            status_code=413,
            code=ErrorCode.DOCUMENT_UPLOAD_INVALID,
            message="File too large",
            details={"max_size_bytes": 50 * 1024 * 1024},
        )

    await _enforce_tenant_storage_limits(tenant_id, payload.size)

    metadata = DocumentMetadata(
        owner_id=owner_id,
        file_name=payload.file_name,
        mime_type=payload.mime_type,
        size=payload.size,
    )
    provider = DocumentStorageManager.get_instance().provider
    stored = provider.complete_upload(
        object_key=payload.object_key,
        metadata=metadata,
        checksum=payload.checksum,
    )

    return await create_document(
        DocumentCreate(
            owner_id=owner_id,
            tenant_id=tenant_id,
            file_name=payload.file_name,
            object_key=stored.object_key,
            backend=stored.backend.value,
            mime_type=stored.mime_type,
            size=stored.size,
            checksum=stored.checksum,
            metadata=payload.model_dump(),
            created_at=_epoch(),
            updated_at=_epoch(),
        )
    )


async def fetch_document(document_id: str) -> tuple[DocumentOut, str]:
    doc = await get_document_by_id(document_id=document_id)
    if doc is None:
        raise resource_not_found("Document", document_id)

    provider = DocumentStorageManager.get_instance().provider
    return doc, provider.download_url(object_key=doc.object_key)


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
