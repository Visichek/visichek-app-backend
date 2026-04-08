from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.department_repo import (
    create_department,
    get_department,
    get_departments,
    update_department,
    delete_department,
)
from schemas.department_schema import DepartmentCreate, DepartmentUpdate, DepartmentOut


async def add_department(dept_data: DepartmentCreate, created_by: str = None) -> DepartmentOut:
    existing = await get_department({
        "tenant_id": dept_data.tenant_id,
        "code": dept_data.code,
    })
    if existing:
        raise HTTPException(status_code=409, detail="Department with this code already exists in tenant")
    if created_by:
        dept_data.created_by = created_by
    return await create_department(dept_data)


async def retrieve_department_by_id(department_id: str, tenant_id: str) -> DepartmentOut:
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    result = await get_department({"_id": ObjectId(department_id), "tenant_id": tenant_id})
    if not result:
        raise HTTPException(status_code=404, detail="Department not found")
    return result


async def retrieve_departments(tenant_id: str, start=0, stop=100) -> List[DepartmentOut]:
    return await get_departments(filter_dict={"tenant_id": tenant_id}, start=start, stop=stop)


async def update_department_by_id(
    department_id: str, tenant_id: str, dept_data: DepartmentUpdate
) -> DepartmentOut:
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    result = await update_department(
        {"_id": ObjectId(department_id), "tenant_id": tenant_id}, dept_data
    )
    if not result:
        raise HTTPException(status_code=404, detail="Department not found or update failed")
    return result


async def remove_department(department_id: str, tenant_id: str):
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    result = await delete_department({"_id": ObjectId(department_id), "tenant_id": tenant_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Department not found")
