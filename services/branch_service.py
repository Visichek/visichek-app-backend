from __future__ import annotations

"""
Branch service — business logic for tenant branch management.

Each tenant has at least one branch (created during bootstrap or first
branch creation). The maximum number of branches is controlled by the
tenant's subscription plan via ``TenantCapLimit.max_branches``.
"""

from typing import Any, List, Optional

from bson import ObjectId
from fastapi import HTTPException, status

from repositories.branch_repo import (
    count_branches,
    create_branch,
    delete_branch,
    get_branch,
    get_branches,
    update_branch,
)
from schemas.branch_schema import BranchCreate, BranchOut, BranchUpdate
from schemas.branch_schema import BranchStatus


async def validate_branch_contact_user(
    tenant_id: str,
    contact_user_id: str,
    branch_id: Optional[str] = None,
) -> None:
    """Validate a designated branch point-of-contact.

    Rules (raises ``HTTPException`` on violation):

    * must be a valid ObjectId belonging to a system_user of the SAME tenant;
    * if that user has a branch-scoped role (dept_admin / receptionist /
      security_officer), ``branch_id`` must be in their ``branch_ids`` — a
      contact must be able to see the branch they front. On CREATE the branch
      is new, so a branch-scoped user can only be designated after being
      assigned to it (update flow).
    """
    if not ObjectId.is_valid(contact_user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid contact_user_id format",
        )
    from repositories.system_user_repo import get_system_user

    user = await get_system_user(
        {"_id": ObjectId(contact_user_id), "tenant_id": tenant_id}
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact user not found for this organization",
        )
    from security.principal import BRANCH_SCOPED_ROLES

    role = getattr(user.role, "value", None) or str(user.role)
    if role in BRANCH_SCOPED_ROLES:
        assigned = list(getattr(user, "branch_ids", None) or [])
        if not branch_id or branch_id not in assigned:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This user has a branch-scoped role and is not assigned to "
                    "this branch, so they cannot be its point of contact."
                ),
            )


async def get_branch_contact_summary(branch: BranchOut):
    """Resolve the point-of-contact card for a branch (never raises)."""
    from services.summary_resolver import resolve_contact_summary

    return await resolve_contact_summary(
        tenant_id=branch.tenant_id,
        contact_user_id=branch.contact_user_id,
        branch_name=branch.name,
        branch_email=branch.email,
        branch_phone=branch.phone,
    )


async def get_org_contact_summary(tenant_id: str):
    """Point-of-contact card for the organization: the main super admin."""
    from services.summary_resolver import resolve_contact_summary

    return await resolve_contact_summary(tenant_id=tenant_id)


async def _enrich_branch_with_contact(branch: BranchOut) -> BranchOut:
    try:
        branch.contact_summary = await get_branch_contact_summary(branch)
    except Exception:
        # Enrichment is best-effort — never break a branch read.
        branch.contact_summary = None
    return branch


async def add_branch(
    branch_data: BranchCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> BranchOut:
    """Create a new branch for a tenant, enforcing plan limits."""
    # Check branch cap from tenant's plan
    await _enforce_branch_cap(branch_data.tenant_id)

    if branch_data.contact_user_id:
        await validate_branch_contact_user(
            branch_data.tenant_id,
            branch_data.contact_user_id,
            branch_id=preassigned_id,
        )

    # Check for duplicate branch name within the same tenant
    existing = await get_branch(
        {
            "tenant_id": branch_data.tenant_id,
            "name": branch_data.name,
        }
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Branch '{branch_data.name}' already exists for this tenant",
        )

    return await create_branch(branch_data, preassigned_id=preassigned_id)


async def retrieve_branch_by_id(branch_id: str) -> Optional[BranchOut]:
    if not ObjectId.is_valid(branch_id):
        return None
    branch = await get_branch({"_id": ObjectId(branch_id)})
    if branch is None:
        return None
    return await _enrich_branch_with_contact(branch)


async def retrieve_branches_for_tenant(
    tenant_id: str,
    start: int = 0,
    stop: int = 100,
) -> List[BranchOut]:
    """List all branches belonging to a tenant, with contact_summary enriched
    (feeds both the ``branches.list`` precompute loader and the route-side
    fallback loader)."""
    import asyncio

    branches = await get_branches({"tenant_id": tenant_id}, start=start, stop=stop)
    if branches:
        await asyncio.gather(*(_enrich_branch_with_contact(b) for b in branches))
    return branches


async def count_tenant_branches(tenant_id: str) -> int:
    """Count branches for a given tenant."""
    return await count_branches({"tenant_id": tenant_id})


async def update_branch_by_id(
    branch_id: str,
    update_data: BranchUpdate,
) -> Optional[BranchOut]:
    if not ObjectId.is_valid(branch_id):
        return None
    return await update_branch({"_id": ObjectId(branch_id)}, update_data)


async def deactivate_branch(branch_id: str) -> Optional[BranchOut]:
    """Soft-deactivate a branch (cannot delete, only deactivate)."""
    branch = await retrieve_branch_by_id(branch_id)
    if not branch:
        return None

    # Prevent deactivating the last active branch
    active_count = await count_branches(
        {
            "tenant_id": branch.tenant_id,
            "status": "active",
        }
    )
    if active_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate the last active branch for a tenant",
        )

    return await update_branch_by_id(
        branch_id,
        BranchUpdate(status=BranchStatus("inactive")),
    )


