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
from schemas.tenant_schema import TenantCreate, TenantUpdate, TenantOut, TenantBootstrapRequest


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
            tenant_id=tenant.id,
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
