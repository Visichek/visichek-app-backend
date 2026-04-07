from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from core.response_envelope import document_response
from schemas.visitor_profile_schema import VisitorProfileUpdate
from security.auth import verify_system_user_token, verify_any_system_user_token
from security.principal import AuthPrincipal
from services.visitor_profile_service import (
    retrieve_visitor_profile_by_id,
    retrieve_visitor_profiles,
    search_profiles,
    update_profile_by_id,
)

router = APIRouter(prefix="/visitor-profiles", tags=["Visitor Profiles"])


@router.get("/search")
@document_response(message="Visitor profiles search results", success_example=[])
async def search_visitor_profiles_endpoint(
    q: str,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 20,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await search_profiles(tenant_id=tenant_id, query=q, start=start, stop=stop)


@router.get("/")
@document_response(message="Visitor profiles fetched successfully", success_example=[])
async def list_visitor_profiles(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(verify_system_user_token("dept_admin", "super_admin", "auditor")),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_visitor_profiles(tenant_id=tenant_id, start=start, stop=stop)


@router.get("/{profile_id}")
@document_response(message="Visitor profile fetched successfully")
async def get_visitor_profile_endpoint(
    profile_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await retrieve_visitor_profile_by_id(profile_id=profile_id, tenant_id=tenant_id)


@router.patch("/{profile_id}")
@document_response(message="Visitor profile updated successfully")
async def update_visitor_profile_endpoint(
    profile_id: str,
    profile_data: VisitorProfileUpdate,
    principal: AuthPrincipal = Depends(verify_system_user_token("receptionist", "dept_admin", "super_admin")),
):
    tenant_id = principal.tenant_id or ""
    return await update_profile_by_id(
        profile_id=profile_id, tenant_id=tenant_id, profile_data=profile_data
    )
