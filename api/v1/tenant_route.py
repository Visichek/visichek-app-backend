from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.tenant_schema import TenantCreate, TenantUpdate, TenantOut
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.tenant_service import (
    add_tenant,
    retrieve_tenant_by_id,
    retrieve_tenants,
    update_tenant_by_id,
)

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.post("/")
@document_response(message="Tenant created successfully", status_code=status.HTTP_201_CREATED)
async def create_tenant_endpoint(
    tenant_data: TenantCreate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    return await add_tenant(tenant_data=tenant_data)


@router.get("/")
@document_response(message="Tenants fetched successfully", success_example=[])
async def list_tenants(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    return await retrieve_tenants(start=start, stop=stop)


@router.get("/{tenant_id}")
@document_response(message="Tenant fetched successfully")
async def get_tenant_endpoint(
    tenant_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    return await retrieve_tenant_by_id(tenant_id=tenant_id)


@router.patch("/{tenant_id}")
@document_response(message="Tenant updated successfully")
async def update_tenant_endpoint(
    tenant_id: str,
    tenant_data: TenantUpdate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    return await update_tenant_by_id(tenant_id=tenant_id, tenant_data=tenant_data)
