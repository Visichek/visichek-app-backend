from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.system_user_schema import (
    SystemUserCreate,
    SystemUserUpdate,
    SystemUserOut,
    SystemUserLogin,
    SystemUserRefresh,
)
from security.auth import (
    verify_any_system_user_token,
    verify_super_admin_token,
    verify_system_user_refresh_token,
)
from security.principal import AuthPrincipal
from services.system_user_service import (
    add_system_user,
    authenticate_system_user,
    refresh_system_user_tokens,
    retrieve_system_user_by_id,
    retrieve_system_users,
    update_system_user_by_id,
    remove_system_user,
)

router = APIRouter(prefix="/system-users", tags=["System Users"])


@router.post("/login")
@document_response(message="Login successful")
async def login_system_user(login_data: SystemUserLogin):
    return await authenticate_system_user(login_data=login_data)


@router.post("/signup")
@document_response(message="System user created successfully", status_code=status.HTTP_201_CREATED)
async def signup_system_user(
    user_data: SystemUserCreate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    # Ensure tenant_id matches the super admin's tenant
    if principal.tenant_id:
        user_data.tenant_id = principal.tenant_id
    return await add_system_user(user_data=user_data)


@router.post("/refresh")
@document_response(message="Tokens refreshed successfully")
async def refresh_tokens(
    refresh_data: SystemUserRefresh,
    principal: AuthPrincipal = Depends(verify_system_user_refresh_token),
):
    return await refresh_system_user_tokens(
        refresh_data=refresh_data,
        expired_access_token=principal.access_token_id,
    )


@router.get("/me")
@document_response(message="Profile fetched successfully")
async def get_my_profile(
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    return await retrieve_system_user_by_id(user_id=principal.user_id)


@router.get("/")
@document_response(message="System users fetched successfully", success_example=[])
async def list_system_users(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_system_users(tenant_id=tenant_id, start=start, stop=stop)


@router.patch("/{user_id}")
@document_response(message="System user updated successfully")
async def update_system_user_endpoint(
    user_id: str,
    user_data: SystemUserUpdate,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await update_system_user_by_id(
        user_id=user_id, tenant_id=tenant_id, user_data=user_data
    )


@router.delete("/{user_id}")
@document_response(message="System user deleted successfully")
async def delete_system_user_endpoint(
    user_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
):
    tenant_id = principal.tenant_id or ""
    return await remove_system_user(user_id=user_id, tenant_id=tenant_id)
