from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from schemas.imports import UserType
from security.auth import verify_any_token
from security.principal import AuthPrincipal, TENANT_USER_ROLES
from services.session_service import (
    retrieve_sessions,
    revoke_session,
    revoke_all_sessions_except_current,
)

router = APIRouter(prefix="/sessions", tags=["Session Management"])


def _user_type(principal: AuthPrincipal) -> UserType:
    return (
        UserType.SYSTEM_USER if principal.role in TENANT_USER_ROLES else UserType.ADMIN
    )


@router.get("")
@document_response(
    message="Sessions fetched successfully",
    success_example=[
        {
            "id": "64f1a2b3c4d5e6f7a8b9c0d1",
            "device": "Chrome (Windows)",
            "location": "Lagos, NG",
            "ipAddress": "102.89.xx.xx",
            "deviceType": "desktop",
            "createdAt": 1712361600,
            "lastActiveAt": 1712534400,
            "isCurrent": True,
        }
    ],
    description="List all active sessions for the authenticated user. Used in the Account tab's sessions table.",
    summary="List sessions",
    response_codes={401: "Unauthorized"},
)
async def list_sessions(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """List active sessions for the authenticated user."""
    return await retrieve_sessions(
        principal.user_id,
        _user_type(principal),
        current_token_id=principal.access_token_id,
    )


@router.delete("/{session_id}")
@document_response(
    message="Session revoked",
    success_example={"revoked": True},
    description="Revoke a single session. Cannot revoke the current session (return 400).",
    summary="Revoke session",
    response_codes={
        400: "Bad request - invalid ID or trying to revoke current session",
        401: "Unauthorized",
        404: "Session not found",
    },
)
async def revoke_single_session(
    session_id: str,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Revoke a specific session."""
    await revoke_session(session_id, principal.user_id, _user_type(principal))
    return {"revoked": True}


@router.post("/revoke-all")
@document_response(
    message="All other sessions revoked",
    success_example={"revokedCount": 3},
    description="Revoke all sessions except the current one. Used by 'Log out of all devices' button.",
    summary="Revoke all other sessions",
    response_codes={401: "Unauthorized"},
)
async def revoke_all_other_sessions(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    """Revoke all sessions except the current one."""
    count = await revoke_all_sessions_except_current(
        principal.user_id,
        _user_type(principal),
        principal.access_token_id,
    )
    return {"revoked_count": count}
