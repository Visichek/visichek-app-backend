import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from core.errors import AppException, ErrorCode
from core.queue.entity_cache import get_or_compute_entity
from core.response_envelope import document_response
from schemas.checkin_schema import CheckinOut, CheckinPurpose
from schemas.imports import IDType
from schemas.public_registration_schema import (
    PublicFinalizeRequest,
    PublicRegistrationRequest,
    PublicReturningVisitorLookupRequest,
    PublicReturningVisitorSubmitRequest,
    PublicVisitorStatusRequest,
)
from security.auth import verify_optional_kiosk_token
from security.principal import AuthPrincipal
from services.checkin_config_service import (
    enforce_kiosk_submit_access,
    resolve_public_config_by_tenant,
)
from services.checkin_service import (
    submit_returning_visitor_checkin_by_id,
    submit_verified_checkin_for_tenant,
)
from services.public_registration_service import (
    check_returning_visitor_status,
    checkout_visitor_public,
    finalize_public_registration,
    get_public_privacy_notice,
    get_public_tenant_info,
    list_public_departments,
    lookup_public_appointment,
    lookup_returning_visitor,
    public_ocr_id_scan,
    register_visitor_public,
    verify_public_registration_token,
)

router = APIRouter(prefix="/public", tags=["Public Registration"])


@router.post("/register/{tenant_id}")
@document_response(
    message="Visitor registered successfully",
    status_code=status.HTTP_201_CREATED,
    description="Public visitor self-registration endpoint. No authentication required. Creates a visitor profile and session with REGISTERED status.",
    summary="Public visitor self-registration",
    success_example={
        "session_id": "507f1f77bcf86cd799439011",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "status": "registered",
        "message": "Registration successful. Please proceed to reception.",
    },
    response_codes={
        400: "Missing required fields",
        404: "Tenant not found",
        422: "Validation error",
    },
)
async def public_register_endpoint(
    tenant_id: str,
    request: PublicRegistrationRequest,
    principal: Optional[AuthPrincipal] = Depends(verify_optional_kiosk_token),
):
    await enforce_kiosk_submit_access(tenant_id=tenant_id, principal=principal)
    return await register_visitor_public(tenant_id=tenant_id, request=request)


@router.get("/register/{tenant_id}/departments")
@document_response(
    message="Departments retrieved",
    description="List active departments for a tenant's public registration form.",
    summary="List departments for public registration",
    success_example=[
        {"id": "dept123", "name": "Engineering"},
        {"id": "dept456", "name": "Sales"},
    ],
)
async def list_tenant_departments_public_endpoint(tenant_id: str):
    return await get_or_compute_entity(
        entity_type="public_tenant_departments",
        entity_id=tenant_id,
        loader=lambda: list_public_departments(tenant_id=tenant_id),
    )


@router.get("/register/{tenant_id}/info")
@document_response(
    message="Tenant info retrieved",
    description="Return basic tenant info and branding for the public registration UI.",
    summary="Get tenant info for public registration",
    success_example={"tenant_id": "t123", "company_name": "Acme Corp"},
)
async def get_tenant_info_public_endpoint(tenant_id: str):
    return await get_or_compute_entity(
        entity_type="public_tenant_info",
        entity_id=tenant_id,
        loader=lambda: get_public_tenant_info(tenant_id=tenant_id),
    )


@router.get("/register/{tenant_id}/privacy-notice")
@document_response(
    message="Privacy notice retrieved",
    description="Return the active privacy notice text for the tenant. Must be displayed before data capture.",
    summary="Get privacy notice for public registration",
    success_example={
        "notice_id": "n123",
        "title": "Privacy Notice",
        "content": "We collect your data for...",
        "version": "1.0",
    },
)
async def get_privacy_notice_public_endpoint(tenant_id: str):
    return await get_or_compute_entity(
        entity_type="public_privacy_notice",
        entity_id=tenant_id,
        loader=lambda: get_public_privacy_notice(tenant_id=tenant_id),
    )


