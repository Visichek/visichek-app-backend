from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr


class PublicRegistrationRequest(BaseModel):
    full_name: str
    phone: str
    company: Optional[str] = None
    email: Optional[EmailStr] = None
    purpose: Optional[str] = None
    department_id: Optional[str] = None
    appointment_id: Optional[str] = None
    consent_granted: Optional[bool] = None
    consent_method: Optional[str] = None
    privacy_notice_version_id: Optional[str] = None


class PublicRegistrationResponse(BaseModel):
    session_id: str
    visitor_profile_id: str
    status: str
    message: str


class PublicDepartmentOut(BaseModel):
    id: str
    name: str


class PublicTenantInfoOut(BaseModel):
    tenant_id: str
    company_name: str


class PublicPrivacyNoticeOut(BaseModel):
    notice_id: Optional[str] = None
    title: str
    content: str
    version: Optional[str] = None


class PublicCheckoutResponse(BaseModel):
    session_id: str
    status: str
    visit_duration: Optional[int] = None


class PublicAppointmentLookupOut(BaseModel):
    appointment_id: str
    host_id: Optional[str] = None
    host_name: Optional[str] = None
    department_id: Optional[str] = None
    department_name: Optional[str] = None
    scheduled_at: Optional[int] = None
    purpose: Optional[str] = None
