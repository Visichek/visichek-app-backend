from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
import io

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.dashboard_service import get_dashboard_stats, get_visitor_log
from services.export_service import export_visitor_log_csv, export_visitor_log_xlsx

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
