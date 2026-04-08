from __future__ import annotations

import time
from bson import ObjectId
from fastapi import HTTPException

from repositories.visit_session_repo import create_visit_session, get_visit_session, update_visit_session
from repositories.tenant_repo import get_tenant
from repositories.department_repo import get_department, get_departments
from repositories.appointment_repo import get_appointment, update_appointment
from repositories.privacy_notice_repo import get_active_notice_for_tenant
from repositories.visitor_profile_repo import increment_visitor_profile_visits
from schemas.appointment_schema import AppointmentUpdate
from schemas.public_registration_schema import (
    PublicAppointmentLookupOut,
    PublicCheckoutResponse,
    PublicDepartmentOut,
    PublicPrivacyNoticeOut,
    PublicRegistrationRequest,
    PublicRegistrationResponse,
    PublicTenantInfoOut,
)
from schemas.imports import VisitStatus, CheckInMethod, CheckOutMethod, AppointmentStatus
from schemas.visit_session_schema import VisitSessionCreate, VisitSessionUpdate
from services.visitor_profile_service import get_or_create_visitor_profile
from services.qr_service import verify_badge_token


async def register_visitor_public(
    tenant_id: str,
    request: PublicRegistrationRequest,
) -> PublicRegistrationResponse:
    """Public visitor self-registration (no auth). Creates profile + REGISTERED session."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")

    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Consent enforcement based on tenant's lawful basis
    from schemas.imports import LawfulBasis
    lawful_basis = None
    consent_granted = None
    consent_method = None
    consent_timestamp_val = None

    if hasattr(tenant, 'lawful_basis') and tenant.lawful_basis:
        try:
            lawful_basis = LawfulBasis(tenant.lawful_basis)
        except (ValueError, KeyError):
            lawful_basis = None

    if lawful_basis == LawfulBasis.CONSENT:
        if not getattr(request, 'consent_granted', None):
            raise HTTPException(
                status_code=400,
                detail="Consent is required for registration. Please accept the privacy notice.",
            )
        consent_granted = True
        consent_method = getattr(request, 'consent_method', None) or "digital_acceptance"
        consent_timestamp_val = int(time.time())

    # Get or create visitor profile
    profile = await get_or_create_visitor_profile(
        tenant_id=tenant_id,
        phone=request.phone,
        full_name=request.full_name,
        company=request.company,
    )

    # Increment visit count
    await increment_visitor_profile_visits({"_id": ObjectId(profile.id)})

    # Resolve department
    department_name = None
    department_id = request.department_id
    if department_id and ObjectId.is_valid(department_id):
        dept = await get_department({"_id": ObjectId(department_id), "tenant_id": tenant_id})
        if dept:
            department_name = dept.name

    # Handle appointment linkage
    host_id = None
    host_name = None
    appointment_id = request.appointment_id
    if appointment_id and ObjectId.is_valid(appointment_id):
        appointment = await get_appointment({"_id": ObjectId(appointment_id), "tenant_id": tenant_id})
        if appointment and appointment.status == AppointmentStatus.SCHEDULED.value:
            host_id = appointment.host_id if hasattr(appointment, 'host_id') else None
            if not department_id and hasattr(appointment, 'department_id'):
                department_id = appointment.department_id
            await update_appointment(
                {"_id": ObjectId(appointment_id)},
                AppointmentUpdate(status=AppointmentStatus.FULFILLED),
            )

    # Create visit session with REGISTERED status
    session_data = VisitSessionCreate(
        tenant_id=tenant_id,
        visitor_profile_id=profile.id,
        department_id=department_id or "",
        host_id=host_id,
        appointment_id=appointment_id,
        check_in_method=CheckInMethod.QR,
        status=VisitStatus.REGISTERED,
        purpose=request.purpose,
        visitor_name_snapshot=profile.full_name,
        company_snapshot=profile.company,
        department_name_snapshot=department_name,
        consent_notice_displayed=True if getattr(request, 'privacy_notice_version_id', None) else False,
        consent_granted=consent_granted,
        consent_method=consent_method,
        consent_timestamp=consent_timestamp_val,
        privacy_notice_version_id=getattr(request, 'privacy_notice_version_id', None),
        lawful_basis_at_time=lawful_basis,
    )
    session = await create_visit_session(session_data)

    return PublicRegistrationResponse(
        session_id=session.id or "",
        visitor_profile_id=profile.id or "",
        status=session.status,
        message="Registration successful. Please proceed to reception.",
    )


async def checkout_visitor_public(badge_qr_token: str) -> PublicCheckoutResponse:
    """Public self-checkout via badge QR token (no auth)."""
    session_id = verify_badge_token(badge_qr_token)
    if not session_id:
        raise HTTPException(status_code=400, detail="Invalid or expired badge QR token")

    session = await get_visit_session({"_id": ObjectId(session_id)})
    if not session:
        raise HTTPException(status_code=404, detail="Visit session not found")

    if session.status not in (VisitStatus.CHECKED_IN.value, "checked_in"):
        raise HTTPException(status_code=400, detail=f"Visitor is not currently checked in (status: {session.status})")

    checkout_time = int(time.time())
    updated = await update_visit_session(
        {"_id": ObjectId(session_id)},
        VisitSessionUpdate(
            status=VisitStatus.CHECKED_OUT,
            check_out_method=CheckOutMethod.QR_SCAN,
            check_out_time=checkout_time,
        ),
    )

    visit_duration = None
    if updated.check_in_time:
        visit_duration = checkout_time - updated.check_in_time

    return PublicCheckoutResponse(
        session_id=updated.id or "",
        status=updated.status,
        visit_duration=visit_duration,
    )


async def list_public_departments(tenant_id: str) -> list[PublicDepartmentOut]:
    departments = await get_departments({"tenant_id": tenant_id, "is_active": True})
    return [PublicDepartmentOut(id=d.id or "", name=d.name) for d in departments]


async def get_public_tenant_info(tenant_id: str) -> PublicTenantInfoOut:
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")

    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return PublicTenantInfoOut(
        tenant_id=tenant.id or "",
        company_name=tenant.company_name,
    )


async def get_public_privacy_notice(tenant_id: str) -> PublicPrivacyNoticeOut:
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID")

    notice = await get_active_notice_for_tenant(tenant_id)
    if not notice:
        return PublicPrivacyNoticeOut(
            notice_id=None,
            title="No privacy notice available",
            content="",
            version=None,
        )

    return PublicPrivacyNoticeOut(
        notice_id=notice.id,
        title=getattr(notice, "title", "Privacy Notice"),
        content=getattr(notice, "content", ""),
        version=getattr(notice, "version", None),
    )


async def lookup_public_appointment(
    tenant_id: str,
    appointment_id: str,
) -> PublicAppointmentLookupOut:
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(status_code=400, detail="Invalid appointment ID")

    appointment = await get_appointment(
        {"_id": ObjectId(appointment_id), "tenant_id": tenant_id, "status": "scheduled"},
    )
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found or not in scheduled status")

    return PublicAppointmentLookupOut(
        appointment_id=appointment.id or "",
        host_id=getattr(appointment, "host_id", None),
        host_name=getattr(appointment, "host_name", None),
        department_id=getattr(appointment, "department_id", None),
        department_name=getattr(appointment, "department_name", None),
        scheduled_at=getattr(appointment, "scheduled_at", None),
        purpose=getattr(appointment, "purpose", None),
    )
