"""Tenant-facing agreement acceptance API — ``/v1/agreements/*``.

Tenants view and accept/decline the platform's master legal agreements (Data
Processing Agreement + Visitor Privacy Policy), each resolved with the tenant's
own details substituted into the ``[placeholder]`` tokens. Until every required
agreement is accepted at its current published version, all tenant-role
requests are blocked by ``security.auth._enforce_agreement_acceptance`` (these
routes are on its allowlist so a blocked tenant can still reach them).

Acceptance is SYNCHRONOUS (not queued): it is auth-adjacent, one-shot, and must
clear the gate within the same response — consistent with the onboarding DPA
accept and the other auth/account paths.

Reads are open to any tenant role (so every user can see what is pending);
accept/decline are restricted to the super_admin who acts for the tenant.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from core.response_envelope import document_response
from schemas.tenant_agreement_schema import TenantAgreementOut
from security.auth import verify_any_system_user_token, verify_super_admin_token
from security.principal import AuthPrincipal
from services.tenant_agreement_service import (
    list_for_tenant,
    mark_accepted,
    mark_declined,
    pending_agreements,
    retrieve_or_build,
)
from services.tenant_agreements.config import ALL_AGREEMENT_KEYS, get_agreement

router = APIRouter(prefix="/agreements", tags=["Tenant Agreements"])


def _require_known_key(key: str) -> None:
    if get_agreement(key) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown agreement '{key}'. Known: {ALL_AGREEMENT_KEYS}",
        )


# ---------------------------------------------------------------------------
# Reads (any tenant role) — static paths BEFORE dynamic /{key}
# ---------------------------------------------------------------------------


@router.get("")
@document_response(
    message="Agreements retrieved",
    description=(
        "List every platform agreement for the calling tenant, each resolved "
        "with the tenant's details substituted into the placeholders, plus the "
        "acceptance state. Use it to drive the acceptance screen."
    ),
    summary="List tenant agreements",
)
async def list_agreements_endpoint(
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> Any:
    return await list_for_tenant(principal.tenant_id or "")


@router.get("/pending")
@document_response(
    message="Pending agreements retrieved",
    description=(
        "Return the keys of agreements the tenant must still accept at their "
        "current published version. ``mustAccept`` is true when any are pending."
    ),
    summary="List pending agreements",
)
async def pending_agreements_endpoint(
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> Any:
    pending = await pending_agreements(principal.tenant_id or "")
    return {"pending": pending, "mustAccept": bool(pending)}


@router.get("/{key}")
@document_response(
    message="Agreement retrieved",
    description=(
        "Full resolved agreement for display — BlockNote ``body`` with the "
        "tenant's placeholders substituted, plus acceptance metadata."
    ),
    summary="Get one agreement",
)
async def get_agreement_endpoint(
    key: str = Path(..., description="Agreement key, e.g. 'dpa'"),
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> TenantAgreementOut:
    _require_known_key(key)
    agreement = await retrieve_or_build(principal.tenant_id or "", key)
    if agreement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agreement '{key}' is not available yet.",
        )
    return agreement


# ---------------------------------------------------------------------------
# Accept / decline (super_admin only) — synchronous
# ---------------------------------------------------------------------------


@router.post("/{key}/accept")
@document_response(
    message="Agreement accepted",
    description=(
        "Accept the current published version of an agreement for the tenant. "
        "Freezes the resolved copy as the immutable record of what was agreed "
        "and clears the acceptance gate. super_admin only."
    ),
    summary="Accept an agreement",
)
async def accept_agreement_endpoint(
    request: Request,
    key: str = Path(..., description="Agreement key, e.g. 'dpa'"),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> TenantAgreementOut:
    _require_known_key(key)
    accepted = await mark_accepted(
        principal.tenant_id or "",
        key,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    if accepted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agreement '{key}' is not available yet.",
        )
    return accepted


@router.post("/{key}/decline")
@document_response(
    message="Agreement declined",
    description=(
        "Record that the tenant declined the current version. The tenant stays "
        "blocked from privileged actions until an agreement is accepted — "
        "declining is a logged signal, not an account deletion. super_admin only."
    ),
    summary="Decline an agreement",
)
async def decline_agreement_endpoint(
    request: Request,
    key: str = Path(..., description="Agreement key, e.g. 'dpa'"),
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    _require_known_key(key)
    await mark_declined(
        principal.tenant_id or "",
        key,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=getattr(request.state, "request_id", None),
    )
    return {"declined": True, "stillBlocked": True}
