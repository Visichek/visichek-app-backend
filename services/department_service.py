import re
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


def _name_match_filter(name: str) -> dict:
    # Case-insensitive exact-match on a trimmed name. re.escape keeps any
    # regex metacharacters in user input (e.g. ".") literal.
    return {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"}


async def validate_department_create(
    *,
    tenant_id: str,
    name: str,
    code: Optional[str],
) -> None:
    """Synchronous pre-flight check used as the route-level gate.

    Raises before a write is enqueued so the client gets an immediate 409
    instead of a 202 followed by a failed-job notification.
    """
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    current_count = await count_departments({"tenant_id": tenant_id})
    await enforce_entity_cap(
        tenant_id=tenant_id,
        cap_key="max_departments",
        current_count=current_count,
        friendly_name="Department",
    )

    if code:
        existing_code = await get_department(
            {"tenant_id": tenant_id, "code": code}
        )
        if existing_code:
            raise HTTPException(
                status_code=409,
                detail="Department with this code already exists in tenant",
            )

    if name and name.strip():
        existing_name = await get_department(
            {"tenant_id": tenant_id, "name": _name_match_filter(name)}
        )
        if existing_name:
            raise HTTPException(
                status_code=409,
                detail="Department with this name already exists in tenant",
            )


async def validate_department_update(
    *,
    department_id: str,
    tenant_id: str,
    name: Optional[str],
    code: Optional[str],
) -> None:
    """Pre-flight check for renames/recodes; excludes the department itself."""
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    self_oid = ObjectId(department_id)

    if code:
        existing_code = await get_department(
            {
                "tenant_id": tenant_id,
                "code": code,
                "_id": {"$ne": self_oid},
            }
        )
        if existing_code:
            raise HTTPException(
                status_code=409,
                detail="Department with this code already exists in tenant",
            )

    if name and name.strip():
        existing_name = await get_department(
            {
                "tenant_id": tenant_id,
                "name": _name_match_filter(name),
                "_id": {"$ne": self_oid},
            }
        )
        if existing_name:
            raise HTTPException(
                status_code=409,
                detail="Department with this name already exists in tenant",
            )


async def add_department(
    dept_data: DepartmentCreate,
    created_by: Optional[str] = None,
    *,
    preassigned_id: Optional[str] = None,
) -> DepartmentOut:
    await validate_department_create(
        tenant_id=dept_data.tenant_id or "",
        name=dept_data.name,
        code=dept_data.code,
    )
    if created_by:
        dept_data.created_by = created_by
    return await create_department(dept_data, preassigned_id=preassigned_id)


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
    await validate_department_update(
        department_id=department_id,
        tenant_id=tenant_id,
        name=dept_data.name,
        code=dept_data.code,
    )
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
