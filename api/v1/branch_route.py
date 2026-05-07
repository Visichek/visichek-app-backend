from __future__ import annotations

from typing import Annotated, Any, List

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.branch_schema import BranchCreate, BranchUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.branch_service import (
    retrieve_branch_by_id,
    retrieve_branches_for_tenant,
)

router = APIRouter(prefix="/branches", tags=["Branches"])

# Only super_admin can manage branches
_super_admin_dep = verify_system_user_token("super_admin")


@router.post("")
@document_response(
    message="Branch creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a branch create for the authenticated user's tenant. Cap + duplicate-name checks run inside the writer.",
    summary="Create branch (async)",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - only super_admin can create branches",
    },
)
async def create_branch_endpoint(
    payload: BranchCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_super_admin_dep),
):
    """Enqueue branch creation. tenant_id is always enforced from the token."""
    data = payload.model_dump(exclude_none=True)
    data["tenant_id"] = principal.tenant_id or data.get("tenant_id")
    return await enqueue_write(
        writer_key="branch.create",
        payload=data,
        resource_type="branch",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Branches fetched successfully",
    description="List all branches for the authenticated user's tenant. First page served from the per-tenant precompute cache.",
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
) -> Any:
    tenant_id = principal.tenant_id or ""
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="branches.list",
            ttl=60,
            loader=lambda: _load_branches_for_tenant(tenant_id),
        )
    return await retrieve_branches_for_tenant(tenant_id, start=start, stop=stop)


async def _load_branches_for_tenant(tenant_id: str) -> List[Any]:
    branches = await retrieve_branches_for_tenant(tenant_id, start=0, stop=100)
    return [
        b.model_dump(mode="json", by_alias=True) if hasattr(b, "model_dump") else b
        for b in branches
    ]


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
) -> Any:
    branch = await get_or_compute_entity(
        entity_type="branch",
        entity_id=branch_id,
        loader=lambda: retrieve_branch_by_id(branch_id),
    )
    # Cross-tenant filter is applied AFTER the read-through cache so the
    # cache itself stays scope-agnostic (one Redis key per branch),
    # while never leaking data outside the owning tenant.
    if branch:
        owning_tenant = (
            branch.get("tenant_id") if isinstance(branch, dict) else branch.tenant_id
        )
        if owning_tenant != principal.tenant_id:
            return None
    return branch


@router.put("/{branch_id}")
@document_response(
    message="Branch update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a branch update.",
    summary="Update branch (async)",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
    },
)
async def update_branch_endpoint(
    branch_id: str,
    payload: BranchUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_super_admin_dep),
):
    # Cross-tenant check still runs synchronously to avoid leaking write errors.
    existing = await retrieve_branch_by_id(branch_id)
    if not existing or existing.tenant_id != principal.tenant_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Branch not found")
    data = payload.model_dump(exclude_none=True)
    data["tenant_id"] = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="branch.update",
        payload=data,
        resource_type="branch",
        resource_id=branch_id,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/{branch_id}/deactivate")
@document_response(
    message="Branch deactivation queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue soft-deactivation. Last-active-branch protection runs inside the writer.",
    summary="Deactivate branch (async)",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "job_id": "c4e6f8a0-3456-4fab-9bcd-2345678901cd",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
        404: "Branch not found",
    },
)
async def deactivate_branch_endpoint(
    branch_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(_super_admin_dep),
):
    existing = await retrieve_branch_by_id(branch_id)
    if not existing or existing.tenant_id != principal.tenant_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Branch not found")
    return await enqueue_write(
        writer_key="branch.deactivate",
        payload={"tenant_id": principal.tenant_id or ""},
        resource_type="branch",
        resource_id=branch_id,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{branch_id}")
@document_response(
    message="Branch deletion queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue hard delete. Last-branch protection runs inside the writer.",
    summary="Delete branch (async)",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "job_id": "d5f7a9b1-4567-4abc-8def-3456789012de",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized",
        403: "Forbidden",
        404: "Branch not found",
    },
)
async def delete_branch_endpoint(
    branch_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(_super_admin_dep),
):
    existing = await retrieve_branch_by_id(branch_id)
    if not existing or existing.tenant_id != principal.tenant_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Branch not found")
    return await enqueue_write(
        writer_key="branch.delete",
        payload={"tenant_id": principal.tenant_id or ""},
        resource_type="branch",
        resource_id=branch_id,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
