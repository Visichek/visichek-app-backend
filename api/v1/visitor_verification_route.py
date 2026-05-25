from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from core.response_envelope import document_response
from schemas.imports import IDType
from schemas.visitor_schema import VisitorOut
from security.auth import verify_any_system_user_token
from security.principal import AuthPrincipal
from services.visitor_verification_service import (
    list_verified_visitors,
    verify_visitor_from_id,
)

router = APIRouter(prefix="/visitor-verification", tags=["Visitor Verification"])


@router.post("/from-id")
@document_response(
    message="Visitor verified",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Upload an ID document (JPEG/PNG/WEBP/BMP/PDF). OCR runs via "
        "Document AI, a face is cropped out, and a verified visitor record "
        "is created or updated (matched by email or phone). Scoped to the "
        "authenticated tenant — the caller's token determines the tenant, "
        "callers cannot target another tenant."
    ),
    summary="Verify visitor from ID upload",
    response_codes={
        400: "Missing or invalid input",
        401: "Unauthorized - invalid or missing authentication token",
        403: "Forbidden - caller is not a tenant user",
        413: "File too large",
        422: "No face detected in the ID",
        502: "OCR / face detection service error",
        503: "Required service dependency missing",
    },
)
async def verify_visitor_from_id_endpoint(
    id_type: IDType = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    file: UploadFile = File(...),
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> VisitorOut:
    file_bytes = await file.read()
    return await verify_visitor_from_id(
        tenant_id=principal.tenant_id or "",
        file_bytes=file_bytes,
        mime_type=file.content_type or "application/octet-stream",
        id_type=id_type,
        email=email,
        phone=phone,
    )


@router.get("/visitors")
@document_response(
    message="Verified visitors retrieved",
    description=(
        "List verified visitors for the authenticated tenant (newest "
        "first). Scoped to the caller's tenant via their token."
    ),
    summary="List verified visitors",
    include_meta=True,
)
async def list_verified_visitors_endpoint(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    items = await list_verified_visitors(
        tenant_id=principal.tenant_id or "", skip=skip, limit=limit
    )
    return items, {"skip": skip, "limit": limit, "total": len(items)}
