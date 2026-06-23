from __future__ import annotations

import time
from typing import Annotated, Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from core.csv_export import csv_response
from core.database import db
from core.errors import AppException, ErrorCode
from core.list_params import FilterDef, ListSpec, coerce_bool, parse_list_query
from core.list_runner import run_list
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.response_envelope import document_response
from schemas.admin_schema import AdminOut
from security.account_status_check import check_admin_account_status_and_permissions
from services.admin_dashboard_service import (
    get_admin_attention_queue,
    get_admin_dashboard_stats,
)
from services.admin_insights_service import get_admin_insights
from services.billing_report_service import (
    get_billing_summary,
    get_payment_discrepancies,
)

router = APIRouter(prefix="/admins/dashboard", tags=["Application Admin Dashboard"])


@router.get("/stats")
@document_response(
    message="Admin dashboard stats fetched successfully",
    success_example={
        "total_tenants": 42,
        "active_tenants": 38,
        "total_tenant_users": 256,
        "total_application_users": 5,
        "total_subscriptions": 38,
        "subscription_breakdown": {
            "active": 30,
            "trialing": 5,
            "past_due": 1,
            "cancelled": 2,
            "suspended": 0,
            "expired": 0,
        },
        "total_plans": 6,
        "active_plans": 4,
        "archived_plans": 1,
        "draft_plans": 1,
        "plan_distribution": [
            {
                "plan_id": "507f1f77bcf86cd799439011",
                "plan_name": "Professional",
                "plan_tier": "professional",
                "subscriber_count": 20,
            }
        ],
        "total_incidents": 15,
        "open_incidents": 3,
        "top_tenants_by_incidents": [
            {
                "tenant_id": "507f1f77bcf86cd799439012",
                "company_name": "Acme Corp",
                "incident_count": 5,
            }
        ],
        "total_visitors_all_time": 12500,
        "visitors_this_month": 1800,
        "top_tenants_by_visitors": [
            {
                "tenant_id": "507f1f77bcf86cd799439013",
                "company_name": "MegaCorp",
                "visitor_count": 3200,
            }
        ],
        "total_monthly_revenue": 4999.50,
        "total_yearly_revenue": 12000.00,
        "recent_signups_30d": 7,
        "last_updated": 1712548800,
    },
    description=(
        "Platform-wide statistics for the application admin dashboard. "
        "Includes tenant counts, subscription breakdowns, plan distribution, "
        "incident logs, visitor metrics, revenue, and top tenants."
    ),
    summary="Get admin platform-wide stats",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def admin_dashboard_stats(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    """Platform-wide dashboard for application admins — precomputed globally."""
    return await get_or_compute(
        scope_key=PrecomputeScope.GLOBAL.value,
        resource="admin_dashboard.stats",
        ttl=120,
        loader=_load_admin_stats,
    )


async def _load_admin_stats() -> Any:
    result = await get_admin_dashboard_stats()
    return (
        result.model_dump(mode="json", by_alias=True)
        if hasattr(result, "model_dump")
        else result
    )


# ─── Cross-tenant incidents oversight ─────────────────────────────────
#
# The tenant incident routes (api/v1/incident_route.py) are tenant-scoped.
# Platform admins need to see incidents across EVERY tenant to oversee NDPC
# compliance, so this mirrors the tenant list spec but drops the tenant
# filter (admins see all) and adds an optional tenant_id filter + an embedded
# tenant summary per row.

_ADMIN_INC_STATUSES = frozenset(
    {"open", "investigating", "contained", "reported_to_ndpc", "closed"}
)
_ADMIN_INC_TYPES = frozenset(
    {
        "data_breach",
        "unauthorized_access",
        "data_export_exposure",
        "device_loss",
        "misconfiguration",
        "third_party",
    }
)
_ADMIN_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})

