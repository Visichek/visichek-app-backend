from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from core.response_envelope import document_response
from schemas.badge_schema import BadgeValidationResponse
from services.badge_service import validate_badge

router = APIRouter(prefix="/badges", tags=["Badges"])


@router.get(
    "/validate",
    response_model=BadgeValidationResponse,
)
@document_response(
    message="Badge validation result",
    description="Validate a badge by QR code value (scanner/kiosk, unauthenticated).",
    summary="Validate badge",
    success_example={
        "valid": True,
        "badge_id": "507f1f77bcf86cd799439012",
        "qr_code_value": "aB3cD4eF5gH6iJ7kL8mN9oP0q",
        "visitor_name": "John Doe",
        "verified": False,
        "portrait_url": "https://s3.example.com/portraits/john_doe.jpg",
        "host_employee_name": "Jane Smith",
        "purpose": "Meeting",
        "issued_at": 1710000000,
        "expires_at": 1710086400,
    },
    response_codes={
        404: "Badge not found (valid=False, reason=not_found)",
    },
)
async def validate_badge_endpoint(
    qr_code_value: Annotated[str, Query(description="QR code value from badge")]
):
    """Validate a badge by QR code (scanner/kiosk, unauthenticated)."""
    return await validate_badge(qr_code_value)
