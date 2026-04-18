from typing import Annotated

from fastapi import APIRouter, Depends, Query

from core.response_envelope import document_response
from schemas.visitor_profile_schema import (
    VisitorProfileUpdate,
    VisitorProfileWithSummaryOut,
)
from security.auth import verify_system_user_token, verify_any_system_user_token
from security.principal import AuthPrincipal
from services.visitor_profile_service import (
    retrieve_visitor_profile_by_id_with_summary,
    retrieve_visitor_profiles_with_summary,
    search_profiles,
    update_profile_by_id,
)

router = APIRouter(prefix="/visitor-profiles", tags=["Visitor Profiles"])


@router.get("/search")
@document_response(
    message="Visitor profiles search results",
    description="Search visitor profiles by name, phone, or email with pagination.",
    summary="Search visitor profiles",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439012",
            "tenant_id": "t12345",
            "phone": "+1-555-0123",
            "email_address": "john.doe@acmecorp.com",
            "full_name": "John Doe",
            "company": "Acme Corp",
            "photo_object_key": "photos/profile_507f1f77bcf86cd799439012.jpg",
            "id_type": "driver_license",
            "id_number": "DL123456789",
            "id_image_object_key": "id_images/profile_507f1f77bcf86cd799439012.jpg",
            "profiling_preference": "allowed",
            "last_verification_date": 1712520000,
            "date_created": 1710000000,
            "last_updated": 1712520000,
            "deleted_at": None,
            "total_visits": 5,
            "last_visit_date": 1712532000,
        }
    ],
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def search_visitor_profiles_endpoint(
    q: str,
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 20,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
):
    tenant_id = principal.tenant_id or ""
    return await search_profiles(tenant_id=tenant_id, query=q, start=start, stop=stop)


@router.get("")
@document_response(
    message="Visitor profiles fetched successfully",
    description="Retrieve paginated list of all visitor profiles for the tenant.",
    summary="List visitor profiles",
    success_example=[
        {
            "id": "507f1f77bcf86cd799439012",
            "tenant_id": "t12345",
            "phone": "+1-555-0123",
            "email_address": "john.doe@acmecorp.com",
            "full_name": "John Doe",
            "company": "Acme Corp",
            "photo_object_key": "photos/profile_507f1f77bcf86cd799439012.jpg",
            "id_type": "driver_license",
            "id_number": "DL123456789",
            "id_image_object_key": "id_images/profile_507f1f77bcf86cd799439012.jpg",
            "profiling_preference": "allowed",
            "last_verification_date": 1712520000,
            "date_created": 1710000000,
            "last_updated": 1712520000,
            "deleted_at": None,
            "total_visits": 5,
            "last_visit_date": 1712532000,
        }
    ],
    include_meta=True,
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
    },
)
async def list_visitor_profiles(
    start: Annotated[int, Query(ge=0)] = 0,
    stop: Annotated[int, Query(gt=0)] = 100,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("dept_admin", "super_admin", "auditor")
    ),
) -> list[VisitorProfileWithSummaryOut]:
    tenant_id = principal.tenant_id or ""
    return await retrieve_visitor_profiles_with_summary(
        tenant_id=tenant_id, start=start, stop=stop
    )


@router.get("/{profile_id}")
@document_response(
    message="Visitor profile fetched successfully",
    description="Retrieve detailed information about a specific visitor profile.",
    summary="Fetch visitor profile by ID",
    success_example={
        "id": "507f1f77bcf86cd799439012",
        "tenant_id": "t12345",
        "phone": "+1-555-0123",
        "email_address": "john.doe@acmecorp.com",
        "full_name": "John Doe",
        "company": "Acme Corp",
        "photo_object_key": "photos/profile_507f1f77bcf86cd799439012.jpg",
        "id_type": "driver_license",
        "id_number": "DL123456789",
        "id_image_object_key": "id_images/profile_507f1f77bcf86cd799439012.jpg",
        "profiling_preference": "allowed",
        "last_verification_date": 1712520000,
        "date_created": 1710000000,
        "last_updated": 1712520000,
        "deleted_at": None,
        "total_visits": 5,
        "last_visit_date": 1712532000,
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Visitor profile not found",
    },
    error_examples={
        404: {
            "success": False,
            "message": "Visitor profile not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def get_visitor_profile_endpoint(
    profile_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> VisitorProfileWithSummaryOut:
    tenant_id = principal.tenant_id or ""
    return await retrieve_visitor_profile_by_id_with_summary(
        profile_id=profile_id, tenant_id=tenant_id
    )


@router.patch("/{profile_id}")
@document_response(
    message="Visitor profile updated successfully",
    description="Update visitor profile fields such as contact info or personal details.",
    summary="Update visitor profile",
    success_example={
        "id": "507f1f77bcf86cd799439012",
        "tenant_id": "t12345",
        "phone": "+1-555-0124",
        "email_address": "john.doe.updated@acmecorp.com",
        "full_name": "John Doe",
        "company": "Acme Corp",
        "photo_object_key": "photos/profile_507f1f77bcf86cd799439012.jpg",
        "id_type": "driver_license",
        "id_number": "DL123456789",
        "id_image_object_key": "id_images/profile_507f1f77bcf86cd799439012.jpg",
        "profiling_preference": "allowed",
        "last_verification_date": 1712520000,
        "date_created": 1710000000,
        "last_updated": 1712535600,
        "deleted_at": None,
        "total_visits": 5,
        "last_visit_date": 1712532000,
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - insufficient permissions",
        404: "Visitor profile not found",
    },
    error_examples={
        404: {
            "success": False,
            "message": "Visitor profile not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
)
async def update_visitor_profile_endpoint(
    profile_id: str,
    profile_data: VisitorProfileUpdate,
    principal: AuthPrincipal = Depends(
        verify_system_user_token("receptionist", "dept_admin", "super_admin")
    ),
):
    tenant_id = principal.tenant_id or ""
    return await update_profile_by_id(
        profile_id=profile_id, tenant_id=tenant_id, profile_data=profile_data
    )
