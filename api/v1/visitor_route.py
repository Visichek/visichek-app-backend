from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import io

from core.queue.entity_cache import get_or_compute_entity
from core.response_envelope import document_response
from schemas.visit_session_schema import (
    CheckInRequest,
    CheckOutRequest,
    ConfirmCheckInRequest,
    DenyVisitorRequest,
    VisitSessionUpdate,
)
from security.auth import verify_system_user_token, verify_any_system_user_token
from security.principal import AuthPrincipal
from services.visit_session_service import (
    check_in_visitor,
    check_out_visitor,
    retrieve_active_visitors,
    retrieve_visit_session_by_id_with_summary,
    retrieve_visit_sessions_with_summary,
    retrieve_visitors_awaiting_checkout,
    confirm_check_in,
    deny_visitor,
    retrieve_pending_sessions,
    verify_id_with_ocr,
    apply_id_scan_verification,
    approve_visitor_by_host,
    download_badge_pdf,
    resume_draft_registration,
    generate_tenant_registration_qr,
)
from schemas.visit_session_schema import VisitSessionWithSummaryOut

router = APIRouter(prefix="/visitors", tags=["Visitors"])

_checkin_roles = verify_system_user_token("receptionist", "dept_admin", "super_admin")


class ApplyIdScanRequest(BaseModel):
    id_type: str
    id_number: str
    id_image_object_key: Optional[str] = None


class ApproveVisitorRequest(BaseModel):
    pass  # No body required; host_id comes from auth principal


class RegistrationQrRequest(BaseModel):
    department_id: Optional[str] = None
    branch_id: Optional[str] = None


