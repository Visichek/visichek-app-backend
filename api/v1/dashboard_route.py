from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
import io

from core.csv_export import csv_response
from core.errors import AppException, ErrorCode
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.response_envelope import document_response
from security.auth import verify_any_token, verify_system_user_token
from security.principal import AuthPrincipal
from services.dashboard_service import get_dashboard_stats, get_visitor_log
from services.export_service import export_visitor_log_csv, export_visitor_log_xlsx
from services.insights_service import get_insights, is_export_allowed

router = APIRouter(prefix="/dashboard", tags=["Tenant Dashboard"])

_admin_roles = verify_system_user_token("dept_admin", "super_admin")
_all_tenant_roles = verify_system_user_token(
    "receptionist", "dept_admin", "super_admin", "auditor", "security_officer", "dpo"
)


@router.get("/stats")
@document_response(
    message="Dashboard stats fetched successfully",
    success_example={
        "total_visits": 1250,
        "total_visitors": 540,
        "currently_active": 8,
        "visitors_today": 47,
        "new_signups_today": 6,
        "new_signups_30d": 142,
        "visitor_retention_rate": 38.5,
        "avg_visit_duration_minutes": 45.2,
        "visits_growth_wow": {
            "current": 312,
            "previous": 280,
            "change": 32,
            "change_percent": 11.4,
        },
        "visit_status_distribution": [
            {
                "key": "checked_out",
                "label": "Checked out",
                "value": 980,
                "percentage": 78.4,
            },
            {"key": "checked_in", "label": "Checked in", "value": 8, "percentage": 0.6},
        ],
        "check_in_method_distribution": [
            {
                "key": "qr_registration",
                "label": "QR registration",
                "value": 720,
                "percentage": 57.6,
            },
            {
                "key": "manual_entry",
                "label": "Manual entry",
                "value": 410,
                "percentage": 32.8,
            },
            {"key": "id_scan", "label": "ID scan", "value": 120, "percentage": 9.6},
        ],
        "top_departments": [
            {
                "id": "dept-1",
                "label": "Engineering",
                "value": 412,
                "percentage": 33.0,
                "extra": {"code": "ENG"},
            },
        ],
        "top_companies": [
            {
                "id": None,
                "label": "Acme Corp",
                "value": 38,
                "percentage": 3.0,
                "extra": {},
            },
        ],
        "hourly_distribution": [{"hour": 9, "label": "09:00", "value": 81}],
        "day_of_week_distribution": [{"day": 1, "label": "Tue", "value": 220}],
        "visits_last_7_days": [
            {"timestamp": 1746662400, "label": "2026-05-08", "value": 47},
        ],
        "open_incidents": 1,
        "open_dsr_requests": 2,
        "role_view": "super_admin",
        "last_updated": 1712548800,
    },
    description="Comprehensive tenant dashboard payload — KPIs, pie-chart distributions, top-N rollups, hourly/daily heatmaps, time series, growth metrics, and real-time samples",
    summary="Get dashboard stats",
    response_codes={
        200: "Dashboard stats fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or missing token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def dashboard_stats(
    department_id: Optional[str] = None,
    principal: AuthPrincipal = Depends(_all_tenant_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""
    return await get_dashboard_stats(
        tenant_id=tenant_id,
        department_id=department_id,
        role=principal.role,
    )


@router.get("/insights")
@document_response(
    message="Insights fetched successfully",
    description=(
        "Range-aware, role-scoped, plan-gated analytics that replace the "
        "fixed-window tenant dashboard. Accepts a caller-chosen start/stop "
        "window (clamped to the tenant's creation date), auto-picks a bucket "
        "granularity, and returns only the sections/KPIs the requested role's "
        "tabs need. Free tenants get a minimal Overview-only experience."
    ),
    summary="Get role-scoped insights",
    response_codes={
        200: "Insights fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        422: "Invalid range (stop before start)",
    },
)
async def dashboard_insights(
    role_view: Optional[str] = None,
    start: Optional[int] = None,
    stop: Optional[int] = None,
    granularity: Optional[str] = None,
    department_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    host_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    operation_type: Optional[str] = None,
    resource_type: Optional[str] = None,
    incident_type: Optional[str] = None,
    incident_status: Optional[str] = None,
    severity: Optional[str] = None,
    dsr_type: Optional[str] = None,
    dsr_status: Optional[str] = None,
    lawful_basis: Optional[str] = None,
    status_filter: Optional[str] = None,
    principal: AuthPrincipal = Depends(_all_tenant_roles),
) -> Any:
    tenant_id = principal.tenant_id or ""

    async def _compute() -> Any:
        result = await get_insights(
            tenant_id=tenant_id,
            caller_user_id=principal.user_id,
            caller_role=principal.role,
            role_view=role_view,
            start=start,
            stop=stop,
            granularity=granularity,
            department_id=department_id,
            branch_id=branch_id,
            host_id=host_id,
            actor_id=actor_id,
            operation_type=operation_type,
            resource_type=resource_type,
            incident_type=incident_type,
            incident_status=incident_status,
            severity=severity,
            dsr_type=dsr_type,
            dsr_status=dsr_status,
            lawful_basis=lawful_basis,
            status_filter=status_filter,
        )
        return result.model_dump(mode="json", by_alias=True)

    # The default, now-anchored, unfiltered view for the caller's own role is
    # cacheable (60s). Custom ranges / filters / role_view bypass the cache and
    # compute on demand — that's acceptable per the Insights spec.
    is_default = not any(
        (
            role_view,
            start,
            stop,
            granularity,
            department_id,
            branch_id,
            host_id,
            actor_id,
            operation_type,
            resource_type,
            incident_type,
            incident_status,
            severity,
            dsr_type,
            dsr_status,
            lawful_basis,
            status_filter,
        )
    )
    if is_default and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}:{principal.role}",
            resource="dashboard.insights",
            ttl=60,
            loader=_compute,
        )
    return await _compute()


@router.get("/live/stream", include_in_schema=False)
async def dashboard_live_stream(
    request: Request,
    principal: AuthPrincipal = Depends(verify_any_token),
) -> StreamingResponse:
    """Single role-agnostic SSE stream of LIVE dashboard counters.

    ONE endpoint for everyone (like ``GET /v1/notifications/stream``); WHAT it
    returns depends on the caller's access rights:

      * application admin -> platform-wide live counters (openIncidents,
        visitorCheckInsToday, newTenantsToday, …).
      * any tenant role -> that tenant's live counters (currentlyActive,
        checkInsToday, openIncidents, …) plus a ``meta`` block with the
        tenant's plan context (planTier / isFreeFallback).

    Pushes the FULL ABSOLUTE slice on connect, on a relevant write, and every
    ~15s as a safety net. Heavy charts stay on the one-shot GETs
    (/v1/dashboard/insights, /v1/admins/dashboard/stats). Layered on top of
    polling: if unavailable the FE keeps polling and still works."""
    from security.principal import TENANT_USER_ROLES
    from services.dashboard_stream_service import unified_stream

    if principal.role != "admin" and principal.role not in TENANT_USER_ROLES:
        # Application users have no dashboard surface.
        raise AppException(
            status_code=403,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message="No dashboard available for this account type.",
        )

    generator = unified_stream(request, principal=principal)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/insights/drill")
@document_response(
    message="Drill-down rows fetched successfully",
    description=(
        "Records behind a clicked Insights chart element. `section` is the "
        "chart's section id; `key` is the slice key, a point's date label "
        "(YYYY-MM-DD), or an hour/entity id. Honours the SAME range, filters, "
        "and role scoping as GET /v1/dashboard/insights. Paginated (skip/limit)."
    ),
    summary="Insights drill-down (tenant)",
    response_codes={200: "OK", 401: "Unauthorized", 403: "Forbidden"},
)
async def dashboard_insights_drill(
    section: str,
    key: str = "",
    start: Optional[int] = None,
    stop: Optional[int] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    role_view: Optional[str] = None,
    department_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    host_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    operation_type: Optional[str] = None,
    resource_type: Optional[str] = None,
    incident_type: Optional[str] = None,
    incident_status: Optional[str] = None,
    severity: Optional[str] = None,
    dsr_type: Optional[str] = None,
    dsr_status: Optional[str] = None,
    lawful_basis: Optional[str] = None,
    status_filter: Optional[str] = None,
    principal: AuthPrincipal = Depends(_all_tenant_roles),
) -> Any:
    from services.insights_drill_service import drill_tenant

    return await drill_tenant(
        tenant_id=principal.tenant_id or "",
        caller_user_id=principal.user_id,
        caller_role=principal.role,
        section=section,
        key=key,
        start=start,
        stop=stop,
        skip=skip,
        limit=limit,
        role_view=role_view,
        department_id=department_id,
        branch_id=branch_id,
        host_id=host_id,
        actor_id=actor_id,
        operation_type=operation_type,
        resource_type=resource_type,
        incident_type=incident_type,
        incident_status=incident_status,
        severity=severity,
        dsr_type=dsr_type,
        dsr_status=dsr_status,
        lawful_basis=lawful_basis,
        status_filter=status_filter,
    )


@router.get("/insights/export")
async def dashboard_insights_export(
    format: str = "csv",
    role_view: Optional[str] = None,
    start: Optional[int] = None,
    stop: Optional[int] = None,
    granularity: Optional[str] = None,
    department_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    principal: AuthPrincipal = Depends(_all_tenant_roles),
):
    tenant_id = principal.tenant_id or ""
    if not await is_export_allowed(tenant_id):
        raise AppException(
            status_code=403,
            code=ErrorCode.SUBSCRIPTION_REQUIRED,
            message="Insights export is available on paid plans. Upgrade to enable CSV/PDF export.",
        )
    if format.lower() != "csv":
        # PDF rendering of the current tab is a planned enhancement; only CSV
        # of the underlying rows is implemented today.
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="Only format=csv is currently supported for insights export.",
        )
    result = await get_insights(
        tenant_id=tenant_id,
        caller_user_id=principal.user_id,
        caller_role=principal.role,
        role_view=role_view,
        start=start,
        stop=stop,
        granularity=granularity,
        department_id=department_id,
        branch_id=branch_id,
    )
    rows = _insights_to_rows(result)
    return csv_response(
        rows=rows,
        columns=["section", "key", "label", "value", "extra"],
        filename=f"insights-{result.meta.role_view}-{tenant_id}",
    )


