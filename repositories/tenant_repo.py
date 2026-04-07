from pymongo import ReturnDocument
from core.database import db
from fastapi import HTTPException, status
from typing import List, Optional
from schemas.tenant_schema import TenantCreate, TenantUpdate, TenantOut


async def create_tenant(tenant_data: TenantCreate) -> TenantOut:
    tenant_dict = tenant_data.model_dump()
    result = await db.tenant_companies.insert_one(tenant_dict)
    result = await db.tenant_companies.find_one({"_id": result.inserted_id})
    return TenantOut(**result)


async def get_tenant(filter_dict: dict) -> Optional[TenantOut]:
    try:
        result = await db.tenant_companies.find_one(filter_dict)
        if result is None:
            return None
        return TenantOut(**result)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching tenant: {str(e)}",
        )


async def get_tenants(filter_dict: dict = {}, start=0, stop=100) -> List[TenantOut]:
    try:
        if filter_dict is None:
            filter_dict = {}
        cursor = db.tenant_companies.find(filter_dict).skip(start).limit(stop - start)
        tenant_list = []
        async for doc in cursor:
            tenant_list.append(TenantOut(**doc))
        return tenant_list
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching tenants: {str(e)}",
        )


async def update_tenant(filter_dict: dict, tenant_data: TenantUpdate) -> TenantOut:
    update_dict = {k: v for k, v in tenant_data.model_dump().items() if v is not None}
    result = await db.tenant_companies.find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    return TenantOut(**result)


async def delete_tenant(filter_dict: dict):
    return await db.tenant_companies.delete_one(filter_dict)
