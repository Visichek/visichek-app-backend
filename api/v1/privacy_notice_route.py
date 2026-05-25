from typing import Annotated, Any, List, Tuple

from fastapi import APIRouter, Depends, Query, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.privacy_notice_schema import PrivacyNoticeCreate, PrivacyNoticeUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal
from services.privacy_notice_service import (
    count_privacy_notices_for_tenant,
    retrieve_active_notice,
    retrieve_privacy_notices,
)

router = APIRouter(prefix="/privacy-notices", tags=["Privacy Notices"])

_admin_roles = verify_system_user_token("super_admin", "dpo")


@router.post("")
@document_response(
    message="Privacy notice creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create privacy notice (async)",
    description="Enqueue a privacy notice create. Only super_admin and dpo roles can submit.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        422: "Invalid payload",
    },
)
async def create_privacy_notice_endpoint(
    notice_data: PrivacyNoticeCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    payload = notice_data.model_dump(exclude_none=True)
    # tenant_id is token-derived, never client-supplied: overwrite any value
    # that came in on the request body with the authenticated tenant.
    payload["tenant_id"] = principal.tenant_id or ""
    request_id = getattr(request.state, "request_id", None)
    # Thread actor metadata so the writer can record an audit event.
    payload["_actor_id"] = principal.user_id
    payload["_actor_role"] = principal.role
    payload["_request_id"] = request_id
    return await enqueue_write(
        writer_key="privacy_notice.create",
        payload=payload,
        resource_type="privacy_notice",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )


@router.get("/active")
@document_response(
    message="Active privacy notice fetched successfully",
    summary="Get active privacy notice",
    description="Served from the per-tenant precompute cache.",
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        404: "Notice not found",
    },
)
async def get_active_notice(
    principal: AuthPrincipal = Depends(
        verify_system_user_token("super_admin", "dpo", "receptionist", "dept_admin")
    ),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return await retrieve_active_notice(tenant_id=tenant_id)
    # Seed-on-read: a tenant that never authored a notice still gets the
    # VisiChek default so the kiosk consent gate works on day one. The seed
    # is a one-time write on the first cache miss; subsequent reads are warm.
    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="privacy_notice.active",
        ttl=60,
        loader=lambda: _load_active_notice(tenant_id),
    )


async def _load_active_notice(tenant_id: str) -> Any:
    notice = await retrieve_active_notice(tenant_id=tenant_id, seed_if_missing=True)
    return (
        notice.model_dump(mode="json", by_alias=True)
        if hasattr(notice, "model_dump")
        else notice
    )


@router.get("")
@document_response(
    message="Privacy notices fetched successfully",
    summary="List privacy notices",
    description="First-page reads from the per-tenant precompute cache; pagination falls through to live.",
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions"},
)
async def list_privacy_notices(
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0, le=200)] = 100,
    principal: AuthPrincipal = Depends(_admin_roles),
) -> Tuple[List[Any], dict]:
    tenant_id = principal.tenant_id or ""
    total = await count_privacy_notices_for_tenant(tenant_id) if tenant_id else 0
    meta = {"total": total, "skip": skip, "limit": limit}
    if skip == 0 and limit == 100 and tenant_id:
        items = await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource="privacy_notices.list",
            ttl=60,
            loader=lambda: _load_notices_for_tenant(tenant_id),
        )
        return items, meta
    notices = await retrieve_privacy_notices(
        tenant_id=tenant_id, start=skip, stop=skip + limit
    )
    items = [
        n.model_dump(mode="json", by_alias=True) if hasattr(n, "model_dump") else n
        for n in notices
    ]
    return items, meta


async def _load_notices_for_tenant(tenant_id: str) -> List[Any]:
    notices = await retrieve_privacy_notices(tenant_id=tenant_id, start=0, stop=100)
    return [
        n.model_dump(mode="json", by_alias=True) if hasattr(n, "model_dump") else n
        for n in notices
    ]


@router.patch("/{notice_id}")
@document_response(
    message="Privacy notice update queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update privacy notice (async)",
    description="Enqueue a partial privacy notice update.",
    success_example={
        "id": "507f1f77bcf86cd799439011",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        422: "Invalid payload",
    },
)
async def update_privacy_notice_endpoint(
    notice_id: str,
    notice_data: PrivacyNoticeUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    tenant_id = principal.tenant_id or ""
    payload = notice_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    request_id = getattr(request.state, "request_id", None)
    # Thread actor metadata so the writer can record an audit event.
    payload["_actor_id"] = principal.user_id
    payload["_actor_role"] = principal.role
    payload["_request_id"] = request_id
    return await enqueue_write(
        writer_key="privacy_notice.update",
        payload=payload,
        resource_type="privacy_notice",
        resource_id=notice_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=request_id,
    )
