from bson import ObjectId
from fastapi import HTTPException
from typing import List, Optional

from repositories.department_repo import (
    count_departments,
    create_department,
    get_department,
    get_departments,
    update_department,
    delete_department,
)
from schemas.department_schema import (
    DepartmentCreate,
    DepartmentUpdate,
    DepartmentOut,
    DepartmentWithSummaryOut,
)
from services.plan_limits import enforce_entity_cap


async def add_department(
    dept_data: DepartmentCreate, created_by: Optional[str] = None
) -> DepartmentOut:
    # Enforce plan cap on total departments for this tenant
    current_count = await count_departments({"tenant_id": dept_data.tenant_id})
    await enforce_entity_cap(
        tenant_id=dept_data.tenant_id or "",
        cap_key="max_departments",
        current_count=current_count,
        friendly_name="Department",
    )

    existing = await get_department(
        {
            "tenant_id": dept_data.tenant_id,
            "code": dept_data.code,
        }
    )
    if existing:
        raise HTTPException(
            status_code=409, detail="Department with this code already exists in tenant"
        )
    if created_by:
        dept_data.created_by = created_by
    return await create_department(dept_data)


async def retrieve_department_by_id(
    department_id: str, tenant_id: str
) -> DepartmentOut:
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    result = await get_department(
        {"_id": ObjectId(department_id), "tenant_id": tenant_id}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Department not found")
    return result


async def retrieve_departments(
    tenant_id: str, start=0, stop=100
) -> List[DepartmentOut]:
    return await get_departments(
        filter_dict={"tenant_id": tenant_id}, start=start, stop=stop
    )


async def _enrich_department(dept: DepartmentOut) -> DepartmentWithSummaryOut:
    import asyncio
    from services.summary_resolver import (
        resolve_tenant_summary,
        resolve_user_summary,
    )

    tenant_summary, creator_summary = await asyncio.gather(
        resolve_tenant_summary(dept.tenant_id),
        resolve_user_summary(dept.created_by),
    )
    data = dept.model_dump(by_alias=False)
    data["tenant_summary"] = tenant_summary
    data["created_by_summary"] = creator_summary
    return DepartmentWithSummaryOut(**data)


async def retrieve_departments_with_summary(
    tenant_id: str, start: int = 0, stop: int = 100
) -> List[DepartmentWithSummaryOut]:
    import asyncio

    departments = await retrieve_departments(
        tenant_id=tenant_id, start=start, stop=stop
    )
    return list(await asyncio.gather(*[_enrich_department(d) for d in departments]))


async def retrieve_department_by_id_with_summary(
    department_id: str, tenant_id: str
) -> DepartmentWithSummaryOut:
    dept = await retrieve_department_by_id(
        department_id=department_id, tenant_id=tenant_id
    )
    return await _enrich_department(dept)


async def update_department_by_id(
    department_id: str, tenant_id: str, dept_data: DepartmentUpdate
) -> DepartmentOut:
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    result = await update_department(
        {"_id": ObjectId(department_id), "tenant_id": tenant_id}, dept_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Department not found or update failed"
        )
    return result


async def remove_department(department_id: str, tenant_id: str):
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    result = await delete_department(
        {"_id": ObjectId(department_id), "tenant_id": tenant_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Department not found")
