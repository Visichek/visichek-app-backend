from typing import Any, List

from fastapi import APIRouter, Depends, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from repositories.retention_policy_repo import get_retention_policies
from schemas.retention_policy_schema import RetentionPolicyCreate, RetentionPolicyUpdate
from security.auth import verify_system_user_token
from security.principal import AuthPrincipal

router = APIRouter(prefix="/retention-policies", tags=["Retention Policies"])
_dpo_roles = verify_system_user_token("super_admin", "dpo")


@router.post("")
@document_response(
    message="Retention policy creation queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create retention policy (async)",
    description="Enqueue a retention policy create.",
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
async def create_policy(
    policy_data: RetentionPolicyCreate,
    request: Request,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    payload = policy_data.model_dump(exclude_none=True)
    # tenant_id is token-derived, never client-supplied.
    payload["tenant_id"] = principal.tenant_id or ""
    return await enqueue_write(
        writer_key="retention_policy.create",
        payload=payload,
        resource_type="retention_policy",
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("")
@document_response(
    message="Retention policies fetched successfully",
    summary="List retention policies",
    description="Served from the per-tenant precompute cache.",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439011",
            "tenant_id": "tenant_001",
            "scope": "visitor_profiles",
            "retention_days": 365,
            "action": "anonymise",
            "date_created": 1712448000,
        }
    ],
    include_meta=True,
    response_codes={401: "Unauthorized token", 403: "Insufficient permissions"},
)
async def list_policies(principal: AuthPrincipal = Depends(_dpo_roles)) -> Any:
    tenant_id = principal.tenant_id or ""
    if not tenant_id:
        return await get_retention_policies({"tenant_id": tenant_id})
    return await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource="retention_policies.list",
        ttl=60,
        loader=lambda: _load_policies_for_tenant(tenant_id),
    )


async def _load_policies_for_tenant(tenant_id: str) -> List[Any]:
    policies = await get_retention_policies({"tenant_id": tenant_id})
    return [
        p.model_dump(mode="json", by_alias=True) if hasattr(p, "model_dump") else p
        for p in policies
    ]


@router.patch("/{policy_id}")
@document_response(
    message="Retention policy update queued",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update retention policy (async)",
    description="Enqueue a partial retention policy update.",
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
async def update_policy(
    policy_id: str,
    policy_data: RetentionPolicyUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(_dpo_roles),
):
    tenant_id = principal.tenant_id or ""
    payload = policy_data.model_dump(exclude_none=True)
    payload["tenant_id"] = tenant_id
    return await enqueue_write(
        writer_key="retention_policy.update",
        payload=payload,
        resource_type="retention_policy",
        resource_id=policy_id,
        tenant_id=tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
