from __future__ import annotations

from fastapi import Depends, status
from fastapi import APIRouter

from core.response_envelope import document_response
from schemas.platform_settings_schema import MaintenanceModeUpdateRequest
from security.account_status_check import check_admin_account_status_and_permissions
from services.platform_settings_service import (
    retrieve_or_create_platform_settings,
    request_maintenance_mode_otp,
    update_maintenance_mode,
)

router = APIRouter(prefix="/platform-settings", tags=["Platform Settings (Unified)"])


@router.get("")
@document_response(
    message="Platform settings fetched successfully",
    success_example={
        "maintenanceMode": False,
        "maintenanceMessage": None,
    },
    description=(
        "Return the runtime-editable platform configuration. Only maintenance "
        "mode is editable at runtime; every other platform setting is defined "
        "in code (config/platform_config.py) and changed by a deploy. "
        "Application admin only."
    ),
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


@router.post("/maintenance/request-otp", status_code=status.HTTP_202_ACCEPTED)
@document_response(
    message="Verification code sent",
    status_code=status.HTTP_202_ACCEPTED,
    success_example={
        "otpRequired": True,
        "otpChallengeId": "65f0a1b2c3d4e5f6a7b8c9d0",
        "message": "Verification code sent. Submit it with the new maintenance state.",
    },
    description=(
        "Step 1 of toggling maintenance mode. Mints an OTP challenge and "
        "dispatches the code via the admin's MFA channel (email for invited "
        "admins, static dev code for the env primary admin / non-prod). Submit "
        "the code to PATCH /v1/platform-settings to apply the change. "
        "Application admin only."
    ),
    summary="Request OTP to change maintenance mode",
    response_codes={401: "Unauthorized", 403: "Forbidden - must be application admin"},
)
async def request_maintenance_otp(
    admin=Depends(check_admin_account_status_and_permissions),
):
    """Request an OTP challenge to change maintenance mode."""
    challenge_id = await request_maintenance_mode_otp(actor_id=admin.id)
    return {
        "otp_required": True,
        "otp_challenge_id": challenge_id,
        "message": "Verification code sent. Submit it with the new maintenance state.",
    }


@router.patch("")
@document_response(
    message="Maintenance mode updated successfully",
    description=(
        "Step 2 of toggling maintenance mode. Verifies the OTP from "
        "/maintenance/request-otp, then sets maintenance mode (and optional "
        "message). This is the ONLY runtime-editable platform setting. "
        "Application admin only."
    ),
    summary="Update maintenance mode (OTP required)",
    response_codes={
        401: "Unauthorized - invalid/expired OTP",
        403: "Forbidden - must be application admin",
        422: "Validation error",
        429: "Too many OTP attempts",
    },
)
async def update_platform_settings(
    data: MaintenanceModeUpdateRequest,
    admin=Depends(check_admin_account_status_and_permissions),
):
    """Update maintenance mode (application admin only, OTP required)."""
    return await update_maintenance_mode(data, actor_id=admin.id)
