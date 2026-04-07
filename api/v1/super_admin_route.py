from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.dashboard_service import get_dashboard_stats
from services.department_service import retrieve_departments, add_department
from services.system_user_service import retrieve_system_users, add_system_user
from schemas.department_schema import DepartmentCreate
from schemas.system_user_schema import SystemUserCreate

router = APIRouter(prefix="/super-admin", tags=["Super Admin"])


@router.get("/analytics")
@document_response(message="Company-wide analytics fetched successfully")
async def company_analytics(
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await get_dashboard_stats(tenant_id=tenant_id)


@router.get("/departments")
@document_response(message="All departments fetched successfully", success_example=[])
async def list_all_departments(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_departments(tenant_id=tenant_id, start=start, stop=stop)


@router.post("/departments")
@document_response(message="Department created successfully", status_code=status.HTTP_201_CREATED)
async def create_department(
    dept_data: DepartmentCreate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    if principal.tenant_id:
        dept_data.tenant_id = principal.tenant_id
    return await add_department(dept_data=dept_data)


@router.get("/admins")
@document_response(message="All system users fetched successfully", success_example=[])
async def list_all_admins(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_system_users(tenant_id=tenant_id, start=start, stop=stop)


@router.post("/admins/invite")
@document_response(message="System user invited successfully", status_code=status.HTTP_201_CREATED)
async def invite_admin(
    user_data: SystemUserCreate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    if principal.tenant_id:
        user_data.tenant_id = principal.tenant_id
    return await add_system_user(user_data=user_data)
