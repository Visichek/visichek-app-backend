from __future__ import annotations

from fastapi import APIRouter, status

from core.response_envelope import document_response
from schemas.public_registration_schema import PublicRegistrationRequest
from services.public_registration_service import (
    checkout_visitor_public,
    get_public_privacy_notice,
    get_public_tenant_info,
    list_public_departments,
    lookup_public_appointment,
    register_visitor_public,
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
async def public_register_endpoint(tenant_id: str, request: PublicRegistrationRequest):
    return await register_visitor_public(tenant_id=tenant_id, request=request)


@router.get("/register/{tenant_id}/departments")
@document_response(
    message="Departments retrieved",
    description="List active departments for a tenant's public registration form.",
    summary="List departments for public registration",
    success_example=[{"id": "dept123", "name": "Engineering"}, {"id": "dept456", "name": "Sales"}],
)
async def list_tenant_departments_public_endpoint(tenant_id: str):
    return await list_public_departments(tenant_id=tenant_id)


@router.get("/register/{tenant_id}/info")
@document_response(
    message="Tenant info retrieved",
    description="Return basic tenant info and branding for the public registration UI.",
    summary="Get tenant info for public registration",
    success_example={"tenant_id": "t123", "company_name": "Acme Corp"},
)
async def get_tenant_info_public_endpoint(tenant_id: str):
    return await get_public_tenant_info(tenant_id=tenant_id)


@router.get("/register/{tenant_id}/privacy-notice")
@document_response(
    message="Privacy notice retrieved",
    description="Return the active privacy notice text for the tenant. Must be displayed before data capture.",
    summary="Get privacy notice for public registration",
    success_example={"notice_id": "n123", "title": "Privacy Notice", "content": "We collect your data for...", "version": "1.0"},
)
async def get_privacy_notice_public_endpoint(tenant_id: str):
    return await get_public_privacy_notice(tenant_id=tenant_id)


@router.post("/checkout")
@document_response(
    message="Visitor checked out successfully",
    description="Public self-service check-out via badge QR token. No authentication required.",
    summary="Public visitor self-checkout",
    success_example={"session_id": "507f1f77bcf86cd799439011", "status": "checked_out", "visit_duration": 3600},
    response_codes={400: "Invalid or expired QR token", 404: "Session not found"},
)
async def public_checkout_endpoint(badge_qr_token: str):
    return await checkout_visitor_public(badge_qr_token=badge_qr_token)


@router.get("/register/{tenant_id}/appointment/{appointment_id}")
@document_response(
    message="Appointment details retrieved",
    description="Look up a scheduled appointment to pre-fill the registration form.",
    summary="Public appointment lookup",
    success_example={"appointment_id": "a123", "host_name": "Jane Smith", "department_name": "Sales", "scheduled_at": 1712000000},
    response_codes={404: "Appointment not found or not scheduled"},
)
async def lookup_appointment_public_endpoint(tenant_id: str, appointment_id: str):
    return await lookup_public_appointment(tenant_id=tenant_id, appointment_id=appointment_id)
