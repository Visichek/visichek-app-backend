from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from schemas.user_settings_schema import UserSettingsUpdate
from security.auth import verify_any_token
from security.principal import AuthPrincipal, TENANT_USER_ROLES
from services.user_settings_service import (
    retrieve_or_create_settings,
    update_settings,
)

router = APIRouter(prefix="/user-settings", tags=["User Settings"])


def _user_type(principal: AuthPrincipal) -> str:
    return "system_user" if principal.role in TENANT_USER_ROLES else "admin"


@router.get("")
@document_response(
    message="User settings fetched successfully",
    success_example={
        "language": "en",
        "timezone": "Africa/Lagos",
        "dateFormat": "DD/MM/YYYY",
        "timeFormat": "24h",
        "emailNotifications": True,
        "pushNotifications": False,
        "notifyOnSystemAlert": True,
        "notifyOnVisitorCheckIn": True,
        "notifyOnAppointmentReminder": True,
        "notifyOnIncidentCreated": True,
        "notifyOnDsrReceived": True,
        "digestFrequency": "realtime",
    },
    description=(
        "Return the authenticated user's personal preferences with server defaults "
        "for any unset fields. Works for both platform admins and tenant system users. "
        "Auto-creates defaults on first access — never returns 404."
    ),
    summary="Get user settings",
    response_codes={401: "Unauthorized - invalid or missing token"},
)
async def get_user_settings(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Get personal settings for the authenticated user."""
    return await retrieve_or_create_settings(principal.user_id, _user_type(principal))


@router.patch("")
@document_response(
    message="User settings updated successfully",
    description="Partial update — only include changed fields. Returns full settings after applying changes.",
    summary="Update user settings",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        422: "Validation error",
    },
)
async def update_user_settings(
    data: UserSettingsUpdate,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Update personal settings for the authenticated user."""
    return await update_settings(principal.user_id, _user_type(principal), data)
