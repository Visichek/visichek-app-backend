import re
from bson import ObjectId
from fastapi import HTTPException, status
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
from services.branch_service import resolve_hq_branch_id
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
    branch_id: Optional[str] = None,
) -> str:
    """Synchronous pre-flight check used as the route-level gate.

    Raises before a write is enqueued so the client gets an immediate 409
    instead of a 202 followed by a failed-job notification.

    Returns the RESOLVED branch id so the caller can stamp it on the write
    payload. Departments are branch-scoped: the cap and both uniqueness
    checks are per-branch, so a six-property group gets its plan's
    ``max_departments`` at each property and Imperial and Elvis may each
    have their own "Front Office". A single-branch tenant supplies no
    branch_id and everything resolves to HQ, which is behaviourally
    identical to the previous tenant-wide rule.
    """
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    resolved_branch_id = branch_id or await resolve_hq_branch_id(tenant_id)
    if not resolved_branch_id:
        raise HTTPException(
            status_code=409,
            detail="Tenant has no branch — cannot create a department",
        )

    scope = {"tenant_id": tenant_id, "branch_id": resolved_branch_id}

    current_count = await count_departments(scope)
    await enforce_entity_cap(
        tenant_id=tenant_id,
        cap_key="max_departments",
        current_count=current_count,
        friendly_name="Department",
    )

    if code:
        existing_code = await get_department({**scope, "code": code})
        if existing_code:
            raise HTTPException(
                status_code=409,
                detail="Department with this code already exists in this branch",
            )

    if name and name.strip():
        existing_name = await get_department({**scope, "name": _name_match_filter(name)})
        if existing_name:
            raise HTTPException(
                status_code=409,
                detail="Department with this name already exists in this branch",
            )

    return resolved_branch_id


async def validate_department_update(
    *,
    department_id: str,
    tenant_id: str,
    name: Optional[str],
    code: Optional[str],
) -> None:
    """Pre-flight check for renames/recodes; excludes the department itself.

    Uniqueness is scoped to the department's OWN branch, so two properties
    may each run a "Front Office".
    """
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    self_oid = ObjectId(department_id)

    current = await get_department({"_id": self_oid, "tenant_id": tenant_id})
    if current is None:
        raise HTTPException(status_code=404, detail="Department not found")

    scope: dict = {"tenant_id": tenant_id, "_id": {"$ne": self_oid}}
    # Legacy rows the backfill has not tagged yet carry no branch_id; fall
    # back to tenant-wide uniqueness for those rather than matching on null.
    if current.branch_id:
        scope["branch_id"] = current.branch_id

    if code:
        existing_code = await get_department({**scope, "code": code})
        if existing_code:
            raise HTTPException(
                status_code=409,
                detail="Department with this code already exists in this branch",
            )

    if name and name.strip():
        existing_name = await get_department({**scope, "name": _name_match_filter(name)})
        if existing_name:
            raise HTTPException(
                status_code=409,
                detail="Department with this name already exists in this branch",
            )


async def get_accessible_department_ids(tenant_id: str) -> Optional[set[str]]:
    """Return the set of department IDs the tenant may currently interact with.

    Returns ``None`` if the tenant has no plan-level cap on departments
    (Premium with overrides, Enterprise) — meaning every department is
    accessible. Otherwise returns the set of the OLDEST ``cap`` active
    departments by ``date_created``; everything beyond the cap is
    locked-out (read AND write) until either the cap is raised or the
    excess departments are deleted.

    Sort order is stable (oldest first) so a tenant who downgrades and
    re-upgrades sees the same primary department both times.
    """
    from services.plan_cache_service import resolve_tenant_plan

    try:
        plan_data = await resolve_tenant_plan(tenant_id)
    except Exception:
        return None

    if not plan_data:
        return None

    caps = plan_data.get("tenant_caps") or {}
    cap = caps.get("max_departments")
    if cap is None:
        return None

    from core.database import db as _db

    cursor = (
        _db["departments"]
        .find({"tenant_id": tenant_id}, projection={"_id": 1})
        .sort("date_created", 1)
        .limit(int(cap))
    )
    allowed: set[str] = set()
    async for doc in cursor:
        _id = doc.get("_id")
        if _id is not None:
            allowed.add(str(_id))
    return allowed


async def enforce_department_access(tenant_id: str, department_id: str) -> None:
    """Raise HTTP 403 if the department is locked out by the tenant's plan.

    Use at the top of every department-scoped service write OR direct
    read. Listings should call ``filter_to_accessible`` on the result
    so locked departments simply don't appear, rather than 403'ing
    individual reads from a list page.
    """
    accessible = await get_accessible_department_ids(tenant_id)
    if accessible is None:
        return  # No cap → all accessible
    if department_id not in accessible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This department is locked under your current plan. "
                "Upgrade your plan or delete the department to interact with it."
            ),
        )


async def lock_down_to_department_cap(tenant_id: str) -> int:
    """Mark every department beyond the plan's ``max_departments`` cap inactive.

    Mirrors ``branch_service.lock_down_to_hq``. Called from
    ``transition_tenant_to_free_plan`` so a tenant dropping from Premium
    (15 departments) to Free (1 department) sees their excess departments
    flipped to ``is_active=false`` rather than silently still queryable.

    Returns the number of departments deactivated. Idempotent.
    """
    from core.database import db as _db
    import time as _time

    accessible = await get_accessible_department_ids(tenant_id)
    if accessible is None:
        return 0  # No cap

    now = int(_time.time())
    object_ids = [ObjectId(d) for d in accessible if ObjectId.is_valid(d)]
    result = await _db["departments"].update_many(
        {
            "tenant_id": tenant_id,
            "_id": {"$nin": object_ids},
            "is_active": True,
        },
        {"$set": {"is_active": False, "last_updated": now}},
    )
    return getattr(result, "modified_count", 0) or 0


async def add_department(
    dept_data: DepartmentCreate,
    created_by: Optional[str] = None,
    *,
    preassigned_id: Optional[str] = None,
) -> DepartmentOut:
    resolved_branch_id = await validate_department_create(
        tenant_id=dept_data.tenant_id or "",
        name=dept_data.name,
        code=dept_data.code,
        branch_id=dept_data.branch_id,
    )
    dept_data.branch_id = resolved_branch_id
    if created_by:
        dept_data.created_by = created_by
    return await create_department(dept_data, preassigned_id=preassigned_id)


async def retrieve_department_by_id(
    department_id: str, tenant_id: str
) -> DepartmentOut:
    if not ObjectId.is_valid(department_id):
        raise HTTPException(status_code=400, detail="Invalid department ID format")
    # Plan-level lockdown: when the tenant has more departments than
    # the plan permits, only the oldest N are accessible. Calls against
    # locked departments return 403 with a clear upgrade message.
    await enforce_department_access(tenant_id=tenant_id, department_id=department_id)
    result = await get_department(
        {"_id": ObjectId(department_id), "tenant_id": tenant_id}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Department not found")
    return result


async def retrieve_departments(
    tenant_id: str, start=0, stop=100
) -> List[DepartmentOut]:
    """List departments. Departments locked by the plan cap are returned
    with an extra ``is_locked=True`` marker via the schema's ``is_active``
    field already being False, so the frontend can render them as
    "locked under your current plan" rows. Locked rows are still RETURNED
    so super_admins can see what they have and decide what to delete or
    upgrade for — they just can't be interacted with (writes, member
    assignments, visitor flows) per ``enforce_department_access``.
    """
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
    # Plan-level lockdown: locked departments cannot be edited.
    await enforce_department_access(tenant_id=tenant_id, department_id=department_id)
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
