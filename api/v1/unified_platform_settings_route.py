from __future__ import annotations

from fastapi import Depends
from fastapi import APIRouter

from core.response_envelope import document_response
from schemas.platform_settings_schema import PlatformSettingsUpdate
from security.account_status_check import check_admin_account_status_and_permissions
from services.platform_settings_service import (
    retrieve_or_create_platform_settings,
    update_platform_settings_data,
)

router = APIRouter(prefix="/platform-settings", tags=["Platform Settings (Unified)"])


@router.get("")
@document_response(
    message="Platform settings fetched successfully",
    success_example={
        "platformName": "VisiChek",
        "supportEmail": "support@visichek.com",
        "maintenanceMode": False,
        "signupsEnabled": True,
        "publicApiEnabled": True,
        "betaFeaturesEnabled": False,
    },
    description="Return global platform configuration. Application admin only.",
    summary="Get platform settings",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - must be application admin",
    },
)
async def get_platform_settings(
    admin=Depends(check_admin_account_status_and_permissions),
):
    """Get platform settings (application admin only)."""
    return await retrieve_or_create_platform_settings()


@router.patch("")
@document_response(
    message="Platform settings updated successfully",
    description="Partial update of global platform configuration. Application admin only.",
    summary="Update platform settings",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - must be application admin",
        422: "Validation error",
    },
)
async def update_platform_settings(
    data: PlatformSettingsUpdate,
    admin=Depends(check_admin_account_status_and_permissions),
):
    """Update platform settings (application admin only)."""
    return await update_platform_settings_data(data, actor_id=admin.id)