async def remove_branch(branch_id: str) -> bool:
    """Hard-delete a branch. Only allowed if tenant has more than one branch."""
    branch = await retrieve_branch_by_id(branch_id)
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found",
        )

    total = await count_branches({"tenant_id": branch.tenant_id})
    if total <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the last branch for a tenant. Each tenant must have at least one branch.",
        )

    return await delete_branch({"_id": ObjectId(branch_id)})


async def ensure_default_branch(tenant_id: str, company_name: str) -> BranchOut:
    """Create a default 'Headquarters' branch if no branches exist for the tenant."""
    existing_count = await count_branches({"tenant_id": tenant_id})
    if existing_count > 0:
        branches = await get_branches({"tenant_id": tenant_id}, start=0, stop=1)
        return branches[0]

    branch_data = BranchCreate(
        tenant_id=tenant_id,
        name=f"{company_name} - Headquarters",
        is_headquarters=True,
    )
    return await create_branch(branch_data)


async def resolve_hq_branch_id(tenant_id: str) -> Optional[str]:
    """Return the tenant's headquarters branch id — the "HQ data" bucket.

    Records created without an explicit branch (and all historical
    branch-null data) belong to HQ. Prefers the ``is_headquarters`` branch,
    falls back to the oldest branch, and returns ``None`` only when the
    tenant has no branches at all (which bootstrap should have prevented).
    """
    hq = await get_branch({"tenant_id": tenant_id, "is_headquarters": True})
    if hq:
        return hq.id
    branches = await get_branches({"tenant_id": tenant_id}, start=0, stop=1)
    return branches[0].id if branches else None


