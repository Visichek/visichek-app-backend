from fastapi import APIRouter, File, Form, Query, UploadFile, status

from core.response_envelope import document_response
from schemas.imports import IDType
from schemas.visitor_schema import VisitorOut
from services.visitor_verification_service import (
    list_verified_visitors,
    verify_visitor_from_id,
)

router = APIRouter(prefix="/visitor-verification", tags=["Visitor Verification"])


@router.post("/test")
@document_response(
    message="Visitor verified",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Public test endpoint. Upload an ID document (JPEG/PNG/WEBP/BMP/PDF). "
        "OCR runs via Document AI, a face is cropped out, and a verified visitor "
        "record is created or updated (matched by email or phone)."
    ),
    summary="Verify visitor from ID upload",
    response_codes={
        400: "Missing or invalid input",
        413: "File too large",
        422: "No face detected in the ID",
        502: "OCR / face detection service error",
        503: "Required service dependency missing",
    },
)
async def verify_visitor_test(
    tenant_id: str = Form(...),
    id_type: IDType = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    file: UploadFile = File(...),
) -> VisitorOut:
    file_bytes = await file.read()
    return await verify_visitor_from_id(
        tenant_id=tenant_id,
        file_bytes=file_bytes,
        mime_type=file.content_type or "application/octet-stream",
        id_type=id_type,
        email=email,
        phone=phone,
    )


@router.get("/visitors")
@document_response(
    message="Verified visitors retrieved",
    description="Public test endpoint. List visitors for a tenant (newest first).",
    summary="List verified visitors",
    include_meta=True,
)
async def list_verified_visitors_endpoint(
    tenant_id: str = Query(...),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    items = await list_verified_visitors(tenant_id=tenant_id, skip=skip, limit=limit)
    return items, {"skip": skip, "limit": limit, "total": len(items)}
