from __future__ import annotations

from fastapi import APIRouter, status

from core.response_envelope import document_response
from schemas.id_extraction_schema import (
    IDExtractionRequest,
    IDExtractionResponse,
)
from services.id_extraction_service import extract_id

router = APIRouter(prefix="/id-extractions", tags=["ID Extractions"])


@router.post(
    "", response_model=IDExtractionResponse, status_code=status.HTTP_201_CREATED
)
@document_response(
    message="ID extracted successfully",
    description="Extract ID data from an uploaded document via OCR (kiosk, unauthenticated).",
    summary="Extract ID",
    status_code=status.HTTP_201_CREATED,
    success_example={
        "id_extraction_id": "507f1f77bcf86cd799439012",
        "document_id": "doc123",
        "provider": "google_document_ai",
        "id_type": "passport",
        "extracted_fields": {
            "full_name": "John Doe",
            "date_of_birth": "1990-01-15",
            "nationality": "US",
            "address": "123 Main St, Springfield, IL 62701",
            "id_number_encrypted": "gAAAAABlNzQ1...",
        },
        "unmatched_required_fields": [],
        "confidence": 0.95,
        "verified": True,
    },
    response_codes={
        400: "Invalid request or document not found",
        500: "OCR provider error or extraction failed",
    },
)
async def extract_id_endpoint(payload: IDExtractionRequest):
    """Extract ID data from a document (kiosk, unauthenticated).

    NOTE: In production, this should be gated behind a short-lived anonymous
    kiosk token to prevent abuse. Currently rate-limited by the global
    rate-limiting middleware.
    """
    return await extract_id(payload, tenant_id=None)