@router.post("/checkout")
@document_response(
    message="Visitor checked out successfully",
    description="Public self-service check-out via badge QR token. No authentication required.",
    summary="Public visitor self-checkout",
    success_example={
        "session_id": "507f1f77bcf86cd799439011",
        "status": "checked_out",
        "visit_duration": 3600,
    },
    response_codes={400: "Invalid or expired QR token", 404: "Session not found"},
)
async def public_checkout_endpoint(
    badge_qr_token: str,
    principal: Optional[AuthPrincipal] = Depends(verify_optional_kiosk_token),
):
    # Plan gate: on Free / Starter the kiosk self-checkout requires a
    # system user with visitor permissions. We need the tenant_id
    # before we can check the plan, so resolve the badge first.
    from services.qr_service import verify_badge_token
    from bson import ObjectId
    from repositories.visit_session_repo import get_visit_session

    session_id = verify_badge_token(badge_qr_token)
    if session_id and ObjectId.is_valid(session_id):
        session = await get_visit_session({"_id": ObjectId(session_id)})
        if session is not None:
            await enforce_kiosk_submit_access(
                tenant_id=session.tenant_id, principal=principal
            )
    return await checkout_visitor_public(badge_qr_token=badge_qr_token)


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


@router.post("/tenants/{tenant_id}/submit", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Check-in submitted",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Public kiosk submit endpoint keyed by tenant_id. Resolves the tenant's "
        "active check-in configuration server-side; falls back to the default "
        "required-field set when the tenant has not configured one. Same "
        "multipart contract as POST /checkin-configs/{checkin_config_id}/submit: "
        "if `id_file` is uploaded, OCR + face verification run and the visitor "
        "is marked verified. 422 on verification failure — retry with a clearer "
        "ID or resubmit without the file."
    ),
    summary="Submit check-in by tenant (optional ID verification)",
    response_codes={
        400: "Missing/invalid fields or malformed JSON in a form field",
        404: "Tenant not found",
        409: "Visitor has a pending check-in already",
        422: "ID verification failed — retry with clearer ID or submit without file",
    },
)
async def submit_checkin_for_tenant_endpoint(
    tenant_id: str,
    phone: str = Form(..., description="Visitor phone — required identity key."),
    full_name: Optional[str] = Form(
        None,
        description=(
            "Visitor full name. Required by the system at submission time, "
            "but may also be carried inside the ``bio_data`` JSON object — "
            "either source satisfies the requirement."
        ),
    ),
    email: Optional[str] = Form(
        None, description="Optional. May be omitted entirely."
    ),
    purpose: str = Form(..., description="JSON object"),
    bio_data: str = Form("{}", description="JSON object — fields from the ID"),
    tenant_specific_data: str = Form("{}", description="JSON object"),
    id_type: Optional[IDType] = Form(None),
    id_file: Optional[UploadFile] = File(None),
    visitor_lat: Optional[float] = Form(
        None,
        description="Visitor latitude — required when tenant has geofencing enabled",
    ),
    visitor_lng: Optional[float] = Form(
        None,
        description="Visitor longitude — required when tenant has geofencing enabled",
    ),
    kyc_reference_id: Optional[str] = Form(
        None,
        description=(
            "Dojah verification reference returned by the kiosk widget. "
            "When supplied the backend skips OCR and trusts the Dojah "
            "result (subject to plan + tenant settings allowing KYC)."
        ),
    ),
    principal: Optional[AuthPrincipal] = Depends(verify_optional_kiosk_token),
) -> CheckinOut:
    await enforce_kiosk_submit_access(tenant_id=tenant_id, principal=principal)
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

    return await submit_verified_checkin_for_tenant(
        tenant_id=tenant_id,
        email=email,
        phone=phone,
        bio_data=bio_dict,
        tenant_specific_data=tsd_dict,
        purpose=purpose_obj,
        id_file_bytes=file_bytes,
        id_file_mime=file_mime,
        id_type=id_type,
        kyc_reference_id=kyc_reference_id,
        visitor_lat=visitor_lat,
        visitor_lng=visitor_lng,
    )


