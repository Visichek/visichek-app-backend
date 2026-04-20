from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.user_settings_schema import UserSettingsUpdate
from security.auth import verify_any_token
from security.principal import AuthPrincipal, TENANT_USER_ROLES
from services.user_settings_service import retrieve_or_create_settings

router = APIRouter(prefix="/user-settings", tags=["User Settings"])


def _user_type(principal: AuthPrincipal) -> str:
    return "system_user" if principal.role in TENANT_USER_ROLES else "admin"


@router.get("")
@document_response(
    message="User settings fetched successfully",
    description=(
        "Return the authenticated user's personal preferences with server defaults. "
        "Small per-user record — served live from the DB (HttpCache handles repeat reads)."
    ),
    summary="Get user settings",
    response_codes={401: "Unauthorized - invalid or missing token"},
)
async def get_user_settings(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    return await retrieve_or_create_settings(principal.user_id, _user_type(principal))


@router.patch("")
@document_response(
    message="User settings update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description="Enqueue a partial user-settings update.",
    summary="Update user settings (async)",
    success_example={
        "id": "64f1a2b3c4d5e6f7a8b9c0d1",
        "job_id": "b3d5f7a9-2345-4def-8abc-1234567890bc",
        "status": "queued",
    },
    response_codes={
        401: "Unauthorized - invalid or missing token",
        422: "Validation error",
    },
)
async def update_user_settings(
    data: UserSettingsUpdate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    payload = data.model_dump(exclude_none=True)
    payload["user_id"] = principal.user_id
    payload["user_type"] = _user_type(principal)
    return await enqueue_write(
        writer_key="user_settings.update",
        payload=payload,
        resource_type="user_settings",
        resource_id=principal.user_id,
        tenant_id=principal.tenant_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
