from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException
from typing import List

from repositories.tenant_repo import (
    create_tenant,
    get_tenant,
    get_tenants,
    update_tenant,
    delete_tenant,
)
from schemas.tenant_schema import TenantCreate, TenantUpdate, TenantOut, TenantBootstrapRequest, TenantWithSummaryOut, TenantPlanSummary


async def bootstrap_tenant(payload: TenantBootstrapRequest) -> dict:
    """
    Create a new tenant **and** its first super_admin in one atomic step.

    Only application admins call this.  If the system user creation fails the
    tenant is rolled back (deleted) so we never leave an orphaned tenant.

    Returns a dict with keys ``tenant`` (TenantOut) and ``super_admin``
    (SystemUserOut).
    """
    from repositories.system_user_repo import get_system_user
    from schemas.system_user_schema import SystemUserCreate
    from schemas.imports import SystemUserRole, AccountStatus
    from services.system_user_service import add_system_user

    # 1. Check for duplicate company name
    existing = await get_tenant(filter_dict={"company_name": payload.company_name})
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Tenant with this company name already exists",
        )

    # 1B. Cross-border transfer validation
    if hasattr(payload, 'country_of_hosting') and payload.country_of_hosting:
        if payload.country_of_hosting.lower() != "nigeria" and not getattr(payload, 'cross_border_approved', False):
            raise HTTPException(
                status_code=400,
                detail="Cross-border data transfer requires approval. Set cross_border_approved=true or use hosting within Nigeria.",
            )

    # 2. Create the tenant
    tenant_data = TenantCreate(
        company_name=payload.company_name,
        lawful_basis=payload.lawful_basis,
        notice_display_mode=payload.notice_display_mode,
        retention_days=payload.retention_days,
        default_retention_action=payload.default_retention_action,
        dpo_contact_email=payload.dpo_contact_email,
        privacy_policy_url=payload.privacy_policy_url,
        country_of_hosting=payload.country_of_hosting,
        cross_border_approved=payload.cross_border_approved,
    )
    tenant = await create_tenant(tenant_data)

    # 3. Guard: no super_admin should exist for this tenant yet
    existing_super = await get_system_user({
        "tenant_id": tenant.id,
        "role": SystemUserRole.SUPER_ADMIN.value,
    })
    if existing_super:
        raise HTTPException(
            status_code=409,
            detail="This tenant already has a super_admin",
        )

    # 4. Create the first super_admin system user
    try:
        super_admin_data = SystemUserCreate(
            tenant_id=tenant.id or "",
            full_name=payload.admin_full_name,
            email=payload.admin_email,
            role=SystemUserRole.SUPER_ADMIN,
            account_status=AccountStatus.ACTIVE,
            password_hash=payload.admin_password,
        )
        super_admin = await add_system_user(super_admin_data)
    except Exception:
        # Roll back: remove the orphaned tenant
        await delete_tenant({"_id": ObjectId(tenant.id)})
        raise

    return {
        "tenant": tenant,
        "super_admin": super_admin,
    }


async def add_tenant(tenant_data: TenantCreate) -> TenantOut:
    existing = await get_tenant(filter_dict={"company_name": tenant_data.company_name})
    if existing:
        raise HTTPException(status_code=409, detail="Tenant with this company name already exists")

    # Cross-border transfer validation
    if hasattr(tenant_data, 'country_of_hosting') and tenant_data.country_of_hosting:
        if tenant_data.country_of_hosting.lower() != "nigeria" and not getattr(tenant_data, 'cross_border_approved', False):
            raise HTTPException(
                status_code=400,
                detail="Cross-border data transfer requires approval. Set cross_border_approved=true or use hosting within Nigeria.",
            )

    return await create_tenant(tenant_data)


async def retrieve_tenant_by_id(tenant_id: str) -> TenantOut:
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")
    result = await get_tenant({"_id": ObjectId(tenant_id)})
    if not result:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return result


async def retrieve_tenants(start=0, stop=100) -> List[TenantOut]:
    return await get_tenants(start=start, stop=stop)


def _build_plan_summary(plan_data: dict | None) -> TenantPlanSummary | None:
    if not plan_data:
        return None
    return TenantPlanSummary(
        plan_id=plan_data.get("plan_id"),
        plan_name=plan_data.get("plan_name"),
        plan_display_name=plan_data.get("plan_display_name"),
        plan_tier=plan_data.get("tier"),
        subscription_id=plan_data.get("subscription_id"),
        subscription_status=plan_data.get("subscription_status"),
        billing_cycle=plan_data.get("billing_cycle"),
        effective_price=plan_data.get("effective_price"),
        currency="NGN",
        current_period_end=plan_data.get("current_period_end"),
        trial_ends_at=plan_data.get("trial_ends_at"),
        entity_caps=plan_data.get("tenant_caps"),
    )


async def _enrich_tenant(tenant: TenantOut) -> TenantWithSummaryOut:
    from services.plan_cache_service import resolve_tenant_plan
    plan_data = await resolve_tenant_plan(tenant.id or "")
    data = tenant.model_dump(by_alias=False)
    data["plan_summary"] = _build_plan_summary(plan_data)
    return TenantWithSummaryOut(**data)


async def retrieve_tenants_with_summary(start=0, stop=100) -> List[TenantWithSummaryOut]:
    import asyncio
    tenants = await get_tenants(start=start, stop=stop)
    return list(await asyncio.gather(*[_enrich_tenant(t) for t in tenants]))


async def retrieve_tenant_by_id_with_summary(tenant_id: str) -> TenantWithSummaryOut:
    tenant = await retrieve_tenant_by_id(tenant_id)
    return await _enrich_tenant(tenant)


async def update_tenant_by_id(tenant_id: str, tenant_data: TenantUpdate) -> TenantOut:
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")
    result = await update_tenant({"_id": ObjectId(tenant_id)}, tenant_data)
    if not result:
        raise HTTPException(status_code=404, detail="Tenant not found or update failed")
    return result


async def remove_tenant(tenant_id: str):
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")
    result = await delete_tenant({"_id": ObjectId(tenant_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Tenant not found")
