from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import uuid4

from bson import ObjectId

from core.errors import AppException, ErrorCode
from repositories.id_verification_hash_repo import (
    create_hash,
    find_by_hash,
)
from repositories.visitor_repo import (
    create_visitor,
    find_visitor_by_email_or_phone_any,
    get_visitor,
    list_visitors_for_tenant,
    update_visitor,
)
from schemas.id_extraction_schema import (
    IDExtractionProvider,
    IDExtractionRequest,
)
from schemas.id_verification_hash_schema import IDVerificationHashCreate
from schemas.imports import IDType
from schemas.visitor_schema import VisitorCreate, VisitorOut, VisitorUpdate
from services.document_service import direct_upload
from services.face_crop_service import crop_face
from services.id_extraction_service import extract_id

logger = logging.getLogger(__name__)


async def verify_visitor_from_id(
    *,
    tenant_id: str,
    file_bytes: bytes,
    mime_type: str,
    id_type: IDType,
    email: str,
    phone: str,
) -> VisitorOut:
    """End-to-end verification flow.

    1. Validates input (tenant_id, email/phone, file size).
    2. Hashes file bytes; if (tenant_id, sha256) seen before, returns the mapped
       visitor without re-running OCR or face extraction.
    3. Uploads the original file via document_service.direct_upload.
    4. Runs Document AI OCR on the stored document (id_extraction_service).
    5. Crops the face via face_crop_service.
    6. Uploads the cropped face as a separate object; stores its presigned URL.
    7. Upserts a visitor matched by email OR phone (first match wins).
       Verified=True, verification_method=id_type.
    8. Records the (tenant_id, sha256) -> visitor_id mapping for idempotency.
    """
    if not ObjectId.is_valid(tenant_id):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Invalid tenant ID",
        )

    if not email or not phone:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Both email and phone are required",
        )

    if not file_bytes:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Empty file",
        )

    sha256 = hashlib.sha256(file_bytes).hexdigest()

    existing_hash = await find_by_hash(tenant_id=tenant_id, sha256=sha256)
    if existing_hash is not None:
        visitor = await get_visitor(
            {"_id": existing_hash.visitor_id, "tenant_id": tenant_id}
        )
        if visitor is not None:
            logger.info(
                "Returning cached verification for tenant=%s sha256=%s",
                tenant_id,
                sha256[:12],
            )
            return visitor
        # Hash exists but visitor was deleted — fall through and re-process.
        logger.warning(
            "Hash %s mapped to missing visitor %s; re-processing",
            sha256[:12],
            existing_hash.visitor_id,
        )

    # 1. Upload original document to storage
    extension = _extension_for_mime(mime_type)
    file_name = f"id-{uuid4().hex}{extension}"
    document = await direct_upload(
        owner_id=f"public:{tenant_id}",
        file_name=file_name,
        mime_type=mime_type,
        payload=file_bytes,
        tenant_id=tenant_id,
    )

    # 2. OCR via Document AI (reuses existing id_extraction_service)
    try:
        extraction = await extract_id(
            IDExtractionRequest(
                document_id=document.id or "",
                id_type=id_type,
                provider=IDExtractionProvider.GOOGLE_DOCUMENT_AI,
            ),
            tenant_id=tenant_id,
        )
    except AppException:
        raise
    except Exception as exc:
        logger.exception("OCR failed for tenant=%s", tenant_id)
        raise AppException(
            status_code=502,
            code=ErrorCode.INTERNAL_ERROR,
            message=f"OCR extraction failed: {exc}",
        ) from exc

    # 3. Face crop (fails the whole call if no face is found — no half-verified visitors)
    cropped_bytes = await crop_face(file_bytes)

    # 4. Upload cropped face
    portrait_url = await _upload_portrait(tenant_id=tenant_id, image_bytes=cropped_bytes)

    # 5. Derive visitor fields from OCR
    bio_data = dict(extraction.extracted_fields)
    full_name = str(bio_data.get("full_name") or "Unknown")
    id_number_encrypted = bio_data.get("id_number_encrypted")

    # 6. Upsert visitor
    existing_visitor = await find_visitor_by_email_or_phone_any(
        tenant_id=tenant_id, email=email, phone=phone
    )

    if existing_visitor is not None:
        # Never downgrade verified. Always update portrait, id_document, method, fields.
        update_payload = VisitorUpdate(
            full_name=full_name,
            bio_data=bio_data,
            verified=True,
            verification_method=id_type,
            id_number_encrypted=id_number_encrypted,
            id_document_id=document.id,
            portrait_url=portrait_url,
        )
        # Update contact fields only if missing on the existing record
        if not existing_visitor.email and email:
            update_payload.email = email
        if not existing_visitor.phone and phone:
            update_payload.phone = phone
        assert existing_visitor.id is not None
        visitor = await update_visitor(existing_visitor.id, update_payload)
    else:
        visitor = await create_visitor(
            VisitorCreate(
                tenant_id=tenant_id,
                full_name=full_name,
                email=email,
                phone=phone,
                bio_data=bio_data,
                verified=True,
                verification_method=id_type,
                id_number_encrypted=id_number_encrypted,
                id_document_id=document.id,
                portrait_url=portrait_url,
            )
        )

    # 7. Store hash mapping (idempotency for future re-uploads)
    try:
        await create_hash(
            IDVerificationHashCreate(
                tenant_id=tenant_id,
                sha256=sha256,
                visitor_id=visitor.id or "",
                extraction_id=extraction.id_extraction_id,
                document_id=document.id,
            )
        )
    except Exception as exc:
        # Duplicate key (race) or index issue — non-fatal for the caller.
        logger.warning("Failed to record verification hash: %s", exc)

    return visitor


async def list_verified_visitors(
    tenant_id: str, skip: int = 0, limit: int = 50
) -> list[VisitorOut]:
    if not ObjectId.is_valid(tenant_id):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Invalid tenant ID",
        )
    return await list_visitors_for_tenant(tenant_id=tenant_id, skip=skip, limit=limit)


async def _upload_portrait(*, tenant_id: str, image_bytes: bytes) -> str:
    """Upload cropped face bytes and return a presigned download URL."""
    from core.storage import DocumentStorageManager

    provider: Any = DocumentStorageManager.get_instance().provider
    object_key = f"portraits/{tenant_id}/{uuid4().hex}.jpg"
    provider.upload_bytes(
        object_key=object_key, payload=image_bytes, mime_type="image/jpeg"
    )
    return provider.download_url(object_key=object_key)


def _extension_for_mime(mime_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "application/pdf": ".pdf",
    }
    return mapping.get(mime_type.lower(), ".bin")