@router.get("/tenants/{tenant_id}/active-checkin-config")
@document_response(
    message="Active check-in configuration retrieved",
    description=(
        "Return the tenant's active check-in configuration. If the tenant has "
        "not configured one yet, a default config is returned (bio fields + "
        "purpose, ID upload and returning-visitor lookup enabled) so the "
        "public kiosk / registration UI can still render a usable form. "
        "`checkin_config_id` is an empty string in the default case — use that "
        "as a signal that the tenant has not customized their config."
    ),
    summary="Get active check-in config for tenant (public)",
    success_example={
        "checkin_config_id": "507f1f77bcf86cd799439012",
        "tenant_id": "t123",
        "tenant_name": "Acme Corp",
        "logo_url": "https://cdn.example.com/tenants/acme/logo.png",
        "id_upload_enabled": True,
        "allow_returning_visitor_lookup": True,
        "required_fields": [
            {
                "key": "full_name",
                "label": "Full Name",
                "type": "text",
                "required": True,
                "category": "bio",
            }
        ],
    },
    response_codes={404: "Tenant not found"},
)
async def get_active_checkin_config_for_tenant_endpoint(tenant_id: str):
    return await resolve_public_config_by_tenant(tenant_id=tenant_id)


@router.get("/register/{tenant_id}/appointment/{appointment_id}")
@document_response(
    message="Appointment details retrieved",
    description="Look up a scheduled appointment to pre-fill the registration form.",
    summary="Public appointment lookup",
    success_example={
        "appointment_id": "a123",
        "host_name": "Jane Smith",
        "department_name": "Sales",
        "scheduled_at": 1712000000,
    },
    response_codes={404: "Appointment not found or not scheduled"},
)
async def lookup_appointment_public_endpoint(tenant_id: str, appointment_id: str):
    return await lookup_public_appointment(
        tenant_id=tenant_id, appointment_id=appointment_id
    )


@router.get("/register/verify")
@document_response(
    message="Registration token verified",
    description="Verify a signed QR registration token before rendering the public form. Returns the bound tenant/department/branch scope so the client can pre-fill and lock those fields.",
    summary="Verify public registration QR token",
    success_example={
        "valid": True,
        "tenant_id": "t123",
        "department_id": "d1",
        "branch_id": None,
        "company_name": "Acme Corp",
    },
)
async def verify_registration_token_public_endpoint(token: str):
    return await verify_public_registration_token(token)


@router.get("/verify-registration-token")
@document_response(
    message="Registration token verified",
    description=(
        "Alias of ``GET /v1/public/register/verify``. Kept so older "
        "frontend builds that hard-coded the flatter URL keep working "
        "without a redeploy. Both routes return the same payload."
    ),
    summary="Verify public registration QR token (alias)",
    success_example={
        "valid": True,
        "tenant_id": "t123",
        "department_id": "d1",
        "branch_id": None,
        "company_name": "Acme Corp",
    },
)
async def verify_registration_token_alias_endpoint(token: str):
    return await verify_public_registration_token(token)


@router.post("/register/{tenant_id}/id-scan")
@document_response(
    message="ID scan extracted",
    description="Run OCR on an uploaded ID image and return extracted fields for visitor confirmation. The image is processed in-memory and is NOT persisted — submit the confirmed fields through /public/register/{tenant_id} afterwards.",
    summary="Public OCR ID scan (in-memory, no session created)",
    success_example={
        "full_name": "John Doe",
        "id_number": "A12345678",
        "id_type": "national_id",
        "confidence": 0.93,
    },
    response_codes={
        400: "Invalid image or OCR extraction failed",
        413: "Image exceeds 8 MiB limit",
        503: "OCR service unavailable",
    },
)
async def public_ocr_scan_endpoint(tenant_id: str, file: UploadFile = File(...)):
    image_bytes = await file.read()
    return await public_ocr_id_scan(
        tenant_id=tenant_id,
        image_bytes=image_bytes,
        mime_type=file.content_type or "image/jpeg",
    )


