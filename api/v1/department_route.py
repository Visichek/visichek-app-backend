from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.department_schema import DepartmentCreate, DepartmentUpdate, DepartmentOut
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.department_service import (
    add_department,
    retrieve_department_by_id,
    retrieve_departments,
    update_department_by_id,
    remove_department,
)

router = APIRouter(prefix="/departments", tags=["Departments"])

_admin_roles = verify_system_user_token("super_admin", "dept_admin")


@router.post("/")
@document_response(message="Department created successfully", status_code=status.HTTP_201_CREATED)
async def create_department_endpoint(
    dept_data: DepartmentCreate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    if not principal.is_super_admin:
        dept_data.tenant_id = principal.tenant_id or dept_data.tenant_id
    return await add_department(dept_data=dept_data)


@router.get("/")
@document_response(message="Departments fetched successfully", success_example=[])
async def list_departments(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_departments(tenant_id=tenant_id, start=start, stop=stop)


@router.get("/{department_id}")
@document_response(message="Department fetched successfully")
async def get_department_endpoint(
    department_id: str,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_department_by_id(department_id=department_id, tenant_id=tenant_id)


@router.patch("/{department_id}")
@document_response(message="Department updated successfully")
async def update_department_endpoint(
    department_id: str,
    dept_data: DepartmentUpdate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    return await update_department_by_id(
        department_id=department_id, tenant_id=tenant_id, dept_data=dept_data
    )


@router.delete("/{department_id}")
@document_response(message="Department deleted successfully")
async def delete_department_endpoint(
    department_id: str,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    tenant_id = principal.tenant_id or ""
    return await remove_department(department_id=department_id, tenant_id=tenant_id)