async def resolve_branch_for_principal(
    principal: Any,
    tenant_id: str,
    explicit_branch_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve the branch a new record should be tagged with.

    Policy (see the branch-separation rule):

    * **Branch-scoped roles** (``dept_admin`` / ``receptionist`` /
      ``security_officer``) always write to their own assigned branch. An
      explicit ``branch_id`` outside their assignment is rejected with 403
      — they must not be able to tag data to a branch they can't see.
    * **Unscoped roles** (``super_admin``, app admins) may pass an explicit
      ``branch_id`` (validated to belong to the tenant); otherwise the
      record falls back to the tenant's HQ branch.

    Returns ``None`` only when the tenant genuinely has no branches.
    """
    is_branch_scoped = bool(getattr(principal, "is_branch_scoped", False))
    assigned = list(getattr(principal, "branch_ids", None) or [])

    if is_branch_scoped:
        if explicit_branch_id and explicit_branch_id not in assigned:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only create records for a branch you are assigned to.",
            )
        if explicit_branch_id:
            return explicit_branch_id
        if assigned:
            return assigned[0]
        return await resolve_hq_branch_id(tenant_id)

    # Unscoped roles: honour an explicit, tenant-owned branch; else HQ.
    if explicit_branch_id:
        if not ObjectId.is_valid(explicit_branch_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid branch ID format",
            )
        branch = await get_branch(
            {"_id": ObjectId(explicit_branch_id), "tenant_id": tenant_id}
        )
        if not branch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found for this tenant",
            )
        return explicit_branch_id
    return await resolve_hq_branch_id(tenant_id)


async def branch_scope_filter(
    principal: Any,
    tenant_id: str,
    field: str = "branch_id",
) -> Optional[dict]:
    """Mongo filter fragment that scopes a list query to a branch-scoped
    principal's branches — merge it into a query's ``filter_dict``.

    * Returns ``None`` for **unscoped** principals (super_admin / auditor /
      dpo / app admins) — they see every branch ("full details").
    * Returns ``{"branch_id": {"$in": [...]}}`` for **branch-scoped** roles
      (dept_admin / receptionist / security_officer): only rows tagged with
      one of their assigned branches.

    A scoped user with no branches matches nothing (``$in: []``) — fail
    closed, never wide-open. Legacy null-branch records are migrated to the
    tenant HQ branch by ``services.branch_backfill`` so HQ-assigned users
    still see them; reads stay a pure, DB-free comparison on the token's
    ``branch_ids`` (mirrors ``AuthPrincipal.branch_filter``).

    ``tenant_id`` is accepted for call-site symmetry but unused — kept so
    callers don't have to special-case scoped vs. unscoped resolution.
    """
    _ = tenant_id
    if not getattr(principal, "is_branch_scoped", False):
        return None
    ids = list(getattr(principal, "branch_ids", None) or [])
    return {field: {"$in": ids}}


async def filter_items_for_branch(
    principal: Any,
    tenant_id: str,
    items: list,
    field: str = "branch_id",
) -> list:
    """In-memory counterpart to :func:`branch_scope_filter` for already
    materialized lists (e.g. the precompute cache, which holds enriched
    dicts). Unscoped principals get the list unchanged.

    Accepts dicts (snake_case or camelCase keys) or model objects. Like
    :func:`branch_scope_filter`, this is a pure comparison against the
    token's ``branch_ids`` — no DB lookup.
    """
    _ = tenant_id
    if not getattr(principal, "is_branch_scoped", False):
        return items
    ids = set(getattr(principal, "branch_ids", None) or [])
    camel = "".join(
        part.capitalize() if i else part for i, part in enumerate(field.split("_"))
    )

    def _branch_of(item: Any) -> Optional[str]:
        if isinstance(item, dict):
            return item.get(field) or item.get(camel)
        return getattr(item, field, None)

    return [it for it in items if _branch_of(it) in ids]


async def lock_down_to_hq(tenant_id: str) -> int:
    """Deactivate every non-HQ branch for a tenant.

    Called when a tenant's subscription drops to the Free plan (which
    only allows one location). The HQ branch is kept ACTIVE; everything
    else is flipped to ``status=inactive`` so existing flows that filter
    by status (visitors, kiosks, dashboards) naturally exclude them.

    Returns the number of branches deactivated. Idempotent — branches
    already inactive are skipped.
    """
    from core.database import db as _db
    import time as _time

    now = int(_time.time())
    result = await _db["branches"].update_many(
        {
            "tenant_id": tenant_id,
            "is_headquarters": {"$ne": True},
            "status": "active",
        },
        {"$set": {"status": "inactive", "last_updated": now}},
    )
    return getattr(result, "modified_count", 0) or 0


async def enforce_branch_lock(tenant_id: str) -> int:
    """Lock branches beyond the tenant's CURRENT effective ``max_branches``.

    Called after an addon expiry/cancel/renewal-failure drops the
    addon-inclusive branch cap (see ``plan_cache_service._apply_addon_benefits``
    — ``tenant_caps.max_branches`` already folds in active ``branch_quota``
    addon benefit). Keeps HQ + the N-1 oldest other branches active; locks
    (``status=inactive``, mirroring ``lock_down_to_hq``) the newest branches
    beyond the cap.

    Uses the EXACT same sort order as
    ``services.me_limitations_service._list_locked_branch_ids``
    (``is_headquarters`` desc, then ``date_created`` asc) so the tenant's
    read-time ``lockedEntities.branches`` list always matches which branches
    are actually inactive here. Idempotent and one-way: never reactivates a
    branch that's back within cap (mirrors ``lock_down_to_hq``'s semantics —
    unlocking is a deliberate admin/support action, not automatic).

    Returns the number of branches newly locked. No-op (0) when the cap is
    unlimited (``None``) or plan resolution fails (fails open).
    """
    try:
        from services.plan_cache_service import resolve_tenant_plan

        plan_data = await resolve_tenant_plan(tenant_id)
    except Exception:
        return 0
    if not plan_data:
        return 0

    max_branches = (plan_data.get("tenant_caps") or {}).get("max_branches")
    if max_branches is None:
        return 0

    from core.database import db as _db
    import time as _time

    cursor = (
        _db["branches"]
        .find(
            {"tenant_id": tenant_id},
            projection={"_id": 1, "is_headquarters": 1, "status": 1},
        )
        .sort([("is_headquarters", -1), ("date_created", 1)])
    )
    to_lock: list = []
    seen = 0
    async for doc in cursor:
        seen += 1
        if seen > int(max_branches) and doc.get("status") != "inactive":
            to_lock.append(doc["_id"])

    if not to_lock:
        return 0
    now = int(_time.time())
    result = await _db["branches"].update_many(
        {"_id": {"$in": to_lock}},
        {"$set": {"status": "inactive", "last_updated": now}},
    )
    return getattr(result, "modified_count", 0) or 0


async def _enforce_branch_cap(tenant_id: str) -> None:
    """Check if tenant has reached their plan's max_branches limit."""
    try:
        from services.plan_cache_service import resolve_tenant_plan

        plan_data = await resolve_tenant_plan(tenant_id)
    except Exception:
        # If plan resolution fails (no subscription, etc.), allow branch creation
        return

    if not plan_data:
        return

    tenant_caps = plan_data.get("tenant_caps", {})
    max_branches = tenant_caps.get("max_branches")
    if max_branches is None:
        return  # No limit

    current_count = await count_branches({"tenant_id": tenant_id})
    if current_count >= max_branches:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Branch limit reached ({max_branches}). Upgrade your plan for more branches.",
        )
