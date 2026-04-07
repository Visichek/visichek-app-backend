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
from schemas.tenant_schema import TenantCreate, TenantUpdate, TenantOut


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
