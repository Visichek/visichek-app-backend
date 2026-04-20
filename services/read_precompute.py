"""Precompute loaders for read-only dashboard / aggregation endpoints.

These resources are refreshed by worker-precompute on the schedule
fan-out. Heavy aggregations (admin billing, top-tenant rollups) get
longer TTLs; real-time views (active visitors) get shorter ones via
the route-level ``ttl`` arg to ``get_or_compute``.
"""

from __future__ import annotations

import time
from typing import Any, List

from core.queue.precompute import PrecomputeScope, register_precompute


# ─── Tenant dashboard ─────────────────────────────────────────────────


@register_precompute("dashboard.stats", scope=PrecomputeScope.TENANT)
async def _precompute_dashboard_stats(tenant_id: str) -> Any:
    from services.dashboard_service import get_dashboard_stats

    result = await get_dashboard_stats(tenant_id=tenant_id)
    return (
        result.model_dump(mode="json", by_alias=True)
        if hasattr(result, "model_dump")
        else result
    )


@register_precompute("dashboard.visitors_active", scope=PrecomputeScope.TENANT)
async def _precompute_dashboard_visitors_active(tenant_id: str) -> List[Any]:
    from services.visit_session_service import retrieve_active_visitors

    sessions = await retrieve_active_visitors(tenant_id=tenant_id)
    return [
        s.model_dump(mode="json", by_alias=True) if hasattr(s, "model_dump") else s
        for s in sessions
    ]


@register_precompute("dashboard.visitors_page1", scope=PrecomputeScope.TENANT)
async def _precompute_dashboard_visitors_page1(tenant_id: str) -> Any:
    from services.dashboard_service import get_visitor_log

    result = await get_visitor_log(tenant_id=tenant_id, start=0, stop=100)
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        return [
            r.model_dump(mode="json", by_alias=True) if hasattr(r, "model_dump") else r
            for r in result
        ]
    return result


# ─── Application admin dashboard ──────────────────────────────────────


@register_precompute("admin_dashboard.stats", scope=PrecomputeScope.GLOBAL)
async def _precompute_admin_dashboard_stats(_tenant_id: str) -> Any:
    from services.admin_dashboard_service import get_admin_dashboard_stats

    result = await get_admin_dashboard_stats()
    return (
        result.model_dump(mode="json", by_alias=True)
        if hasattr(result, "model_dump")
        else result
    )


@register_precompute("admin_dashboard.billing_30d", scope=PrecomputeScope.GLOBAL)
async def _precompute_admin_dashboard_billing_30d(_tenant_id: str) -> Any:
    from services.billing_report_service import get_billing_summary

    now = int(time.time())
    start_date = now - (30 * 86400)
    result = await get_billing_summary(start_date=start_date, end_date=now)
    return (
        result.model_dump(mode="json", by_alias=True)
        if hasattr(result, "model_dump")
        else result
    )


@register_precompute("admin_dashboard.discrepancies", scope=PrecomputeScope.GLOBAL)
async def _precompute_admin_dashboard_discrepancies(_tenant_id: str) -> Any:
    from services.billing_report_service import get_payment_discrepancies

    result = await get_payment_discrepancies()
    if isinstance(result, list):
        return [
            r.model_dump(mode="json", by_alias=True) if hasattr(r, "model_dump") else r
            for r in result
        ]
    return result


# ─── Audit ────────────────────────────────────────────────────────────


@register_precompute("audit.recent", scope=PrecomputeScope.TENANT)
async def _precompute_audit_recent(tenant_id: str) -> Any:
    from repositories.audit_log_repo import count_audit_logs
    from services.audit_service import retrieve_audit_logs_with_summary

    filter_dict = {"tenant_id": tenant_id}
    logs = await retrieve_audit_logs_with_summary(filter_dict, start=0, stop=100)
    total = await count_audit_logs(filter_dict)
    return {
        "items": [
            log.model_dump(mode="json", by_alias=True)
            if hasattr(log, "model_dump")
            else log
            for log in logs
        ],
        "total": total,
    }


# ─── Usage ────────────────────────────────────────────────────────────


@register_precompute("usage.my_usage", scope=PrecomputeScope.TENANT)
async def _precompute_usage_my_usage(tenant_id: str) -> Any:
    from services.plan_cache_service import resolve_tenant_plan
    from services.usage_service import get_tenant_usage_summary

    if not tenant_id:
        return None
    plan_data = await resolve_tenant_plan(tenant_id)
    if not plan_data:
        return {"error": "No active subscription for your organization"}
    result = await get_tenant_usage_summary(
        tenant_id=tenant_id,
        subscription_id=plan_data.get("subscription_id", ""),
        plan_data=plan_data,
    )
    return (
        result.model_dump(mode="json", by_alias=True)
        if hasattr(result, "model_dump")
        else result
    )


# ─── Invoices ─────────────────────────────────────────────────────────


@register_precompute("invoices.for_tenant", scope=PrecomputeScope.TENANT)
async def _precompute_invoices_for_tenant(tenant_id: str) -> Any:
    from services.invoice_service import retrieve_invoices_for_tenant_with_summary

    invoices, total = await retrieve_invoices_for_tenant_with_summary(
        tenant_id=tenant_id, skip=0, limit=20
    )
    return {
        "items": [
            i.model_dump(mode="json", by_alias=True)
            if hasattr(i, "model_dump")
            else i
            for i in invoices
        ],
        "total": total,
    }


@register_precompute("invoices.admin_list", scope=PrecomputeScope.GLOBAL)
async def _precompute_invoices_admin_list(_tenant_id: str) -> Any:
    from services.invoice_service import retrieve_all_invoices_with_summary

    invoices, total = await retrieve_all_invoices_with_summary(
        skip=0, limit=20, tenant_id=None, status=None
    )
    return {
        "items": [
            i.model_dump(mode="json", by_alias=True)
            if hasattr(i, "model_dump")
            else i
            for i in invoices
        ],
        "total": total,
    }
