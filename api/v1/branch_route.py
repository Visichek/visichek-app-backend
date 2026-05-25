from __future__ import annotations

from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, Header, Request, status

from core.bulk import enqueue_bulk_write
from core.database import db
from core.idempotency import actor_scope, check_idempotency, store_idempotency
from core.list_params import FilterDef, ListSpec, parse_list_query
from core.list_runner import run_list
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


BRANCHES_LIST_SPEC = ListSpec(
    sortable_fields=frozenset({"name", "date_created", "is_active", "status"}),
    default_sort=(("name", 1),),
    search_fields=("name", "address", "city"),
    filters={
        "status": FilterDef(
            name="status",
            multi=True,
            allowed_values=frozenset({"active", "inactive", "all"}),
            builder=lambda vs: (
                {}
                if "all" in vs
                else {"is_active": vs[0] == "active"}
                if len(vs) == 1
                else {"is_active": {"$in": [v == "active" for v in vs]}}
            ),
        ),
        "country": FilterDef(name="country"),
        "state": FilterDef(name="state"),
        "city": FilterDef(name="city"),
    },
    facet_fields=frozenset({"status"}),
)


def _is_default_branch_listing(request: Request) -> bool:
    qp = request.query_params
    if any(qp.get(k) for k in ("q", "sort", "facets")):
        return False
    if any(k for k in qp.keys() if k not in {"skip", "limit"}):
        return False
    skip_raw = qp.get("skip", "0")
    limit_raw = qp.get("limit", str(BRANCHES_LIST_SPEC.default_limit))
    return skip_raw in ("0", "") and limit_raw == str(BRANCHES_LIST_SPEC.default_limit)


def _map_branch_doc(doc: dict[str, Any]) -> dict[str, Any]:
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


async def _branch_status_facet(
    collection: Any, filter_doc: dict[str, Any], field: str
) -> dict[str, int]:
    if field != "status":
        return {}
    base = {k: v for k, v in filter_doc.items() if k != "is_active"}
    active = await collection.count_documents({**base, "is_active": True})
    inactive = await collection.count_documents({**base, "is_active": False})
    return {"active": active, "inactive": inactive, "all": active + inactive}


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
    # tenant_id is token-derived, never client-supplied (no fallback to the
    # request body — a forged tenant_id must never win).
    data["tenant_id"] = principal.tenant_id or ""
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
    request: Request,
    principal: AuthPrincipal = Depends(_super_admin_dep),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return {
            "items": [],
            "meta": {"total": 0, "skip": 0, "limit": 25, "hasMore": False},
        }
    if _is_default_branch_listing(request):
        cached = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="branches.list",
            ttl=60,
            loader=lambda: _load_branches_for_tenant(tenant_id),
        )
        items = cached if isinstance(cached, list) else []
        limited = items[: BRANCHES_LIST_SPEC.default_limit]
        return {
            "items": limited,
            "meta": {
                "total": len(items),
                "skip": 0,
                "limit": BRANCHES_LIST_SPEC.default_limit,
                "hasMore": len(items) > BRANCHES_LIST_SPEC.default_limit,
            },
        }
    query = parse_list_query(request, BRANCHES_LIST_SPEC)
    return await run_list(
        collection=db.branches,
        query=query,
        base_filter={"tenant_id": tenant_id},
        map_doc=_map_branch_doc,
        facet_runner=_branch_status_facet,
    )


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


# ─── Bulk endpoints ───────────────────────────────────────────────────


def _branch_bulk_extras(principal: AuthPrincipal) -> dict[str, Any]:
    return {"tenant_scope": principal.tenant_id or ""}


@router.post("/bulk/deactivate")
@document_response(
    message="Bulk branch deactivate queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk deactivate branches",
)
async def bulk_deactivate_branches(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(_super_admin_dep),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/branches/bulk/deactivate",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="branch.bulk_deactivate",
        ids=payload.get("ids", []),
        resource_type="branch",
        extras=_branch_bulk_extras(principal),
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/branches/bulk/deactivate",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


@router.post("/bulk/delete")
@document_response(
    message="Bulk branch delete queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk delete branches",
)
async def bulk_delete_branches(
    request: Request,
    payload: dict = Body(...),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    principal: AuthPrincipal = Depends(_super_admin_dep),
):
    tenant_id = principal.tenant_id or ""
    scope = actor_scope(principal.user_id, principal.role)
    hit = check_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/branches/bulk/delete",
        body=payload,
    )
    if hit is not None:
        return hit.response
    response = await enqueue_bulk_write(
        writer_key="branch.bulk_delete",
        ids=payload.get("ids", []),
        resource_type="branch",
        extras=_branch_bulk_extras(principal),
        atomic=bool(payload.get("atomic", False)),
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    store_idempotency(
        key=idempotency_key,
        scope=scope,
        route="POST /v1/branches/bulk/delete",
        body=payload,
        response=response,
        status_code=status.HTTP_202_ACCEPTED,
    )
    return response


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
