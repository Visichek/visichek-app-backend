from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
import io

from core.response_envelope import document_response
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.dashboard_service import get_dashboard_stats, get_visitor_log
from services.export_service import export_visitor_log_csv, export_visitor_log_xlsx

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

_admin_roles = verify_system_user_token("dept_admin", "super_admin")


@router.get("/stats")
@document_response(message="Dashboard stats fetched successfully")
async def dashboard_stats(
    department_id: str = None,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await get_dashboard_stats(tenant_id=tenant_id, department_id=department_id)


@router.get("/visitors")
@document_response(message="Visitor log fetched successfully")
async def dashboard_visitor_log(
    department_id: str = None,
    status_filter: str = None,
    search: str = None,
    date_from: int = None,
    date_to: int = None,
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
        start=start,
        stop=stop,
    )


@router.get("/visitors/active")
@document_response(message="Active visitors fetched successfully", success_example=[])
async def dashboard_active_visitors(
    department_id: str = None,
    principal: AuthPrincipal = Depends(verify_system_user_token(
        "receptionist", "dept_admin", "super_admin"
    )),
):
    from services.visit_session_service import retrieve_active_visitors
    tenant_id = principal.tenant_id or ""
    return await retrieve_active_visitors(tenant_id=tenant_id, department_id=department_id)


@router.get("/export")
async def dashboard_export(
    format: str = "csv",
    department_id: str = None,
    date_from: int = None,
    date_to: int = None,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""

    if format == "xlsx":
        data = await export_visitor_log_xlsx(
            tenant_id=tenant_id, department_id=department_id,
            date_from=date_from, date_to=date_to,
        )
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=visitor_log.xlsx"},
        )
    else:
        data = await export_visitor_log_csv(
            tenant_id=tenant_id, department_id=department_id,
            date_from=date_from, date_to=date_to,
        )
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=visitor_log.csv"},
        )
