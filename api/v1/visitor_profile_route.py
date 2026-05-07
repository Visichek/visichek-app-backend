from typing import Annotated, Any, List

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.entity_cache import get_or_compute_entity
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.visitor_profile_schema import VisitorProfileUpdate
from security.auth import verify_any_system_user_token, verify_system_user_token
from security.principal import AuthPrincipal
from services.visitor_profile_service import (
    retrieve_visitor_profile_by_id_with_summary,
    retrieve_visitor_profiles_with_summary,
    search_profiles,
)

router = APIRouter(prefix="/visitor-profiles", tags=["Visitor Profiles"])


@router.get("/search")
@document_response(
    message="Visitor profiles search results",
    description="Interactive search — runs live against the DB (no precompute).",
    summary="Search visitor profiles",
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def search_visitor_profiles_endpoint(
    q: str,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 20,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await search_profiles(tenant_id=tenant_id, query=q, start=start, stop=stop)


@router.get("")
@document_response(
    message="Visitor profiles fetched successfully",
    description="First page served from the per-tenant precompute cache.",
    summary="List visitor profiles",
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_visitor_profiles(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("dept_admin", "super_admin", "auditor")
    ),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if start == 0 and stop == 100 and tenant_id:
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="visitor_profiles.list",
            ttl=60,
            loader=lambda: _load_visitor_profiles_for_tenant(tenant_id),
        )
    return await retrieve_visitor_profiles_with_summary(
        tenant_id=tenant_id, start=start, stop=stop
    )


async def _load_visitor_profiles_for_tenant(tenant_id: str) -> List[Any]:
    profiles = await retrieve_visitor_profiles_with_summary(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [
        p.model_dump(mode="json", by_alias=True) if hasattr(p, "model_dump") else p
        for p in profiles
    ]


@router.get("/{profile_id}")
@document_response(
    message="Visitor profile fetched successfully",
    description="Retrieve a specific visitor profile by ID.",
    summary="Fetch visitor profile by ID",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Visitor profile not found",
    },
)
async def get_visitor_profile_endpoint(
    profile_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    return await get_or_compute_entity(
        entity_type="visitor_profile",
        entity_id=profile_id,
        loader=lambda: retrieve_visitor_profile_by_id_with_summary(
            profile_id=profile_id, tenant_id=tenant_id
        ),
    )


@router.patch("/{profile_id}")
@document_response(
    message="Visitor profile update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a partial visitor-profile update.",
    summary="Update visitor profile (async)",
    success_example={
        "id": "507f1f77bcf86cd799439012",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def update_visitor_profile_endpoint(
    profile_id: str,
    profile_data: VisitorProfileUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "dept_admin", "super_admin")
    ),
):
    tenant_id = principal.tenant_id or ""
    payload = profile_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    return await enqueue_write(
        writer_key="visitor_profile.update",
        payload=payload,
        resource_type="visitor_profile",
        resource_id=profile_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
