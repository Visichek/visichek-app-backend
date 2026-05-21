from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from schemas.imports import UserType
from schemas.tutorial_schema import TutorialOut, TutorialProgressRequest
from security.auth import verify_any_token
from security.principal import AuthPrincipal, TENANT_USER_ROLES
from services.tutorial_service import (
    record_tutorial_progress,
    retrieve_tutorial_progress,
)

router = APIRouter(prefix="/tutorials", tags=["Tutorials"])


def _user_type(principal: AuthPrincipal) -> UserType:
    return (
        UserType.SYSTEM_USER if principal.role in TENANT_USER_ROLES else UserType.ADMIN
    )


@router.get("")
@document_response(
    message="Tutorial progress fetched successfully",
    summary="List tutorial progress",
    description=(
        "Return every tutorial-progress record for the authenticated user "
        "(one per tutorial type + version). The frontend uses these to decide "
        "which 'Start/Resume tutorial' entry-points to surface."
    ),
    response_codes={401: "Unauthorized - invalid or missing token"},
)
async def list_tutorial_progress(
    principal: AuthPrincipal = Depends(verify_any_token),
) -> List[TutorialOut]:
    """List every tutorial-progress record for the authenticated user.

    One item per (tutorialType, version). Read on shell mount to drive which
    "Start/Resume tutorial" entry-points to show and their current status.
    Only the caller's own records are returned (and only shell-valid records
    can ever exist), so there is nothing cross-shell to leak here.
    """
    return await retrieve_tutorial_progress(principal.user_id, _user_type(principal))


@router.put("")
@document_response(
    message="Tutorial progress saved",
    summary="Record tutorial progress",
    description=(
        "Upsert the authenticated user's progress for one tutorial, keyed by "
        "(tutorial_type, version). Use it to mark a tutorial in_progress, "
        "completed, or dismissed."
    ),
    response_codes={
        401: "Unauthorized - invalid or missing token",
        422: "Validation error - unknown tutorial_type or status",
    },
)
async def save_tutorial_progress(
    data: TutorialProgressRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
) -> TutorialOut:
    """Upsert the caller's progress for one tutorial and return the record.

    Identity is taken from the token (the body carries only tutorial_type /
    tutorial_status / version). Upserts on (user_id, tutorial_type, version):
    a repeat call updates the status; a new version starts a fresh record.

    Shell-gated in the service layer: a tenant user recording a platform
    tutorial (or vice versa) is refused with 403 AUTH_ROLE_MISMATCH; an
    unknown tutorial_type / status is 422.
    """
    return await record_tutorial_progress(
        user_id=principal.user_id,
        user_role=principal.role,
        user_type=_user_type(principal),
        tutorial_type=data.tutorial_type,
        tutorial_status=data.tutorial_status,
        version=data.version,
        tenant_id=principal.tenant_id,
    )
