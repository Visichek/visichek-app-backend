from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from core.response_envelope import document_response
from schemas.usage_schema import TenantUsageSummary
from services.usage_service import get_tenant_usage_summary
from services.plan_cache_service import resolve_tenant_plan
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_any_token
from security.principal import AuthPrincipal

router = APIRouter(prefix="/usage", tags=["Usage & Quotas"])


@router.get("/tenant/{tenant_id}/summary")
@document_response(
    message="Usage summary retrieved successfully",
    description="Get a tenant's current usage vs plan limits (legacy admin only)",
    summary="Get tenant usage summary",
)
async def get_usage_summary_endpoint(
    tenant_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
) -> TenantUsageSummary | dict:
    """Get comprehensive usage summary for a tenant showing current usage vs plan limits."""
    plan_data = await resolve_tenant_plan(tenant_id)
    if not plan_data:
        return {"error": "No active subscription for this tenant"}

    return await get_tenant_usage_summary(
        tenant_id=tenant_id,
        subscription_id=plan_data.get("subscription_id", ""),
        plan_data=plan_data,
    )


@router.get("/my-usage")
@document_response(
    message="Usage summary retrieved successfully",
    description="Get usage summary for the authenticated user's tenant",
    summary="Get my tenant usage",
)
async def get_my_usage_endpoint(
    principal: AuthPrincipal = Depends(verify_any_token),
) -> TenantUsageSummary | dict:
    """Get usage summary for the current user's tenant.
    Available to any authenticated system user.
    """
    tenant_id = principal.tenant_id
    if not tenant_id:
        return {"error": "No tenant associated with your account"}

    plan_data = await resolve_tenant_plan(tenant_id)
    if not plan_data:
        return {"error": "No active subscription for your organization"}

    return await get_tenant_usage_summary(
        tenant_id=tenant_id,
        subscription_id=plan_data.get("subscription_id", ""),
        plan_data=plan_data,
    )