ADMIN_INCIDENTS_LIST_SPEC = ListSpec(
    sortable_fields=frozenset(
        {"date_created", "notification_deadline", "risk_level", "status"}
    ),
    default_sort=(("date_created", -1),),
    search_fields=("description", "summary"),
    filters={
        "status": FilterDef(
            name="status", multi=True, allowed_values=_ADMIN_INC_STATUSES
        ),
        "incidentType": FilterDef(
            name="incidentType",
            mongo_field="incident_type",
            allowed_values=_ADMIN_INC_TYPES,
        ),
        "riskLevel": FilterDef(
            name="riskLevel",
            mongo_field="risk_level",
            allowed_values=_ADMIN_RISK_LEVELS,
        ),
        "ndpcNotified": FilterDef(
            name="ndpcNotified", mongo_field="ndpc_notified", coerce=coerce_bool
        ),
        "tenantId": FilterDef(name="tenantId", mongo_field="tenant_id"),
    },
    range_filters={"dateCreated": "date_created"},
    facet_fields=frozenset({"status"}),
)


def _map_admin_inc_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _admin_inc_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "status"}
    out: dict[str, int] = {}
    for v in _ADMIN_INC_STATUSES:
        out[v] = await collection.count_documents({**base, "status": v})
    out["all"] = sum(out.values())
    return out


async def _attach_admin_inc_tenant_summaries(result: Any) -> None:
    """Embed a ``{id, name}`` tenant summary on each incident row so the
    admin table can show which tenant each incident belongs to without an
    extra round-trip per row. One ``$in`` query for the whole page."""
    items = result.get("items") if isinstance(result, dict) else None
    if not items:
        return
    oids: list[ObjectId] = []
    for it in items:
        tid = it.get("tenant_id")
        if tid and ObjectId.is_valid(str(tid)):
            oids.append(ObjectId(str(tid)))
    name_map: dict[str, Any] = {}
    if oids:
        cursor = db.tenants.find(
            {"_id": {"$in": list(set(oids))}}, projection={"company_name": 1}
        )
        async for t in cursor:
            name_map[str(t["_id"])] = t.get("company_name")
    for it in items:
        tid = str(it.get("tenant_id") or "")
        it["tenant_summary"] = {"id": tid, "name": name_map.get(tid)}


