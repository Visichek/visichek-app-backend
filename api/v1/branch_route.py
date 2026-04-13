from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.branch_schema import BranchCreate, BranchOut, BranchUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.branch_service import (
    add_branch,
    deactivate_branch,
    remove_branch,
    retrieve_branch_by_id,
    retrieve_branches_for_tenant,
    update_branch_by_id,
)

router = APIRouter(prefix="/branches", tags=["Branches"])

# Only super_admin can manage branches
_super_admin_dep = verify_system_user_token("super_admin")


@router.post("")
@document_response(
    message="Branch created successfully",
    status_code=status.HTTP_201_CREATED,
    description="Create a new branch for the authenticated user's tenant. Only super_admin can create branches.",
    summary="Create branch",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "tenant_id": "507f1f77bcf86cd799439010",
        "name": "Lagos Office",
        "address": "123 Victoria Island",
        "city": "Lagos",
        "state": "Lagos",
        "country": "Nigeria",
        "is_headquarters": False,
        "status": "active",
        "date_created": 1712548800,
        "last_updated": 1712548800,
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - only super_admin can create branches",
        409: "Conflict - branch name already exists for this tenant",
        429: "Too Many Requests - branch limit reached for plan",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Token role mismatch",
            "code": "AUTH_ROLE_MISMATCH",
        },
        409: {
            "success": False,
            "message": "Branch 'Lagos Office' already exists for this tenant",
            "code": "VALIDATION_FAILED",
        },
        429: {
            "success": False,
            "message": "Branch limit reached (3). Upgrade your plan for more branches.",
            "code": "QUOTA_EXCEEDED",
        },
    },
)
async def create_branch_endpoint(
    payload: BranchCreate,
    principal: AuthPrincipal = Depends(_super_admin_dep),
) -> BranchOut:
    """Create a new branch. The tenant_id is taken from the authenticated user's token."""
    # Override tenant_id from token for security (prevent creating branches for other tenants)
    payload.tenant_id = principal.tenant_id or payload.tenant_id
    return await add_branch(payload)


@router.get("")
@document_response(
    message="Branches fetched successfully",
    description="List all branches for the authenticated user's tenant.",
    summary="List branches",
    include_meta=True,
    success_example=[
        {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "507f1f77bcf86cd799439010",
            "name": "Headquarters",
            "is_headquarters": True,
            "status": "active",
        }
    ],
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - only super_admin can list branches",
    },
)
async def list_branches(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(_super_admin_dep),
):
    """List all branches for the current tenant."""
    tenant_id = principal.tenant_id or ""
    return await retrieve_branches_for_tenant(tenant_id, start=start, stop=stop)


@router.get("/{branch_id}")
@document_response(
    message="Branch fetched successfully",
    description="Retrieve a specific branch by its ID.",
    summary="Get branch",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
        404: "Branch not found",
    },
)
async def get_branch_endpoint(
    branch_id: str,
    principal: AuthPrincipal = Depends(_super_admin_dep),
) -> BranchOut | None:
    """Get a single branch by ID."""
    branch = await retrieve_branch_by_id(branch_id)
    if branch and branch.tenant_id != principal.tenant_id:
        return None  # Don't leak data across tenants
    return branch


@router.put("/{branch_id}")
@document_response(
    message="Branch updated successfully",
    description="Update an existing branch.",
    summary="Update branch",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
        404: "Branch not found",
    },
)
async def update_branch_endpoint(
    branch_id: str,
    payload: BranchUpdate,
    principal: AuthPrincipal = Depends(_super_admin_dep),
) -> BranchOut | None:
    """Update a branch."""
    # Verify branch belongs to tenant
    existing = await retrieve_branch_by_id(branch_id)
    if not existing or existing.tenant_id != principal.tenant_id:
        return None
    return await update_branch_by_id(branch_id, payload)


@router.post("/{branch_id}/deactivate")
@document_response(
    message="Branch deactivated successfully",
    description="Soft-deactivate a branch. Cannot deactivate the last active branch.",
    summary="Deactivate branch",
    response_codes={
        400: "Cannot deactivate the last active branch",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Branch not found",
    },
)
async def deactivate_branch_endpoint(
    branch_id: str,
    principal: AuthPrincipal = Depends(_super_admin_dep),
) -> BranchOut | None:
    """Deactivate a branch (soft-delete)."""
    existing = await retrieve_branch_by_id(branch_id)
    if not existing or existing.tenant_id != principal.tenant_id:
        return None
    return await deactivate_branch(branch_id)


@router.delete("/{branch_id}")
@document_response(
    message="Branch deleted successfully",
    description="Permanently delete a branch. Cannot delete the last branch for a tenant.",
    summary="Delete branch",
    response_codes={
        400: "Cannot delete the last branch",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Branch not found",
    },
)
async def delete_branch_endpoint(
    branch_id: str,
    principal: AuthPrincipal = Depends(_super_admin_dep),
) -> dict:
    """Hard-delete a branch."""
    existing = await retrieve_branch_by_id(branch_id)
    if not existing or existing.tenant_id != principal.tenant_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Branch not found")
    await remove_branch(branch_id)
    return {"deleted": True}
