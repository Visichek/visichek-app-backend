import json
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile, status

from core.errors import AppException, ErrorCode
from core.response_envelope import document_response
from schemas.checkin_schema import CheckinOut, CheckinPurpose
from schemas.imports import IDType
from services.checkin_service import submit_verified_checkin

router = APIRouter(prefix="/checkin-configs", tags=["Check-In Submit"])


def _parse_json_dict(raw: str, field_name: str) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"{field_name} must be a valid JSON object",
        ) from exc
    if not isinstance(value, dict):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"{field_name} must be a JSON object",
        )
    return value


@router.post("/{checkin_config_id}/submit", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Check-in submitted",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Public kiosk endpoint. Combines ID verification with check-in submission "
        "in a single multipart request. If `id_file` is uploaded, OCR + face "
        "verification run and the visitor is marked verified. If verification "
        "fails, the caller receives 422 with guidance to either retry with a "
        "clearer ID or resubmit without the file (manual entry fallback)."
    ),
    summary="Submit check-in (optional ID verification)",
    response_codes={
        400: "Missing/invalid fields or malformed JSON in a form field",
        404: "Check-in configuration not found",
        409: "Visitor has a pending check-in already",
        422: "ID verification failed — retry with clearer ID or submit without file",
    },
)
async def submit_checkin_endpoint(
    checkin_config_id: str,
    email: str = Form(...),
    phone: str = Form(...),
    purpose: str = Form(..., description="JSON object"),
    bio_data: str = Form("{}", description="JSON object — fields from the ID"),
    tenant_specific_data: str = Form("{}", description="JSON object"),
    id_type: Optional[IDType] = Form(None),
    id_file: Optional[UploadFile] = File(None),
) -> CheckinOut:
    bio_dict = _parse_json_dict(bio_data, "bio_data")
    tsd_dict = _parse_json_dict(tenant_specific_data, "tenant_specific_data")
    purpose_dict = _parse_json_dict(purpose, "purpose")

    try:
        purpose_obj = CheckinPurpose(**purpose_dict)
    except Exception as exc:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"purpose is invalid: {exc}",
        ) from exc

    file_bytes: Optional[bytes] = None
    file_mime: Optional[str] = None
    if id_file is not None:
        file_bytes = await id_file.read()
        if not file_bytes:
            raise AppException(
                status_code=400,
                code=ErrorCode.VALIDATION_FAILED,
                message="id_file is empty",
            )
        file_mime = id_file.content_type or "application/octet-stream"

    return await submit_verified_checkin(
        checkin_config_id=checkin_config_id,
        email=email,
        phone=phone,
        bio_data=bio_dict,
        tenant_specific_data=tsd_dict,
        purpose=purpose_obj,
        id_file_bytes=file_bytes,
        id_file_mime=file_mime,
        id_type=id_type,
    )
