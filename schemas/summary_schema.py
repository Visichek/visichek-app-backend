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


class AppointmentBriefSummary(BaseModel):
    id: str
    purpose: Optional[str] = None
    status: Optional[str] = None
    scheduled_datetime: Optional[int] = None


class BranchBriefSummary(BaseModel):
    id: str
    name: Optional[str] = None
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
