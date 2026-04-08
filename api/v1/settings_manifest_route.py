from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.settings_manifest_service import build_settings_manifest

router = APIRouter(prefix="/settings", tags=["Settings Manifest"])


@router.get("")
@document_response(
    message="Settings manifest fetched successfully",
    description=(
        "Returns a complete manifest of everything the authenticated user can view "
        "and change on their account. Includes profile data, section definitions with "
        "endpoint URLs, 2FA enforcement state, account deletion eligibility, and "
        "role-conditional sections (tenant settings for super_admin, platform settings for admin). "
        "The frontend should use this as the single source of truth for rendering the settings UI."
    ),
    summary="Get settings manifest",
    response_codes={401: "Unauthorized - invalid or missing token"},
)
async def get_settings_manifest(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Get the full settings manifest for the current user."""
    return await build_settings_manifest(principal)
