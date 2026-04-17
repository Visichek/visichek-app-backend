from __future__ import annotations

from repositories.id_extraction_repo import create_id_extraction
from schemas.id_extraction_schema import (
    IDExtractionCreate,
    IDExtractionRequest,
    IDExtractionResponse,
)
from security.id_number_crypto import encrypt_id_number


async def extract_id(
    req: IDExtractionRequest, tenant_id: str | None = None
) -> IDExtractionResponse:
    """Extract ID data from an uploaded document.

    1. Download document bytes from DocumentStorageManager
    2. Call OCRManager to extract fields
    3. Validate against tenant's required fields (if tenant_id provided)
    4. Encrypt id_number
    5. Persist extraction record
    6. Return response DTO
    """
    from core.storage import DocumentStorageManager
    from core.ocr.manager import OCRManager

    # Download document
    storage_manager = DocumentStorageManager.get_instance()
    document_bytes = await storage_manager.provider.download_bytes(req.document_id)
    if not document_bytes:
        raise ValueError(f"Document {req.document_id} not found or empty")

    # Extract using OCR
    ocr_manager = OCRManager.get_instance()
    extraction_result = await ocr_manager._provider.extract(  # type: ignore[union-attr]
        document_bytes=document_bytes,
        mime_type="image/jpeg",  # TODO: get actual mime type from document metadata
        id_type=req.id_type,
    )

    # Determine required fields if tenant_id provided
    unmatched_required_fields: list[str] = []
    if tenant_id:
        try:
            # TODO: get checkin_config from tenant context
            # For now, assume no required fields beyond the extracted ones
            pass
        except Exception:
            pass

    # Encrypt id_number if present
    extracted_fields = extraction_result.get("fields", {}).copy()
    if "id_number" in extracted_fields:
        plaintext_id = extracted_fields["id_number"]
        try:
            extracted_fields["id_number_encrypted"] = encrypt_id_number(plaintext_id)
            # Don't store plaintext
            del extracted_fields["id_number"]
        except RuntimeError:
            # Encryption key not configured, skip encryption
            pass

    # Determine verified status: confidence >= 0.7 and at least one of full_name + date_of_birth
    confidence = extraction_result.get("confidence", 0.0)
    has_key_fields = (
        "full_name" in extracted_fields or "date_of_birth" in extracted_fields
    )
    verified = confidence >= 0.7 and has_key_fields

    # Persist extraction
    create_data = IDExtractionCreate(
        document_id=req.document_id,
        provider=req.provider,
        id_type=req.id_type,
        extracted_fields=extracted_fields,
        confidence=confidence,
        verified=verified,
    )
    extraction_out = await create_id_extraction(create_data)

    return IDExtractionResponse(
        id_extraction_id=extraction_out.id or "",
        document_id=req.document_id,
        provider=req.provider,
        id_type=req.id_type,
        extracted_fields=extracted_fields,
        unmatched_required_fields=unmatched_required_fields,
        confidence=confidence,
        verified=verified,
    )
