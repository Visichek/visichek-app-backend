from typing import Annotated, Any, List, Tuple

from fastapi import APIRouter, Depends, Query, Request, status

from core.errors import AppException, ErrorCode
from core.queue.precompute import PrecomputeScope, get_or_compute
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


def _managed_by_platform() -> AppException:
    """The visitor privacy notice is now derived from the platform-managed
    Visitor Privacy Policy master (authored by the application admin and
    accepted via /v1/agreements). Tenants can no longer author their own."""
    return AppException(
        status_code=status.HTTP_409_CONFLICT,
        code=ErrorCode.CONFLICT,
        message=(
            "The visitor privacy notice is managed by the platform and derived "
            "from the Visitor Privacy Policy. It can no longer be edited per "
            "tenant. View it via GET /v1/privacy-notices/active or "
            "GET /v1/agreements/visitor_privacy_policy."
        ),
        details={"code": "PRIVACY_NOTICE_MANAGED_BY_PLATFORM"},
    )


@router.post("", deprecated=True)
@document_response(
    message="Privacy notice authoring is managed by the platform",
    summary="Create privacy notice (DISABLED — managed by platform)",
    description=(
        "DISABLED. The visitor privacy notice is derived from the platform "
        "Visitor Privacy Policy master and can no longer be authored per tenant. "
        "Always returns 409 PRIVACY_NOTICE_MANAGED_BY_PLATFORM."
    ),
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        409: "Managed by platform — tenant authoring disabled",
    },
)
async def create_privacy_notice_endpoint(
    notice_data: PrivacyNoticeCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    # Tenant self-authoring is disabled — the notice is derived from the
    # platform Visitor Privacy Policy master.
    raise _managed_by_platform()


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


@router.patch("/{notice_id}", deprecated=True)
@document_response(
    message="Privacy notice authoring is managed by the platform",
    summary="Update privacy notice (DISABLED — managed by platform)",
    description=(
        "DISABLED. See the create endpoint — always returns 409 "
        "PRIVACY_NOTICE_MANAGED_BY_PLATFORM."
    ),
    response_codes={
        401: "Unauthorized token",
        403: "Insufficient permissions",
        409: "Managed by platform — tenant authoring disabled",
    },
)
async def update_privacy_notice_endpoint(
    notice_id: str,
    notice_data: PrivacyNoticeUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_admin_roles),
):
    # Tenant self-authoring is disabled — see create endpoint.
    raise _managed_by_platform()
