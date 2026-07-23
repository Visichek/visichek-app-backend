"""Lightweight ``*BriefSummary`` models embedded inside other ``*Out`` schemas.

These deliberately mirror only the smallest field set the frontend needs to
render a label/avatar/status alongside an ID. They live in the schema layer
so other schemas can reference them without pulling in the service layer
(which would create circular imports via repositories).

Resolver functions that build instances of these models live in
``services/summary_resolver.py``.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TenantBriefSummary(BaseModel):
    id: str
    company_name: Optional[str] = None
    is_active: Optional[bool] = None
    country_of_hosting: Optional[str] = None


class PlanBriefSummary(BaseModel):
    id: str
    name: Optional[str] = None
    display_name: Optional[str] = None
    tier: Optional[str] = None


class SubscriptionBriefSummary(BaseModel):
    id: str
    status: Optional[str] = None
    billing_cycle: Optional[str] = None
    plan_id: Optional[str] = None
    current_period_end: Optional[int] = None


class DepartmentBriefSummary(BaseModel):
    id: str
    name: Optional[str] = None
    code: Optional[str] = None
    is_active: Optional[bool] = None


class UserBriefSummary(BaseModel):
    """Brief snapshot for any user — application admin or system user."""

    id: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    user_type: Optional[str] = None  # "admin" or "system_user"


class VisitorProfileBriefSummary(BaseModel):
    id: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email_address: Optional[str] = None
    company: Optional[str] = None


class ManualVerificationInfo(BaseModel):
    """Attribution snapshot for a staff-vouched (manual) verification.

    Recorded on BOTH the ``checkins`` row and the linked ``visitors`` row
    when a staff member marks a visitor verified by hand (walk-in, skipped
    scan, OCR failure). ``verified_by_name`` / ``verified_by_role`` are
    denormalized point-in-time snapshots so the visitors list can render
    "Verified by Ada Receptionist (Receptionist) · 2h ago" without a second
    lookup per row; they intentionally do NOT track later staff renames."""

    manual: bool = True
    verified_by_user_id: str
    verified_by_name: Optional[str] = None
    verified_by_role: Optional[str] = None
    verified_at: int
    method: str = "manual"
    notes: Optional[str] = None


class VisitorBriefSummary(BaseModel):
    """Snapshot of a ``visitors`` collection record embedded on a check-in
    payload so the receptionist / approver can cross-check the visitor's
    identity and spot data-entry errors (spelling, wrong number) without a
    second request.

    This intentionally mirrors only the fields needed for the approval UI:
    who the visitor claims to be, how to reach them, whether they've been
    ID-verified, and the portrait captured during verification."""

    id: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    verified: bool = False
    verification_method: Optional[str] = None
    portrait_url: Optional[str] = None
    # Present only when a staff member has manually verified the visitor;
    # null/absent for id_scan-verified or unverified visitors.
    manual_verification: Optional[ManualVerificationInfo] = None
    created_at: Optional[int] = None
    last_visit_at: Optional[int] = None


class AppointmentBriefSummary(BaseModel):
    id: str
    purpose: Optional[str] = None
    status: Optional[str] = None
    scheduled_datetime: Optional[int] = None


class BranchBriefSummary(BaseModel):
    id: str
    name: Optional[str] = None
    is_active: Optional[bool] = None


class ContactBriefSummary(BaseModel):
    """Point-of-contact card for a branch or organization.

    Resolution order (see ``services.summary_resolver.resolve_contact_summary``):
    a designated contact system_user -> the branch's own email/phone as a
    synthetic contact -> the tenant's main super admin. ``source`` says which
    rung produced the card ("user" | "branch" | "main_super_admin").

    ``phone`` can only come from ``branch.phone`` — system_users have no
    phone field.
    """

    user_id: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    source: str


class HostBriefSummary(BaseModel):
    id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    department_id: Optional[str] = None
    picture_image_url: Optional[str] = None
    is_active: Optional[bool] = None


class VisitSessionBriefSummary(BaseModel):
    id: str
    status: Optional[str] = None
    visitor_name_snapshot: Optional[str] = None
    check_in_time: Optional[int] = None


class InvoiceBriefSummary(BaseModel):
    id: str
    invoice_number: Optional[str] = None
    status: Optional[str] = None
    total_minor: Optional[int] = None
    currency: Optional[str] = None


class DiscountBriefSummary(BaseModel):
    id: str
    code: Optional[str] = None
    name: Optional[str] = None
    discount_type: Optional[str] = None
    value: Optional[float] = None
    status: Optional[str] = None
