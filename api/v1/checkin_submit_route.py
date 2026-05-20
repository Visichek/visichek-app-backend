import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from core.errors import AppException, ErrorCode, resource_not_found
from core.response_envelope import document_response
from repositories.checkin_config_repo import get_checkin_config
from schemas.checkin_schema import CheckinOut, CheckinPurpose
from schemas.imports import IDType
from security.auth import verify_optional_kiosk_token
from security.principal import AuthPrincipal
from services.checkin_config_service import enforce_kiosk_submit_access
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
        "Public kiosk endpoint. Combines optional ID verification with "
        "check-in submission in a single multipart request.\n\n"
        "**Required fields**: ``phone`` (visitor identity key) and "
        "``full_name`` (passed via ``bio_data`` JSON, or as a top-level "
        "form field). Email is optional; everything else (purpose, "
        "company, etc.) follows the tenant's CheckinConfig and is "
        "submitted via ``bio_data`` / ``tenant_specific_data``.\n\n"
        "If ``id_file`` is uploaded, OCR runs and the visitor is marked "
        "verified. If OCR fails the caller receives 422 with guidance "
        "to retry with a clearer photo or resubmit without the file "
        "(manual-entry fallback). For full Dojah KYC, use the kiosk "
        "widget flow described in ``backend-docs/visitor-checkin-v2.md`` "
        "and submit ``kyc_reference_id`` instead of an ``id_file``."
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
    phone: str = Form(..., description="Visitor phone — required identity key."),
    full_name: Optional[str] = Form(
        None,
        description=(
            "Visitor full name. Required by the system at submission time, "
            "but may also be carried inside the ``bio_data`` JSON object — "
            "either source satisfies the requirement."
        ),
    ),
    email: Optional[str] = Form(None, description="Optional. May be omitted entirely."),
    purpose: str = Form(..., description="JSON object: { purpose, ... }"),
    bio_data: str = Form("{}", description="JSON object — bio fields"),
    tenant_specific_data: str = Form("{}", description="JSON object"),
    id_type: Optional[IDType] = Form(None),
    id_file: Optional[UploadFile] = File(None),
    kyc_reference_id: Optional[str] = Form(
        None,
        description=(
            "Dojah verification reference returned by the kiosk widget. "
            "When supplied the backend skips OCR and trusts the Dojah "
            "result (subject to plan + tenant settings allowing KYC)."
        ),
    ),
    registration_token: Optional[str] = Form(
        None,
        description=(
            "Signed QR registration token (Issue 5). When supplied, the "
            "backend verifies the token's tenant/department/branch scope "
            "and rejects any conflicting browser-supplied "
            "``tenant_specific_data['department_id']`` value. The "
            "resolved token id is recorded on the audit trail so we can "
            "trace which QR shaped each registration. Invalid or expired "
            "tokens return 400 with code ``INVALID_REGISTRATION_TOKEN`` "
            "rather than silently downgrading to an unscoped check-in."
        ),
    ),
    principal: Optional[AuthPrincipal] = Depends(verify_optional_kiosk_token),
) -> CheckinOut:
    # Plan gate: on Free / Starter the kiosk submit endpoint requires a
    # system user with visitor permissions. Resolve the config first so
    # we know which tenant the plan check applies to (the path key is
    # checkin_config_id, not tenant_id).
    config = await get_checkin_config({"_id": checkin_config_id, "active": True})
    if not config:
        raise resource_not_found(
            resource="CheckinConfig", resource_id=checkin_config_id
        )
    await enforce_kiosk_submit_access(tenant_id=config.tenant_id, principal=principal)

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

    # full_name may arrive top-level or in bio_data — either works.
    resolved_name = full_name or str(bio_dict.get("full_name") or "").strip()
    if not resolved_name:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="full_name is required (top-level or inside bio_data)",
        )
    bio_dict.setdefault("full_name", resolved_name)

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
        kyc_reference_id=kyc_reference_id,
        registration_token=registration_token,
    )
