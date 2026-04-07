from __future__ import annotations

"""
Branch service — business logic for tenant branch management.

Each tenant has at least one branch (created during bootstrap or first
branch creation). The maximum number of branches is controlled by the
tenant's subscription plan via ``TenantCapLimit.max_branches``.
"""

import time
from typing import List, Optional

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


async def add_branch(branch_data: BranchCreate) -> BranchOut:
    """Create a new branch for a tenant, enforcing plan limits."""
    # Check branch cap from tenant's plan
    await _enforce_branch_cap(branch_data.tenant_id)

    # Check for duplicate branch name within the same tenant
    existing = await get_branch({
        "tenant_id": branch_data.tenant_id,
        "name": branch_data.name,
    })
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Branch '{branch_data.name}' already exists for this tenant",
        )

    return await create_branch(branch_data)


async def retrieve_branch_by_id(branch_id: str) -> Optional[BranchOut]:
    if not ObjectId.is_valid(branch_id):
        return None
    return await get_branch({"_id": ObjectId(branch_id)})


async def retrieve_branches_for_tenant(
    tenant_id: str,
    start: int = 0,
    stop: int = 100,
) -> List[BranchOut]:
    """List all branches belonging to a tenant."""
    return await get_branches({"tenant_id": tenant_id}, start=start, stop=stop)


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
    active_count = await count_branches({
        "tenant_id": branch.tenant_id,
        "status": "active",
    })
    if active_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate the last active branch for a tenant",
        )

    return await update_branch_by_id(
        branch_id,
        BranchUpdate(status="inactive"),
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
