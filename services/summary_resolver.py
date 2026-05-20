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

from typing import Iterable, Mapping, Optional

from bson import ObjectId

from schemas.summary_schema import (
    AppointmentBriefSummary,
    BranchBriefSummary,
    DepartmentBriefSummary,
    DiscountBriefSummary,
    HostBriefSummary,
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


async def resolve_tenant_summary(
    tenant_id: Optional[str],
) -> Optional[TenantBriefSummary]:
    oid = _to_object_id(tenant_id)
    if oid is None:
        return None
    try:
        from repositories.tenant_repo import get_tenant

        tenant = await get_tenant({"_id": oid})
        if not tenant:
            return None
        return TenantBriefSummary(
            id=str(tenant.id or ""),
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
            id=str(plan.id or ""),
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
            id=str(sub.id or ""),
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
            id=str(dept.id or ""),
            name=dept.name,
            code=dept.code,
            is_active=dept.is_active,
        )
    except Exception:
        return None


async def resolve_system_user_summary(
    user_id: Optional[str],
) -> Optional[UserBriefSummary]:
    oid = _to_object_id(user_id)
    if oid is None:
        return None
    try:
        from repositories.system_user_repo import get_system_user

        user = await get_system_user({"_id": oid})
        if not user:
            return None
        return UserBriefSummary(
            id=str(user.id or ""),
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
            id=str(admin.id or ""),
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


# ---------------------------------------------------------------------------
# Batch resolvers — resolve a whole page's distinct ids in one query per
# collection instead of one query per row. Used by audit-log enrichment so a
# 25-row page that points at a handful of distinct actors costs a handful of
# lookups, not 25. Best-effort: never raise; unresolved ids are simply absent
# from the returned map and the caller supplies any fallback.
# ---------------------------------------------------------------------------


async def resolve_tenant_summaries_batch(
    tenant_ids: Iterable[Optional[str]],
) -> dict[str, TenantBriefSummary]:
    """Resolve tenant snapshots for many ids with a single ``$in`` query."""
    out: dict[str, TenantBriefSummary] = {}
    oids: list[ObjectId] = []
    seen: set[str] = set()
    for tid in tenant_ids:
        if not tid or tid in seen:
            continue
        seen.add(tid)
        oid = _to_object_id(tid)
        if oid is not None:
            oids.append(oid)
    if not oids:
        return out
    try:
        from core.database import db

        cursor = db.tenant_companies.find({"_id": {"$in": oids}})
        async for doc in cursor:
            tid = str(doc.get("_id"))
            out[tid] = TenantBriefSummary(
                id=tid,
                company_name=doc.get("company_name"),
                is_active=doc.get("is_active"),
                country_of_hosting=doc.get("country_of_hosting"),
            )
    except Exception:
        return out
    return out


async def resolve_user_summaries_batch(
    user_types: Mapping[str, Optional[str]],
) -> dict[str, UserBriefSummary]:
    """Resolve user snapshots for many ids, keyed by id.

    ``user_types`` maps ``user_id -> "admin" | "system_user" | None``. Admin
    ids hit ``admins``; everything else hits ``system_users``. Admin ids not
    found in the batch fall back to :func:`resolve_admin_summary` individually
    so the env-configured static super admin (which has no DB row) still
    resolves. Best-effort: never raises.
    """
    out: dict[str, UserBriefSummary] = {}
    if not user_types:
        return out

    system_oids: list[ObjectId] = []
    admin_ids: list[str] = []
    for uid, utype in user_types.items():
        if not uid:
            continue
        if utype == "admin":
            admin_ids.append(uid)
        else:
            oid = _to_object_id(uid)
            if oid is not None:
                system_oids.append(oid)

    try:
        from core.database import db

        if system_oids:
            cursor = db.system_users.find({"_id": {"$in": system_oids}})
            async for doc in cursor:
                sid = str(doc.get("_id"))
                out[sid] = UserBriefSummary(
                    id=sid,
                    full_name=doc.get("full_name"),
                    email=doc.get("email"),
                    role=doc.get("role"),
                    user_type="system_user",
                )

        if admin_ids:
            admin_oids = [
                oid for oid in (_to_object_id(a) for a in admin_ids) if oid is not None
            ]
            found: set[str] = set()
            if admin_oids:
                cursor = db.admins.find({"_id": {"$in": admin_oids}})
                async for doc in cursor:
                    aid = str(doc.get("_id"))
                    found.add(aid)
                    out[aid] = UserBriefSummary(
                        id=aid,
                        full_name=doc.get("full_name"),
                        email=doc.get("email"),
                        role="admin",
                        user_type="admin",
                    )
            for aid in admin_ids:
                if aid not in found and aid not in out:
                    summary = await resolve_admin_summary(aid)
                    if summary is not None:
                        out[aid] = summary
    except Exception:
        return out
    return out


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
            id=str(profile.id or ""),
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
            id=str(appt.id or ""),
            purpose=appt.purpose,
            status=appt.status,
            scheduled_datetime=appt.scheduled_datetime,
        )
    except Exception:
        return None


async def resolve_branch_summary(
    branch_id: Optional[str],
) -> Optional[BranchBriefSummary]:
    oid = _to_object_id(branch_id)
    if oid is None:
        return None
    try:
        from repositories.branch_repo import get_branch

        branch = await get_branch({"_id": oid})
        if not branch:
            return None
        return BranchBriefSummary(
            id=str(branch.id or ""),
            name=branch.name,
            is_active=getattr(branch, "is_active", None),
        )
    except Exception:
        return None


async def resolve_host_summary(
    host_id: Optional[str],
) -> Optional[HostBriefSummary]:
    oid = _to_object_id(host_id)
    if oid is None:
        return None
    try:
        from repositories.host_repo import get_host

        host = await get_host({"_id": oid})
        if not host:
            return None
        return HostBriefSummary(
            id=str(host.id or ""),
            name=host.name,
            phone=host.phone,
            email=host.email,
            department_id=host.department_id,
            is_active=host.is_active,
        )
    except Exception:
        return None


async def resolve_appointment_host_summary(
    host_id: Optional[str],
) -> Optional[HostBriefSummary]:
    """Resolve an appointment/visit-session ``host_id`` to a host snapshot.

    ``host_id`` may point at either the ``hosts`` collection (the modern
    reference) or — for appointments created before the host rewire — a
    ``system_users`` row. We try the hosts collection first, then fall back
    to mapping a system user into the same :class:`HostBriefSummary` shape so
    the frontend always receives ONE consistent host shape regardless of how
    the appointment was created. Best-effort: returns ``None`` on a missing
    or invalid id.
    """
    oid = _to_object_id(host_id)
    if oid is None:
        return None
    # 1. Modern path — host record.
    host_summary = await resolve_host_summary(host_id)
    if host_summary is not None:
        return host_summary
    # 2. Legacy fallback — host_id is a system_user id.
    try:
        from repositories.system_user_repo import get_system_user

        user = await get_system_user({"_id": oid})
        if not user:
            return None
        return HostBriefSummary(
            id=str(user.id or ""),
            name=user.full_name,
            email=user.email,
            phone=None,
            department_id=None,
            picture_image_url=None,
            is_active=None,
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
            id=str(session.id or ""),
            status=session.status,
            visitor_name_snapshot=session.visitor_name_snapshot,
            check_in_time=session.check_in_time,
        )
    except Exception:
        return None


async def resolve_invoice_summary(
    invoice_id: Optional[str],
) -> Optional[InvoiceBriefSummary]:
    oid = _to_object_id(invoice_id)
    if oid is None:
        return None
    try:
        from repositories.invoice_repo import get_invoice

        invoice = await get_invoice({"_id": oid})
        if not invoice:
            return None
        return InvoiceBriefSummary(
            id=str(invoice.id or ""),
            invoice_number=invoice.invoice_number,
            status=invoice.status,
            total_minor=invoice.total_minor,
            currency=invoice.currency,
        )
    except Exception:
        return None


async def resolve_discount_summary(
    discount_id: Optional[str],
) -> Optional[DiscountBriefSummary]:
    oid = _to_object_id(discount_id)
    if oid is None:
        return None
    try:
        from repositories.discount_repo import get_discount

        discount = await get_discount({"_id": oid})
        if not discount:
            return None
        return DiscountBriefSummary(
            id=str(discount.id or ""),
            code=discount.code,
            name=discount.name,
            discount_type=discount.discount_type.value
            if hasattr(discount.discount_type, "value")
            else discount.discount_type,
            value=discount.value,
            status=discount.status.value
            if hasattr(discount.status, "value")
            else discount.status,
        )
    except Exception:
        return None
