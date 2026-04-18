from typing import Annotated, List

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.department_schema import (
    DepartmentCreate,
    DepartmentUpdate,
    DepartmentWithSummaryOut,
)
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.department_service import (
    add_department,
    retrieve_department_by_id_with_summary,
    retrieve_departments_with_summary,
    update_department_by_id,
    remove_department,
)

router = APIRouter(prefix="/departments", tags=["Departments"])

_admin_roles = verify_system_user_token("super_admin", "dept_admin")


@router.post("/")
@document_response(
    message="Department created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Create a new department within a tenant. Super-admins or department admins can create departments.",
    summary="Create a new department",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "code": "HR-001",
        "name": "Human Resources",
        "is_active": True,
        "created_by": "64f1a2b3c4d5e6f7a8b9c0d3",
        "date_created": 1712500000,
        "last_updated": 1712500000,
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        422: "Validation error",
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
        422: {
            "success": False,
            "message": "Validation error",
            "code": "VALIDATION_FAILED",
        },
    },
)
async def create_department_endpoint(
    dept_data: DepartmentCreate,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    dept_data.tenant_id = principal.tenant_id
    return await add_department(dept_data=dept_data, created_by=principal.user_id)


@router.get("")
@document_response(
    message="Departments fetched successfully",
    description="Retrieve a paginated list of departments. Super-admins see all departments; department admins see only their tenant's departments.",
    summary="List departments",
    success_example=[
        {
            "id": "64f1a2b3c4d5e6f7a8b9c0d2",
            "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "code": "HR-001",
            "name": "Human Resources",
            "is_active": True,
            "created_by": "64f1a2b3c4d5e6f7a8b9c0d3",
            "date_created": 1712500000,
            "last_updated": 1712500000,
        }
    ],
    include_meta=True,
    response_codes={401: "Unauthorized", 403: "Insufficient permissions"},
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
async def list_departments(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> List[DepartmentWithSummaryOut]:
    tenant_id = principal.tenant_id or ""
    return await retrieve_departments_with_summary(
        tenant_id=tenant_id, start=start, stop=stop
    )


@router.get("/{department_id}")
@document_response(
    message="Department fetched successfully",
    description="Retrieve a specific department by ID. Super-admins see all departments; department admins see only their tenant's departments.",
    summary="Retrieve department by ID",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "code": "HR-001",
        "name": "Human Resources",
        "is_active": True,
        "created_by": "64f1a2b3c4d5e6f7a8b9c0d3",
        "date_created": 1712500000,
        "last_updated": 1712500000,
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Department not found",
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
        404: {
            "success": False,
            "message": "Department not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def get_department_endpoint(
    department_id: str,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> DepartmentWithSummaryOut:
    tenant_id = principal.tenant_id or ""
    return await retrieve_department_by_id_with_summary(
        department_id=department_id, tenant_id=tenant_id
    )


@router.patch("/{department_id}")
@document_response(
    message="Department updated successfully",
    description="Update department details partially. Super-admins or department admins can update departments within their scope.",
    summary="Update department by ID",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d2",
        "tenant_id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "code": "HR-001",
        "name": "Human Resources - Updated",
        "is_active": True,
        "created_by": "64f1a2b3c4d5e6f7a8b9c0d3",
        "date_created": 1712500000,
        "last_updated": 1712600000,
    },
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Department not found",
        422: "Validation error",
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
        404: {
            "success": False,
            "message": "Department not found",
            "code": "RESOURCE_NOT_FOUND",
        },
        422: {
            "success": False,
            "message": "Validation error",
            "code": "VALIDATION_FAILED",
        },
    },
)
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
@document_response(
    message="Department deleted successfully",
    description="Delete a department. Only super-admin users can delete departments. This action is permanent.",
    summary="Delete department by ID",
    success_example={"deleted": True},
    response_codes={
        401: "Unauthorized",
        403: "Insufficient permissions",
        404: "Department not found",
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
        404: {
            "success": False,
            "message": "Department not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def delete_department_endpoint(
    department_id: str,
    principal: AuthPrincipal = Depends(verify_system_user_token("super_admin")),
):
    tenant_id = principal.tenant_id or ""
    return await remove_department(department_id=department_id, tenant_id=tenant_id)