@router.get("/incidents")
@document_response(
    message="Incidents fetched successfully",
    description=(
        "Cross-tenant incident list for platform-admin oversight. Filter by "
        "status, incidentType, riskLevel, ndpcNotified, tenantId, and a "
        "dateCreated range; free-text search over description/summary. Each "
        "row carries a tenant summary so the table can show the owning tenant."
    ),
    summary="List incidents across all tenants",
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - not an application admin",
    },
)
async def list_admin_incidents(
    request: Request,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    query = parse_list_query(request, ADMIN_INCIDENTS_LIST_SPEC)
    result = await run_list(
        collection=db.incident_logs,
        query=query,
        base_filter={},
        map_doc=_map_admin_inc_doc,
        facet_runner=_admin_inc_status_facet,
    )
    await _attach_admin_inc_tenant_summaries(result)
    return result


# ─── Range-aware, tabbed admin insights ───────────────────────────────


def _insights_query(
    start: Optional[int],
    stop: Optional[int],
    granularity: Optional[str],
    tab: Optional[str],
    plan_tier: Optional[str],
    subscription_status: Optional[str],
    billing_cycle: Optional[str],
    payment_provider: Optional[str],
    country: Optional[str],
    tenant_id: Optional[str],
    incident_type: Optional[str],
    incident_status: Optional[str],
    support_status: Optional[str],
    support_priority: Optional[str],
    onboarding_status: Optional[str],
) -> dict:
    return dict(
        start=start,
        stop=stop,
        granularity=granularity,
        tab=tab,
        plan_tier=plan_tier,
        subscription_status=subscription_status,
        billing_cycle=billing_cycle,
        payment_provider=payment_provider,
        country=country,
        tenant_id=tenant_id,
        incident_type=incident_type,
        incident_status=incident_status,
        support_status=support_status,
        support_priority=support_priority,
        onboarding_status=onboarding_status,
    )


@router.get("/insights")
@document_response(
    message="Admin insights fetched successfully",
    description=(
        "Range-aware, tabbed platform-admin analytics — the admin-shell "
        "counterpart to GET /v1/dashboard/insights. Caller-chosen window "
        "(clamped to the first tenant's creation date), auto-granularity, "
        "preceding-window KPI trends, per-tab sections (overview/tenants/"
        "billing/activity/risk), and platform filters. No plan gating."
    ),
    summary="Get range-aware admin insights",
    response_codes={
        200: "Admin insights fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        422: "Invalid range (stop before start)",
    },
)
async def admin_dashboard_insights(
    start: Optional[int] = None,
    stop: Optional[int] = None,
    granularity: Optional[str] = None,
    tab: Optional[str] = None,
    plan_tier: Optional[str] = None,
    subscription_status: Optional[str] = None,
    billing_cycle: Optional[str] = None,
    payment_provider: Optional[str] = None,
    country: Optional[str] = None,
    tenant_id: Optional[str] = None,
    incident_type: Optional[str] = None,
    incident_status: Optional[str] = None,
    support_status: Optional[str] = None,
    support_priority: Optional[str] = None,
    onboarding_status: Optional[str] = None,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    params = _insights_query(
        start,
        stop,
        granularity,
        tab,
        plan_tier,
        subscription_status,
        billing_cycle,
        payment_provider,
        country,
        tenant_id,
        incident_type,
        incident_status,
        support_status,
        support_priority,
        onboarding_status,
    )

    async def _compute() -> Any:
        result = await get_admin_insights(**params)
        return result.model_dump(mode="json", by_alias=True)

    # Default now-anchored, unfiltered overview is cacheable (60s); custom
    # range / filters / non-overview tab bypass the cache and compute on demand.
    is_default = not any(params.values())
    if is_default:
        return await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="admin_dashboard.insights",
            ttl=60,
            loader=_compute,
        )
    return await _compute()


@router.get("/insights/drill")
@document_response(
    message="Drill-down rows fetched successfully",
    description=(
        "Records behind a clicked admin-insights chart element. `section` is "
        "the chart's section id; `key` is the slice key, a point's date label "
        "(YYYY-MM-DD), or a tenant id. Honours the SAME range + filters as "
        "GET /v1/admins/dashboard/insights. Paginated (skip/limit)."
    ),
    summary="Insights drill-down (admin)",
    response_codes={200: "OK", 401: "Unauthorized", 403: "Forbidden"},
)
async def admin_dashboard_insights_drill(
    section: str,
    key: str = "",
    start: Optional[int] = None,
    stop: Optional[int] = None,
    skip: int = 0,
    limit: int = 25,
    plan_tier: Optional[str] = None,
    subscription_status: Optional[str] = None,
    billing_cycle: Optional[str] = None,
    payment_provider: Optional[str] = None,
    country: Optional[str] = None,
    tenant_id: Optional[str] = None,
    incident_type: Optional[str] = None,
    incident_status: Optional[str] = None,
    support_status: Optional[str] = None,
    support_priority: Optional[str] = None,
    onboarding_status: Optional[str] = None,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    from services.insights_drill_service import drill_admin

    return await drill_admin(
        section=section,
        key=key,
        start=start,
        stop=stop,
        skip=max(skip, 0),
        limit=min(max(limit, 1), 200),
        plan_tier=plan_tier,
        subscription_status=subscription_status,
        billing_cycle=billing_cycle,
        payment_provider=payment_provider,
        country=country,
        tenant_id=tenant_id,
        incident_type=incident_type,
        incident_status=incident_status,
        support_status=support_status,
        support_priority=support_priority,
        onboarding_status=onboarding_status,
    )


@router.get("/insights/export")
async def admin_dashboard_insights_export(
    format: str = "csv",
    start: Optional[int] = None,
    stop: Optional[int] = None,
    granularity: Optional[str] = None,
    tab: Optional[str] = None,
    plan_tier: Optional[str] = None,
    subscription_status: Optional[str] = None,
    billing_cycle: Optional[str] = None,
    payment_provider: Optional[str] = None,
    country: Optional[str] = None,
    tenant_id: Optional[str] = None,
    incident_type: Optional[str] = None,
    incident_status: Optional[str] = None,
    support_status: Optional[str] = None,
    support_priority: Optional[str] = None,
    onboarding_status: Optional[str] = None,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
):
    if format.lower() != "csv":
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="Only format=csv is currently supported for admin insights export.",
        )
    params = _insights_query(
        start,
        stop,
        granularity,
        tab,
        plan_tier,
        subscription_status,
        billing_cycle,
        payment_provider,
        country,
        tenant_id,
        incident_type,
        incident_status,
        support_status,
        support_priority,
        onboarding_status,
    )
    result = await get_admin_insights(**params)
    rows = _admin_insights_to_rows(result)
    return csv_response(
        rows=rows,
        columns=["section", "key", "label", "value", "extra"],
        filename=f"admin-insights-{result.meta.tab}",
    )


def _admin_insights_to_rows(result: Any) -> list:
    """Flatten an AdminInsightsResponse into CSV rows (one per KPI / datum)."""
    rows: list = []
    for kpi in result.kpis:
        rows.append(
            {
                "section": "kpi",
                "key": kpi.key,
                "label": kpi.label,
                "value": kpi.value,
                "extra": kpi.unit or "",
            }
        )
    for sid, section in result.sections.items():
        if section.points:
            for p in section.points:
                rows.append(
                    {
                        "section": sid,
                        "key": str(p.timestamp),
                        "label": p.label,
                        "value": p.value,
                        "extra": "",
                    }
                )
        if section.slices:
            for s in section.slices:
                rows.append(
                    {
                        "section": sid,
                        "key": s.key,
                        "label": s.label,
                        "value": s.value,
                        "extra": s.percentage,
                    }
                )
        if section.buckets:
            for b in section.buckets:
                rows.append(
                    {
                        "section": sid,
                        "key": str(b.hour),
                        "label": b.label,
                        "value": b.value,
                        "extra": "",
                    }
                )
        if section.items:
            for it in section.items:
                rows.append(
                    {
                        "section": sid,
                        "key": it.id or "",
                        "label": it.label,
                        "value": it.value,
                        "extra": it.percentage,
                    }
                )
        if section.rows:
            for r in section.rows:
                rows.append(
                    {
                        "section": sid,
                        "key": r.get("id") or r.get("tenantId") or "",
                        "label": r.get("companyName") or "",
                        "value": r.get("monthlyRevenue") or r.get("dateCreated") or "",
                        "extra": r.get("status") or r.get("subscriptionStatus") or "",
                    }
                )
    return rows


# ─── Attention queue (Issue 1 backend) ────────────────────────────────


@router.get("/attention")
@document_response(
    message="Attention queue fetched successfully",
    description=(
        "Operational queue of items that need an admin's attention right now: "
        "open support cases, new onboarding submissions, NDPC-deadline incidents, "
        "and content-cadence reminders. Backs the dashboard 'Needs attention' "
        "panel introduced in Issue 1.\n\n"
        "Items are sorted by priority (blocker → urgent → normal → "
        "informational) and then by count descending. Cache TTL matches the "
        "stats endpoint (120s, refreshed every 60s by the APScheduler fanout) "
        "so polling at 60s keeps the queue current."
    ),
    summary="Get the platform-admin attention queue",
    response_codes={401: "Unauthorized"},
)
async def admin_attention_queue(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    return await get_or_compute(
        scope_key=PrecomputeScope.GLOBAL.value,
        resource="admin_dashboard.attention",
        ttl=120,
        loader=_load_admin_attention,
    )


async def _load_admin_attention() -> Any:
    result = await get_admin_attention_queue()
    return (
        result.model_dump(mode="json", by_alias=True)
        if hasattr(result, "model_dump")
        else result
    )


# ─── Email outbox (Phase B3 — Issue 6) ─────────────────────────────


@router.get("/email-outbox")
@document_response(
    message="Email outbox rows fetched successfully",
    description=(
        "Recent email-send attempts written by the central notification "
        "dispatch (Issue 6). Every notification that supplies a template + "
        "preference flag produces one row: status is one of `sent` | `queued` "
        "| `skipped` | `failed`, and `skipped_reason` carries a stable enum "
        "string when status is `skipped`. Drives the frontend email "
        "diagnostics card's 'recent attempts' panel.\n\n"
        "Filters:\n"
        "  - `status` — narrow to one outcome.\n"
        "  - `template_key` — narrow to one template (e.g. `notif_incident_deadline`).\n"
        "  - `tenant_id` — narrow to one tenant's traffic.\n"
        "  - `skip` / `limit` — pagination (limit capped at 200)."
    ),
    summary="List recent email dispatch attempts",
    include_meta=True,
    response_codes={401: "Unauthorized"},
)
async def list_email_outbox(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
    status: Annotated[
        Optional[str],
        Query(
            description=(
                "Filter rows by outcome. One of `sent` | `queued` | "
                "`skipped` | `failed`."
            )
        ),
    ] = None,
    template_key: Annotated[
        Optional[str],
        Query(description="Filter rows by mounted template key."),
    ] = None,
    tenant_id: Annotated[
        Optional[str],
        Query(description="Filter rows by tenant id."),
    ] = None,
    skip: Annotated[int, Query(ge=0, description="Offset")] = 0,
    limit: Annotated[
        int, Query(ge=1, le=200, description="Page size — hard-capped at 200")
    ] = 50,
) -> Any:
    from repositories.email_outbox_repo import (
        count_email_outbox_rows,
        list_email_outbox_rows,
    )

    rows = await list_email_outbox_rows(
        status=status,
        template_key=template_key,
        tenant_id=tenant_id,
        skip=skip,
        limit=limit,
    )
    total = await count_email_outbox_rows(
        status=status,
        template_key=template_key,
        tenant_id=tenant_id,
    )
    return {"items": rows, "total": total}


@router.get("/billing")
@document_response(
    message="Billing summary fetched successfully",
    description="Revenue, MRR, subscription churn, and invoice stats for a date range.",
    summary="Get billing summary",
    success_example={
        "period": {"start": 1709251200, "end": 1711929600},
        "total_revenue_minor": 1250000,
        "invoice_count": 42,
        "new_subscriptions": 8,
        "cancelled_subscriptions": 2,
        "active_subscriptions": 38,
        "mrr_minor": 425000,
        "generated_at": 1712548800,
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def billing_summary(
    start_date: int = Query(
        default=None,
        description="Period start (Unix timestamp). Defaults to 30 days ago.",
    ),
    end_date: int = Query(
        default=None,
        description="Period end (Unix timestamp). Defaults to now.",
    ),
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    """Billing summary with revenue, MRR, and subscription metrics.

    Default range (last 30 days, no args) is served from the precompute
    cache. Custom ranges bypass the cache.
    """
    now = int(time.time())
    if end_date is None and start_date is None:
        return await get_or_compute(
            scope_key=PrecomputeScope.GLOBAL.value,
            resource="admin_dashboard.billing_30d",
            ttl=300,
            loader=_load_billing_30d,
        )
    if end_date is None:
        end_date = now
    if start_date is None:
        start_date = now - (30 * 86400)
    return await get_billing_summary(start_date=start_date, end_date=end_date)


async def _load_billing_30d() -> Any:
    now = int(time.time())
    result = await get_billing_summary(start_date=now - (30 * 86400), end_date=now)
    return (
        result.model_dump(mode="json", by_alias=True)
        if hasattr(result, "model_dump")
        else result
    )


@router.get("/billing/discrepancies")
@document_response(
    message="Billing discrepancies fetched successfully",
    description="Lists active subscriptions missing invoices and successful payments without matching invoices.",
    summary="Get billing discrepancies",
    success_example=[
        {
            "type": "missing_invoice",
            "subscription_id": "507f1f77bcf86cd799439011",
            "payment_id": None,
            "tenant_id": "507f1f77bcf86cd799439012",
            "issue_description": "Active subscription has no invoice in current period",
            "detected_at": 1712548800,
        }
    ],
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def billing_discrepancies(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> Any:
    """Reconciliation check: finds missing invoices and orphaned payments."""
    return await get_or_compute(
        scope_key=PrecomputeScope.GLOBAL.value,
        resource="admin_dashboard.discrepancies",
        ttl=300,
        loader=_load_discrepancies,
    )


async def _load_discrepancies() -> Any:
    result = await get_payment_discrepancies()
    if isinstance(result, list):
        return [
            r.model_dump(mode="json", by_alias=True) if hasattr(r, "model_dump") else r
            for r in result
        ]
    return result