@router.post(
    "/tenants/{tenant_id}/submit-by-visitor-id",
    status_code=status.HTTP_201_CREATED,
)
@document_response(
    message="Check-in submitted",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Returning-visitor submit endpoint keyed on a known ``visitor_id`` "
        "(obtained from ``POST /v1/public/tenants/{tenant_id}/visitor-status``). "
        "No email, phone, or bio_data is required — the backend loads the "
        "visitor's stored profile and uses it to satisfy the config's "
        "``BIO``-category required fields. The frontend only has to re-supply "
        "``purpose`` plus any required ``TENANT_SPECIFIC`` fields "
        "(``tenant_specific_data``). If the tenant's active config has no "
        "required tenant-specific fields, ``tenant_specific_data`` may be an "
        "empty object. ID re-upload is not supported on this endpoint — if "
        "the visitor needs re-verification, fall back to the "
        "email/phone-based ``/submit`` route."
    ),
    summary="Submit check-in for a known returning visitor (by visitor_id)",
    response_codes={
        400: "Missing required tenant-specific fields, or invalid ids",
        404: "Tenant or visitor not found",
        409: "Visitor has a pending check-in already",
    },
)
async def submit_checkin_for_returning_visitor_endpoint(
    tenant_id: str,
    request: PublicReturningVisitorSubmitRequest,
    principal: Optional[AuthPrincipal] = Depends(verify_optional_kiosk_token),
) -> CheckinOut:
    await enforce_kiosk_submit_access(tenant_id=tenant_id, principal=principal)
    return await submit_returning_visitor_checkin_by_id(
        tenant_id=tenant_id,
        visitor_id=request.visitor_id,
        purpose=request.purpose,
        tenant_specific_data=request.tenant_specific_data,
        visitor_lat=request.visitor_lat,
        visitor_lng=request.visitor_lng,
    )


@router.post("/tenants/{tenant_id}/visitor-status")
@document_response(
    message="Visitor recognition status retrieved",
    description=(
        "Public recognition endpoint keyed on phone OR email within a tenant. "
        "Returns only a non-PII recognition payload — whether the tenant has "
        "seen this visitor before plus visit counters and the id-verification "
        "recency flag. No name, email, phone, company, id_type, or profile_id "
        "is returned. The kiosk uses this to drive a 'welcome back, just tell "
        "us your purpose' UX; the submit endpoint re-resolves the profile "
        "server-side and fills in any required fields the visitor did not "
        "re-type. Edits to stored PII are reserved for authenticated "
        "receptionist / super_admin endpoints. Unauthenticated — subject to "
        "the anonymous rate limit."
    ),
    summary="Public visitor recognition status (no PII)",
    success_example={
        "found": True,
        "total_visits": 5,
        "last_visit_ago_days": 12,
        "id_verified_recently": True,
    },
    response_codes={
        400: "phone or email is required, or invalid tenant id",
    },
)
async def check_returning_visitor_status_public_endpoint(
    tenant_id: str, request: PublicVisitorStatusRequest
):
    return await check_returning_visitor_status(tenant_id=tenant_id, request=request)


@router.post("/register/{tenant_id}/lookup")
@document_response(
    message="Visitor lookup complete",
    description="Check whether a returning visitor matches by phone or email. Returns a masked name + opaque profile_id on match; raw PII is never returned. Submit the phone again on /register to prove possession.",
    summary="Returning-visitor masked lookup",
    success_example={
        "found": True,
        "profile_id": "507f1f77bcf86cd799439012",
        "full_name_masked": "J*** D***",
        "company": "Acme Corp",
        "last_visit_ago_days": 12,
        "id_verified_recently": True,
    },
)
async def lookup_returning_visitor_public_endpoint(
    tenant_id: str, request: PublicReturningVisitorLookupRequest
):
    return await lookup_returning_visitor(tenant_id=tenant_id, request=request)


@router.post("/register/{tenant_id}/finalize")
@document_response(
    message="Check-in finalized",
    description="Finalize a REGISTERED session by naming the receptionist accepting the visitor. `receptionist_code` is the receptionist's system_user id displayed at reception. Generates the badge and transitions the session to CHECKED_IN.",
    summary="Public finalize-by-receptionist-code",
    success_example={"session": {"status": "checked_in"}, "badge_qr_token": "VIS_..."},
    response_codes={
        400: "Invalid session/receptionist id or session not in REGISTERED state",
        404: "Receptionist or session not found",
    },
)
async def finalize_registration_public_endpoint(
    tenant_id: str, request: PublicFinalizeRequest
):
    return await finalize_public_registration(tenant_id=tenant_id, request=request)