def _insights_to_rows(result: Any) -> list:
    """Flatten an InsightsResponse into CSV rows (one per KPI / data point)."""
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
    return rows


@router.get("/visitors")
@document_response(
    message="Visitor log fetched successfully",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439012",
            "tenant_id": "tenant-12345",
            "visitor_name": "Jane Smith",
            "visitor_email": "jane.smith@example.com",
            "visitor_phone": "+234 805 123 4567",
            "department_id": "dept-1234567890",
            "department_name": "engineering",
            "purpose_of_visit": "Technical consultation",
            "check_in_time": 1712544600,
            "check_out_time": 1712548200,
            "status": "checked_out",
            "badge_issued": True,
            "contact_person": "Engineer John Doe",
            "notes": "Scheduled meeting regarding system architecture",
        }
    ],
    description="Retrieve paginated visitor logs with optional filtering by department, status, date range, or search query",
    summary="Get visitor log",
    include_meta=True,
    response_codes={
        200: "Visitor log fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or missing token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def dashboard_visitor_log(
    department_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    host_id: Optional[str] = None,
    verification_status: Optional[str] = None,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await get_visitor_log(
        tenant_id=tenant_id,
        department_id=department_id,
        status_filter=status_filter,
        search=search,
        date_from=date_from,
        date_to=date_to,
        host_id=host_id,
        verification_status=verification_status,
        start=start,
        stop=stop,
    )


@router.get("/visitors/active")
@document_response(
    message="Active visitors fetched successfully",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439013",
            "tenant_id": "tenant-12345",
            "visitor_name": "Michael Brown",
            "visitor_email": "michael.brown@example.com",
            "visitor_phone": "+234 803 456 7890",
            "department_id": "dept-1234567890",
            "department_name": "sales",
            "purpose_of_visit": "Client meeting",
            "check_in_time": 1712548000,
            "check_out_time": None,
            "status": "checked_in",
            "badge_issued": True,
            "contact_person": "Sales Manager Alice Johnson",
            "notes": "Q2 partnership discussion",
        }
    ],
    description="Retrieve currently active visitors in real-time",
    summary="Get active visitors",
    response_codes={
        200: "Active visitors fetched successfully",
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or missing token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Insufficient permissions",
            "code": "AUTH_PERMISSION_DENIED",
        },
    },
)
async def dashboard_active_visitors(
    department_id: Optional[str] = None,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "dept_admin", "super_admin")
    ),
) -> Any:
    from services.visit_session_service import retrieve_active_visitors

    tenant_id = principal.tenant_id or ""
    # Unfiltered active visitors hit the precompute cache (TTL 30s —
    # this view needs to feel live).
    if not department_id and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="dashboard.visitors_active",
            ttl=30,
            loader=lambda: _load_active_visitors(tenant_id),
        )
    return await retrieve_active_visitors(
        tenant_id=tenant_id, department_id=department_id
    )


async def _load_active_visitors(tenant_id: str) -> list:
    from services.visit_session_service import retrieve_active_visitors

    sessions = await retrieve_active_visitors(tenant_id=tenant_id)
    return [
        s.model_dump(mode="json", by_alias=True) if hasattr(s, "model_dump") else s
        for s in sessions
    ]


@router.get("/export")
async def dashboard_export(
    format: str = "csv",
    department_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""

    if format == "xlsx":
        data = await export_visitor_log_xlsx(
            tenant_id=tenant_id,
            department_id=department_id,
            date_from=date_from,
            date_to=date_to,
        )
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=visitor_log.xlsx"},
        )
    else:
        data = await export_visitor_log_csv(
            tenant_id=tenant_id,
            department_id=department_id,
            date_from=date_from,
            date_to=date_to,
        )
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=visitor_log.csv"},
        )
