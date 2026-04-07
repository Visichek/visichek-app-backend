from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.visit_session_schema import CheckInRequest, CheckOutRequest
from security.auth import verify_system_user_token, verify_any_system_user_token
from security.principal import AuthPrincipal
from services.visit_session_service import (
    check_in_visitor,
    check_out_visitor,
    retrieve_active_visitors,
    retrieve_visit_session_by_id,
    retrieve_visit_sessions,
)

router = APIRouter(prefix="/visitors", tags=["Visitors"])

_checkin_roles = verify_system_user_token("receptionist", "dept_admin", "super_admin")


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
        400: {"success": False, "message": "Missing required field: purpose", "code": "VALIDATION_FAILED"},
        404: {"success": False, "message": "Department not found", "code": "RESOURCE_NOT_FOUND"},
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
        404: {"success": False, "message": "Visit session not found", "code": "RESOURCE_NOT_FOUND"},
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
    department_id: str = None,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_active_visitors(tenant_id=tenant_id, department_id=department_id)


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
    department_id: str = None,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_system_user_token("dept_admin", "super_admin", "auditor")),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_visit_sessions(
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
        404: {"success": False, "message": "Visit session not found", "code": "RESOURCE_NOT_FOUND"},
    },
)
async def get_visit_session_endpoint(
    session_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_visit_session_by_id(session_id=session_id, tenant_id=tenant_id)
