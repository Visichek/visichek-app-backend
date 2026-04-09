"""Resolver helpers that fetch brief summaries for foreign-key IDs.

When an API response references another entity by ID, services use the
resolvers in this module to fetch a small snapshot of that entity and
embed it alongside the ID. This avoids forcing the frontend to make
follow-up requests just to render names, statuses, or other commonly
displayed fields.

The summary models themselves live in ``schemas/summary_schema.py`` so
schemas can reference them without importing services (which would
introduce a circular dependency through repositories).

Each resolver returns ``None`` on missing/invalid IDs and never raises —
enrichment is best-effort and must not break a primary response.
"""

from __future__ import annotations

from typing import Optional

from bson import ObjectId

from schemas.summary_schema import (
    AppointmentBriefSummary,
    BranchBriefSummary,
    DepartmentBriefSummary,
    InvoiceBriefSummary,
    PlanBriefSummary,
    SubscriptionBriefSummary,
    TenantBriefSummary,
    UserBriefSummary,
    VisitSessionBriefSummary,
    VisitorProfileBriefSummary,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_object_id(value: Optional[str]) -> Optional[ObjectId]:
    if not value:
        return None
    try:
        return ObjectId(value)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Resolver functions — best-effort, return None on any failure.
# ---------------------------------------------------------------------------


async def resolve_tenant_summary(tenant_id: Optional[str]) -> Optional[TenantBriefSummary]:
    oid = _to_object_id(tenant_id)
    if oid is None:
        return None
    try:
        from repositories.tenant_repo import get_tenant
        tenant = await get_tenant({"_id": oid})
        if not tenant:
            return None
        return TenantBriefSummary(
            id=tenant.id,
            company_name=tenant.company_name,
            is_active=tenant.is_active,
            country_of_hosting=tenant.country_of_hosting,
        )
    except Exception:
        return None


async def resolve_plan_summary(plan_id: Optional[str]) -> Optional[PlanBriefSummary]:
    oid = _to_object_id(plan_id)
    if oid is None:
        return None
    try:
        from repositories.plan_repo import get_plan
        plan = await get_plan({"_id": oid})
        if not plan:
            return None
        return PlanBriefSummary(
            id=plan.id,
            name=plan.name,
            display_name=plan.display_name,
            tier=plan.tier,
        )
    except Exception:
        return None


async def resolve_subscription_summary(
    subscription_id: Optional[str],
) -> Optional[SubscriptionBriefSummary]:
    oid = _to_object_id(subscription_id)
    if oid is None:
        return None
    try:
        from repositories.subscription_repo import get_subscription
        sub = await get_subscription({"_id": oid})
        if not sub:
            return None
        return SubscriptionBriefSummary(
            id=sub.id,
            status=sub.status,
            billing_cycle=sub.billing_cycle,
            plan_id=sub.plan_id,
            current_period_end=sub.current_period_end,
        )
    except Exception:
        return None


async def resolve_department_summary(
    department_id: Optional[str],
) -> Optional[DepartmentBriefSummary]:
    oid = _to_object_id(department_id)
    if oid is None:
        return None
    try:
        from repositories.department_repo import get_department
        dept = await get_department({"_id": oid})
        if not dept:
            return None
        return DepartmentBriefSummary(
            id=dept.id,
            name=dept.name,
            code=dept.code,
            is_active=dept.is_active,
        )
    except Exception:
        return None


async def resolve_system_user_summary(user_id: Optional[str]) -> Optional[UserBriefSummary]:
    oid = _to_object_id(user_id)
    if oid is None:
        return None
    try:
        from repositories.system_user_repo import get_system_user
        user = await get_system_user({"_id": oid})
        if not user:
            return None
        return UserBriefSummary(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            role=user.role,
            user_type="system_user",
        )
    except Exception:
        return None


async def resolve_admin_summary(admin_id: Optional[str]) -> Optional[UserBriefSummary]:
    if not admin_id:
        return None
    try:
        from repositories.admin_repo import get_admin
        # admin_repo accepts either ObjectId or the static super-admin string id
        oid = _to_object_id(admin_id)
        admin = await get_admin({"_id": oid} if oid else {"_id": admin_id})
        if not admin:
            return None
        return UserBriefSummary(
            id=admin.id,
            full_name=admin.full_name,
            email=admin.email,
            role="admin",
            user_type="admin",
        )
    except Exception:
        return None


async def resolve_user_summary(
    user_id: Optional[str], user_type: Optional[str] = None
) -> Optional[UserBriefSummary]:
    """Resolve a user where the type may be either "admin" or "system_user".

    If ``user_type`` is unknown, tries system_user first then admin.
    """
    if not user_id:
        return None
    if user_type == "admin":
        return await resolve_admin_summary(user_id)
    if user_type == "system_user":
        return await resolve_system_user_summary(user_id)
    summary = await resolve_system_user_summary(user_id)
    if summary is not None:
        return summary
    return await resolve_admin_summary(user_id)


async def resolve_visitor_profile_summary(
    visitor_profile_id: Optional[str],
) -> Optional[VisitorProfileBriefSummary]:
    oid = _to_object_id(visitor_profile_id)
    if oid is None:
        return None
    try:
        from repositories.visitor_profile_repo import get_visitor_profile
        # Pass an explicit deleted_at filter that allows soft-deleted records
        # so we can still resolve names for historical references.
        profile = await get_visitor_profile({"_id": oid, "deleted_at": {"$in": [None]}})
        if not profile:
            # Retry without the deleted-at restriction
            profile = await get_visitor_profile({"_id": oid})
        if not profile:
            return None
        return VisitorProfileBriefSummary(
            id=profile.id,
            full_name=profile.full_name,
            phone=profile.phone,
            email_address=profile.email_address,
            company=profile.company,
        )
    except Exception:
        return None


async def resolve_appointment_summary(
    appointment_id: Optional[str],
) -> Optional[AppointmentBriefSummary]:
    oid = _to_object_id(appointment_id)
    if oid is None:
        return None
    try:
        from repositories.appointment_repo import get_appointment
        appt = await get_appointment({"_id": oid})
        if not appt:
            return None
        return AppointmentBriefSummary(
            id=appt.id,
            purpose=appt.purpose,
            status=appt.status,
            scheduled_datetime=appt.scheduled_datetime,
        )
    except Exception:
        return None


async def resolve_branch_summary(branch_id: Optional[str]) -> Optional[BranchBriefSummary]:
    oid = _to_object_id(branch_id)
    if oid is None:
        return None
    try:
        from repositories.branch_repo import get_branch
        branch = await get_branch({"_id": oid})
        if not branch:
            return None
        return BranchBriefSummary(
            id=branch.id,
            name=branch.name,
            is_active=getattr(branch, "is_active", None),
        )
    except Exception:
        return None


async def resolve_visit_session_summary(
    session_id: Optional[str],
) -> Optional[VisitSessionBriefSummary]:
    oid = _to_object_id(session_id)
    if oid is None:
        return None
    try:
        from repositories.visit_session_repo import get_visit_session
        session = await get_visit_session({"_id": oid})
        if not session:
            return None
        return VisitSessionBriefSummary(
            id=session.id,
            status=session.status,
            visitor_name_snapshot=session.visitor_name_snapshot,
            check_in_time=session.check_in_time,
        )
    except Exception:
        return None


async def resolve_invoice_summary(invoice_id: Optional[str]) -> Optional[InvoiceBriefSummary]:
    oid = _to_object_id(invoice_id)
    if oid is None:
        return None
    try:
        from repositories.invoice_repo import get_invoice
        invoice = await get_invoice({"_id": oid})
        if not invoice:
            return None
        return InvoiceBriefSummary(
            id=invoice.id,
            invoice_number=invoice.invoice_number,
            status=invoice.status,
            total_minor=invoice.total_minor,
            currency=invoice.currency,
        )
    except Exception:
        return None
