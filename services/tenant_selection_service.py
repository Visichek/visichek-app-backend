"""Tenant selection challenge service.

When a user's email matches more than one ``system_users`` record (i.e. they
have accounts in multiple tenants), the login endpoint cannot decide which
tenant to log them into. Instead it:

1. Verifies the password against every matching record.
2. Filters to active accounts.
3. Persists a *tenant selection challenge* — a short-lived, single-use record
   listing the candidate user_ids — and returns the challenge id (the
   ``selection_token``) plus a list of tenants for the UI to display.
4. The frontend calls ``POST /v1/system-users/select-tenant`` with
   ``{ selection_token, tenant_id }``. We resolve the user_id, mark the
   challenge used, and continue with the standard 2FA / token-issuance flow.

Mirrors the OTP challenge pattern in ``services.otp_service``.
"""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException

from repositories.tenant_selection_repo import (
    create_tenant_selection_challenge as _create_challenge,
    get_tenant_selection_challenge as _get_challenge,
    mark_tenant_selection_used as _mark_used,
)

logger = logging.getLogger(__name__)

TENANT_SELECTION_TTL_SECONDS = 300  # 5 minutes


async def create_tenant_selection_challenge(
    email: str,
    candidate_user_ids: list[str],
) -> str:
    """Persist a pending tenant-selection challenge. Returns the challenge id."""
    now = int(time.time())
    doc = {
        "email": email,
        "candidate_user_ids": candidate_user_ids,
        "created_at": now,
        "expires_at": now + TENANT_SELECTION_TTL_SECONDS,
        "used": False,
    }
    result = await _create_challenge(doc)
    return str(result["_id"])


async def consume_tenant_selection_challenge(
    selection_token: str,
    tenant_id: str,
) -> str:
    """Validate a tenant-selection challenge against a chosen tenant.

    Returns the resolved ``user_id`` (the candidate that belongs to the chosen
    tenant). Raises HTTPException on every failure mode. Marks the challenge as
    used on success, so it cannot be replayed.
    """
    challenge = await _get_challenge(selection_token)
    if not challenge:
        raise HTTPException(
            status_code=401, detail="Invalid or expired tenant selection token"
        )

    if challenge.get("used"):
        raise HTTPException(
            status_code=401, detail="Tenant selection token already used"
        )

    if challenge["expires_at"] < int(time.time()):
        raise HTTPException(
            status_code=401, detail="Tenant selection token has expired"
        )

    candidate_ids: list[str] = list(challenge.get("candidate_user_ids") or [])
    if not candidate_ids:
        raise HTTPException(status_code=401, detail="No candidate users on selection")

    # Look up which candidate belongs to the requested tenant.
    from repositories.system_user_repo import get_system_user
    from bson import ObjectId

    chosen_user_id: str | None = None
    for uid in candidate_ids:
        if not ObjectId.is_valid(uid):
            continue
        user = await get_system_user({"_id": ObjectId(uid)})
        if user and user.tenant_id == tenant_id:
            chosen_user_id = uid
            break

    if not chosen_user_id:
        raise HTTPException(
            status_code=403,
            detail="Selected tenant is not available for this login attempt",
        )

    await _mark_used(selection_token)
    return chosen_user_id