@router.post("/registration-qr")
@document_response(
    message="Registration QR token generated",
    status_code=status.HTTP_201_CREATED,
    description="Mint a signed registration token for a visitor-facing QR code. The token encodes tenant + optional department/branch scope and expires after 30 days. Render the returned `signed_token` as a QR — the public form calls /v1/public/register/verify to validate it.",
    summary="Generate registration QR token (receptionist / super_admin)",
    success_example={
        "registration_url": "/public/register/t123",
        "signed_token": "base64url-hmac-token",
        "qr_data": "base64url-hmac-token",
        "tenant_id": "t123",
        "department_id": "d1",
        "branch_id": None,
    },
)
async def generate_registration_qr_endpoint(
    request: RegistrationQrRequest,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await generate_tenant_registration_qr(
        tenant_id=tenant_id,
        department_id=request.department_id,
        branch_id=request.branch_id,
    )


@router.post("/check-in")
@document_response(
    message="Visitor checked in successfully",
    status_code=status.HTTP_201_CREATED,
    description="Check in a new visitor by creating a visit session. Requires valid department and host information.",
    summary="Create visitor check-in session",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "department_id": "d12345",
        "host_id": "h12345",
        "receptionist_id": "r12345",
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "manual_entry",
        "check_out_method": None,
        "verification_status": "unverified",
        "verification_method": None,
        "verified_by": None,
        "status": "checked_in",
        "purpose": "Business meeting",
        "visitor_name_snapshot": "John Doe",
        "company_snapshot": "Acme Corp",
        "host_name_snapshot": "Jane Smith",
        "department_name_snapshot": "Sales",
        "receptionist_name_snapshot": "Mike Johnson",
        "consent_notice_displayed": True,
        "consent_granted": True,
        "consent_method": "digital_signature",
        "consent_timestamp": 1712532000,
        "consent_captured_by_user_id": "r12345",
        "consent_withdrawal_at": None,
        "lawful_basis_at_time": "legitimate_interest",
        "badge_qr_token": "VIS_20240407_1234567890AB",
        "badge_format": "pdf_qr",
        "badge_generation_time": 1712532010,
        "badge_expiry": 1712618400,
        "badge_pdf_object_key": "badges/visit_507f1f77bcf86cd799439011.pdf",
        "check_in_time": 1712532000,
        "check_out_time": None,
        "date_created": 1712532000,
        "visit_duration": None,
    },
    response_codes={
        400: "Missing required fields (name, phone, purpose, department, host)",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Department or host not found",
        422: "Validation error - invalid request payload",
    },
    error_examples={
        400: {
            "success": False,
            "message": "Missing required field: purpose",
            "code": "VALIDATION_FAILED",
        },
        404: {
            "success": False,
            "message": "Department not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def check_in(
    request: CheckInRequest,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await check_in_visitor(
        request=request,
        tenant_id=tenant_id,
        receptionist_id=principal.user_id,
    )


@router.post("/check-out")
@document_response(
    message="Visitor checked out successfully",
    description="End a visitor visit session by recording check-out time and closing the session.",
    summary="Record visitor check-out",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "department_id": "d12345",
        "host_id": "h12345",
        "receptionist_id": "r12345",
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "manual_entry",
        "check_out_method": "qr_scan",
        "verification_status": "verified",
        "verification_method": "id_document",
        "verified_by": "r12345",
        "status": "checked_out",
        "purpose": "Business meeting",
        "visitor_name_snapshot": "John Doe",
        "company_snapshot": "Acme Corp",
        "host_name_snapshot": "Jane Smith",
        "department_name_snapshot": "Sales",
        "receptionist_name_snapshot": "Mike Johnson",
        "consent_notice_displayed": True,
        "consent_granted": True,
        "consent_method": "digital_signature",
        "consent_timestamp": 1712532000,
        "consent_captured_by_user_id": "r12345",
        "consent_withdrawal_at": None,
        "lawful_basis_at_time": "legitimate_interest",
        "badge_qr_token": "VIS_20240407_1234567890AB",
        "badge_format": "pdf_qr",
        "badge_generation_time": 1712532010,
        "badge_expiry": 1712618400,
        "badge_pdf_object_key": "badges/visit_507f1f77bcf86cd799439011.pdf",
        "check_in_time": 1712532000,
        "check_out_time": 1712535600,
        "date_created": 1712532000,
        "visit_duration": 3600,
    },
    response_codes={
        400: "Missing required fields (session_id)",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Visit session not found",
    },
    error_examples={
        404: {
            "success": False,
            "message": "Visit session not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def check_out(
    request: CheckOutRequest,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await check_out_visitor(request=request, tenant_id=tenant_id)


@router.get("/active")
@document_response(
    message="Active visitors fetched successfully",
    description="Retrieve list of currently checked-in visitors, optionally filtered by department.",
    summary="List active visitors",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "t12345",
            "visitor_profile_id": "507f1f77bcf86cd799439012",
            "department_id": "d12345",
            "host_id": "h12345",
            "receptionist_id": "r12345",
            "appointment_id": None,
            "privacy_notice_version_id": None,
            "check_in_method": "manual_entry",
            "check_out_method": None,
            "verification_status": "unverified",
            "verification_method": None,
            "verified_by": None,
            "status": "checked_in",
            "purpose": "Business meeting",
            "visitor_name_snapshot": "John Doe",
            "company_snapshot": "Acme Corp",
            "host_name_snapshot": "Jane Smith",
            "department_name_snapshot": "Sales",
            "receptionist_name_snapshot": "Mike Johnson",
            "consent_notice_displayed": True,
            "consent_granted": True,
            "consent_method": "digital_signature",
            "consent_timestamp": 1712532000,
            "consent_captured_by_user_id": "r12345",
            "consent_withdrawal_at": None,
            "lawful_basis_at_time": "legitimate_interest",
            "badge_qr_token": "VIS_20240407_1234567890AB",
            "badge_format": "pdf_qr",
            "badge_generation_time": 1712532010,
            "badge_expiry": 1712618400,
            "badge_pdf_object_key": "badges/visit_507f1f77bcf86cd799439011.pdf",
            "check_in_time": 1712532000,
            "check_out_time": None,
            "date_created": 1712532000,
            "visit_duration": None,
        }
    ],
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_active_visitors(
    department_id: Optional[str] = None,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_active_visitors(
        tenant_id=tenant_id, department_id=department_id
    )


@router.get("/awaiting-checkout")
@document_response(
    message="Visitors awaiting checkout fetched successfully",
    description=(
        "Paginated manual checkout selector. Includes checked-in visit sessions, "
        "approved check-ins that have not been checked out, and scheduled "
        "appointments whose scheduled day is today or earlier. Each row returns "
        "source_type plus checkout_id, flattened visitor details, summaries, and "
        "the source record in details. Submit source_type + checkout_id, or the "
        "specific session_id/checkin_id/appointment_id, to POST /v1/visitors/check-out."
    ),
    summary="List visitors awaiting checkout (manual selector)",
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_visitors_awaiting_checkout(
    department_id: Optional[str] = None,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0, le=200)] = 50,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    tenant_id = principal.tenant_id or ""
    items, total = await retrieve_visitors_awaiting_checkout(
        tenant_id=tenant_id,
        department_id=department_id,
        start=start,
        stop=stop,
    )
    return items, {"total": total, "start": start, "stop": stop}


@router.get("/sessions")
@document_response(
    message="Visit sessions fetched successfully",
    description="Retrieve paginated list of visitor visit sessions with optional department filtering.",
    summary="List visit sessions with pagination",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "t12345",
            "visitor_profile_id": "507f1f77bcf86cd799439012",
            "department_id": "d12345",
            "host_id": "h12345",
            "receptionist_id": "r12345",
            "appointment_id": None,
            "privacy_notice_version_id": None,
            "check_in_method": "manual_entry",
            "check_out_method": "qr_scan",
            "verification_status": "verified",
            "verification_method": "id_document",
            "verified_by": "r12345",
            "status": "checked_out",
            "purpose": "Business meeting",
            "visitor_name_snapshot": "John Doe",
            "company_snapshot": "Acme Corp",
            "host_name_snapshot": "Jane Smith",
            "department_name_snapshot": "Sales",
            "receptionist_name_snapshot": "Mike Johnson",
            "consent_notice_displayed": True,
            "consent_granted": True,
            "consent_method": "digital_signature",
            "consent_timestamp": 1712532000,
            "consent_captured_by_user_id": "r12345",
            "consent_withdrawal_at": None,
            "lawful_basis_at_time": "legitimate_interest",
            "badge_qr_token": "VIS_20240407_1234567890AB",
            "badge_format": "pdf_qr",
            "badge_generation_time": 1712532010,
            "badge_expiry": 1712618400,
            "badge_pdf_object_key": "badges/visit_507f1f77bcf86cd799439011.pdf",
            "check_in_time": 1712532000,
            "check_out_time": 1712535600,
            "date_created": 1712532000,
            "visit_duration": 3600,
        }
    ],
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_visit_sessions(
    department_id: Optional[str] = None,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("dept_admin", "super_admin", "auditor")
    ),
) -> list[VisitSessionWithSummaryOut]:
    tenant_id = principal.tenant_id or ""
    return await retrieve_visit_sessions_with_summary(
        tenant_id=tenant_id, department_id=department_id, start=start, stop=stop
    )


@router.get("/sessions/pending")
@document_response(
    message="Pending sessions fetched successfully",
    description="Retrieve paginated list of pending visitor sessions (status: REGISTERED or PENDING_VERIFICATION) with optional department filtering.",
    summary="List pending visitor sessions",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_pending_sessions(
    department_id: Optional[str] = None,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("dept_admin", "super_admin", "receptionist")
    ),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_pending_sessions(
        tenant_id=tenant_id, department_id=department_id, start=start, stop=stop
    )


@router.get("/sessions/{session_id}")
@document_response(
    message="Visit session fetched successfully",
    description="Retrieve detailed information about a specific visitor visit session.",
    summary="Fetch visit session by ID",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "department_id": "d12345",
        "host_id": "h12345",
        "receptionist_id": "r12345",
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "manual_entry",
        "check_out_method": "qr_scan",
        "verification_status": "verified",
        "verification_method": "id_document",
        "verified_by": "r12345",
        "status": "checked_out",
        "purpose": "Business meeting",
        "visitor_name_snapshot": "John Doe",
        "company_snapshot": "Acme Corp",
        "host_name_snapshot": "Jane Smith",
        "department_name_snapshot": "Sales",
        "receptionist_name_snapshot": "Mike Johnson",
        "consent_notice_displayed": True,
        "consent_granted": True,
        "consent_method": "digital_signature",
        "consent_timestamp": 1712532000,
        "consent_captured_by_user_id": "r12345",
        "consent_withdrawal_at": None,
        "lawful_basis_at_time": "legitimate_interest",
        "badge_qr_token": "VIS_20240407_1234567890AB",
        "badge_format": "pdf_qr",
        "badge_generation_time": 1712532010,
        "badge_expiry": 1712618400,
        "badge_pdf_object_key": "badges/visit_507f1f77bcf86cd799439011.pdf",
        "check_in_time": 1712532000,
        "check_out_time": 1712535600,
        "date_created": 1712532000,
        "visit_duration": 3600,
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Visit session not found",
    },
    error_examples={
        404: {
            "success": False,
            "message": "Visit session not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def get_visit_session_endpoint(
    session_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await get_or_compute_entity(
        entity_type="visit_session",
        entity_id=session_id,
        loader=lambda: retrieve_visit_session_by_id_with_summary(
            session_id=session_id, tenant_id=tenant_id
        ),
    )


@router.post("/sessions/{session_id}/confirm")
@document_response(
    message="Visitor check-in confirmed successfully",
    status_code=status.HTTP_200_OK,
    description="Confirm check-in for a registered visitor, generate badge, and transition to CHECKED_IN status.",
    summary="Confirm visitor check-in and generate badge",
    success_example={
        "session": {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "t12345",
            "visitor_profile_id": "507f1f77bcf86cd799439012",
            "department_id": "d12345",
            "host_id": "h12345",
            "receptionist_id": "r12345",
            "appointment_id": None,
            "privacy_notice_version_id": None,
            "check_in_method": "manual_entry",
            "check_out_method": None,
            "verification_status": "unverified",
            "verification_method": None,
            "verified_by": None,
            "status": "checked_in",
            "purpose": "Business meeting",
            "visitor_name_snapshot": "John Doe",
            "company_snapshot": "Acme Corp",
            "host_name_snapshot": "Jane Smith",
            "department_name_snapshot": "Sales",
            "receptionist_name_snapshot": "Mike Johnson",
            "consent_notice_displayed": True,
            "consent_granted": True,
            "consent_method": "digital_signature",
            "consent_timestamp": 1712532000,
            "consent_captured_by_user_id": "r12345",
            "consent_withdrawal_at": None,
            "lawful_basis_at_time": "legitimate_interest",
            "badge_qr_token": "VIS_20240407_1234567890AB",
            "badge_format": "A7",
            "badge_generation_time": 1712532010,
            "badge_expiry": 1712618400,
            "badge_pdf_object_key": "badges/visit_507f1f77bcf86cd799439011.pdf",
            "check_in_time": 1712532000,
            "check_out_time": None,
            "date_created": 1712532000,
            "visit_duration": None,
        },
        "badge_pdf_base64": "JVBERi0xLjQKJeLj...",
        "badge_qr_token": "VIS_20240407_1234567890AB",
    },
    response_codes={
        400: "Invalid session ID, session not found, or invalid status for confirmation",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Visit session not found",
    },
    error_examples={
        400: {
            "success": False,
            "message": "Cannot confirm check-in with status: checked_out",
            "code": "VALIDATION_FAILED",
        },
        404: {
            "success": False,
            "message": "Visit session not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def confirm_check_in_endpoint(
    session_id: str,
    request: ConfirmCheckInRequest,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await confirm_check_in(
        session_id=session_id,
        receptionist_id=principal.user_id,
        tenant_id=tenant_id,
        badge_format=request.badge_format.value if request.badge_format else "A7",
        purpose=request.purpose,
        host_id=request.host_id,
    )


@router.post("/sessions/{session_id}/deny")
@document_response(
    message="Visitor entry denied successfully",
    status_code=status.HTTP_200_OK,
    description="Deny entry to a registered or pending visitor and record the reason.",
    summary="Deny visitor entry",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "department_id": "d12345",
        "host_id": "h12345",
        "receptionist_id": "r12345",
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "manual_entry",
        "check_out_method": None,
        "verification_status": "unverified",
        "verification_method": None,
        "verified_by": None,
        "status": "denied",
        "purpose": "Business meeting",
        "visitor_name_snapshot": "John Doe",
        "company_snapshot": "Acme Corp",
        "host_name_snapshot": "Jane Smith",
        "department_name_snapshot": "Sales",
        "receptionist_name_snapshot": "Mike Johnson",
        "consent_notice_displayed": True,
        "consent_granted": True,
        "consent_method": "digital_signature",
        "consent_timestamp": 1712532000,
        "consent_captured_by_user_id": "r12345",
        "consent_withdrawal_at": None,
        "lawful_basis_at_time": "legitimate_interest",
        "badge_qr_token": None,
        "badge_format": None,
        "badge_generation_time": None,
        "badge_expiry": None,
        "badge_pdf_object_key": None,
        "denial_reason": "Name not on approved list",
        "denied_by": "r12345",
        "check_in_time": 1712532000,
        "check_out_time": None,
        "date_created": 1712532000,
        "visit_duration": None,
    },
    response_codes={
        400: "Invalid session ID or invalid status for denial",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Visit session not found",
    },
    error_examples={
        400: {
            "success": False,
            "message": "Cannot deny visitor with status: checked_out",
            "code": "VALIDATION_FAILED",
        },
        404: {
            "success": False,
            "message": "Visit session not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def deny_visitor_endpoint(
    session_id: str,
    request: DenyVisitorRequest,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await deny_visitor(
        session_id=session_id,
        reason=request.reason,
        denied_by=principal.user_id,
        tenant_id=tenant_id,
    )


@router.post("/verify/id-scan")
@document_response(
    message="ID scan verification completed",
    status_code=status.HTTP_200_OK,
    description="Upload an ID image for OCR-based identity verification. Returns extracted fields with confidence scores.",
    summary="Verify visitor identity via ID scan",
    success_example={
        "full_name": "John Doe",
        "id_number": "A12345678",
        "id_type": "national_id",
        "confidence": 0.95,
    },
    response_codes={
        400: "Invalid image or OCR extraction failed",
        401: "Unauthorized",
        503: "OCR service unavailable",
    },
)
async def verify_id_scan(
    id_image_object_key: str = Query(
        ..., description="Storage object key of the uploaded ID image"
    ),
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    """Extract identity information from uploaded ID via OCR."""
    return await verify_id_with_ocr(id_image_object_key)


@router.post("/sessions/{session_id}/apply-id-scan")
@document_response(
    message="ID scan results applied to session",
    status_code=status.HTTP_200_OK,
    description="Apply OCR-extracted ID scan results to a visit session and update the linked visitor profile.",
    summary="Apply ID scan verification results",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "department_id": "d12345",
        "host_id": "h12345",
        "receptionist_id": "r12345",
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "manual_entry",
        "check_out_method": None,
        "verification_status": "verified",
        "verification_method": "id_scan",
        "verified_by": None,
        "status": "registered",
        "purpose": "Business meeting",
        "visitor_name_snapshot": "John Doe",
        "company_snapshot": "Acme Corp",
        "host_name_snapshot": "Jane Smith",
        "department_name_snapshot": "Sales",
        "receptionist_name_snapshot": "Mike Johnson",
        "consent_notice_displayed": True,
        "consent_granted": True,
        "consent_method": "digital_signature",
        "consent_timestamp": 1712532000,
        "consent_captured_by_user_id": "r12345",
        "consent_withdrawal_at": None,
        "lawful_basis_at_time": "legitimate_interest",
        "badge_qr_token": None,
        "badge_format": None,
        "badge_generation_time": None,
        "badge_expiry": None,
        "badge_pdf_object_key": None,
        "check_in_time": 1712532000,
        "check_out_time": None,
        "date_created": 1712532000,
        "visit_duration": None,
    },
    response_codes={
        400: "Invalid session ID or scan data",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Visit session not found",
    },
    error_examples={
        400: {
            "success": False,
            "message": "Invalid session ID format",
            "code": "VALIDATION_FAILED",
        },
        404: {
            "success": False,
            "message": "Visit session not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def apply_id_scan_endpoint(
    session_id: str,
    request: ApplyIdScanRequest,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await apply_id_scan_verification(
        session_id=session_id,
        tenant_id=tenant_id,
        id_type=request.id_type,
        id_number=request.id_number,
        id_image_object_key=request.id_image_object_key or "",
    )


@router.patch("/sessions/{session_id}/update-draft")
@document_response(
    message="Draft registration updated",
    description="Update a draft (REGISTERED status) visit session with additional fields. Used by receptionists to complete incomplete registrations.",
    summary="Resume and update draft registration",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "department_id": "d12345",
        "host_id": "h12345",
        "receptionist_id": "r12345",
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "manual_entry",
        "check_out_method": None,
        "verification_status": "unverified",
        "verification_method": None,
        "verified_by": None,
        "status": "registered",
        "purpose": "Business meeting",
        "visitor_name_snapshot": "John Doe",
        "company_snapshot": "Acme Corp",
        "host_name_snapshot": "Jane Smith",
        "department_name_snapshot": "Sales",
        "receptionist_name_snapshot": "Mike Johnson",
        "consent_notice_displayed": True,
        "consent_granted": True,
        "consent_method": "digital_signature",
        "consent_timestamp": 1712532000,
        "consent_captured_by_user_id": "r12345",
        "consent_withdrawal_at": None,
        "lawful_basis_at_time": "legitimate_interest",
        "badge_qr_token": None,
        "badge_format": None,
        "badge_generation_time": None,
        "badge_expiry": None,
        "badge_pdf_object_key": None,
        "check_in_time": 1712532000,
        "check_out_time": None,
        "date_created": 1712532000,
        "visit_duration": None,
    },
    response_codes={
        400: "Session is not in draft status",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Session not found",
    },
    error_examples={
        400: {
            "success": False,
            "message": "Can only update draft sessions (status: REGISTERED). Current status: checked_in",
            "code": "VALIDATION_FAILED",
        },
        404: {
            "success": False,
            "message": "Visit session not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def update_draft_session(
    session_id: str,
    update_data: VisitSessionUpdate,
    principal: AuthPrincipal = Depends(_checkin_roles),
):
    """Update a REGISTERED session with missing fields (receptionist resumes draft)."""
    tenant_id = principal.tenant_id or ""
    return await resume_draft_registration(session_id, tenant_id, update_data)


@router.post("/sessions/{session_id}/host-approve")
@document_response(
    message="Visitor approved by host",
    status_code=status.HTTP_200_OK,
    description="Host approves a visitor's entry. Sets verification to VERIFIED with HOST_APPROVAL method.",
    summary="Host approves visitor entry",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "t12345",
        "visitor_profile_id": "507f1f77bcf86cd799439012",
        "department_id": "d12345",
        "host_id": "h12345",
        "receptionist_id": "r12345",
        "appointment_id": None,
        "privacy_notice_version_id": None,
        "check_in_method": "manual_entry",
        "check_out_method": None,
        "verification_status": "verified",
        "verification_method": "host_approval",
        "verified_by": "h12345",
        "status": "registered",
        "purpose": "Business meeting",
        "visitor_name_snapshot": "John Doe",
        "company_snapshot": "Acme Corp",
        "host_name_snapshot": "Jane Smith",
        "department_name_snapshot": "Sales",
        "receptionist_name_snapshot": "Mike Johnson",
        "consent_notice_displayed": True,
        "consent_granted": True,
        "consent_method": "digital_signature",
        "consent_timestamp": 1712532000,
        "consent_captured_by_user_id": "r12345",
        "consent_withdrawal_at": None,
        "lawful_basis_at_time": "legitimate_interest",
        "badge_qr_token": None,
        "badge_format": None,
        "badge_generation_time": None,
        "badge_expiry": None,
        "badge_pdf_object_key": None,
        "check_in_time": 1712532000,
        "check_out_time": None,
        "date_created": 1712532000,
        "visit_duration": None,
    },
    response_codes={
        400: "Invalid session ID",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Visit session or host not found",
    },
    error_examples={
        400: {
            "success": False,
            "message": "Invalid session ID format",
            "code": "VALIDATION_FAILED",
        },
        404: {
            "success": False,
            "message": "Visit session not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def host_approve_endpoint(
    session_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await approve_visitor_by_host(
        session_id=session_id,
        host_id=principal.user_id,
        tenant_id=tenant_id,
    )


@router.get("/sessions/{session_id}/badge")
@document_response(
    message="Badge PDF retrieved",
    description="Download the visitor badge PDF for a specific session.",
    summary="Download visitor badge PDF",
    response_codes={
        404: "Session or badge not found",
        503: "Storage service unavailable",
    },
)
async def download_badge(
    session_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    """Download badge PDF from storage."""
    tenant_id = principal.tenant_id or ""
    pdf_bytes = await download_badge_pdf(session_id, tenant_id)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=badge_{session_id}.pdf"},
    )
