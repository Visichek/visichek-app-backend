"""Bulk writers for check-in approval queue actions.

Three bulk operations exposed to the receptionist UI:

* ``checkin.bulk_approve``              — approve many pending check-ins at once
* ``checkin.bulk_reject``               — reject many pending check-ins with a single shared reason
* ``checkin.bulk_force_approve_pending``— super_admin-only batch un-stick for
                                          KYC-parked check-ins
                                          (``PENDING_VERIFICATION`` → ``PENDING_APPROVAL``)

Each handler wraps the existing single-item service function and runs it
per id via ``run_bulk_handlers`` so the per-id success / failure lands
on ``queue_job_log.result`` in the standard ``{succeeded, failed}``
shape. Clients poll ``GET /v1/jobs/{job_id}`` for the per-id outcome.

The writer surfaces dashboard / visitor-flow precompute invalidations
via ``invalidates=_CHECKIN_INVALIDATES`` — kept symmetric with the
visit-session bulk writer so a checkin write clears the same
read-models as a visitor write (the two surfaces share state).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.bulk import run_bulk_handlers
from core.queue.write_pipeline import write_handler
from schemas.checkin_schema import CheckinConfirmRequest
from services.checkin_service import (
    confirm_checkin,
    force_approve_pending_verification,
)

logger = logging.getLogger(__name__)


_CHECKIN_INVALIDATES = [
    "dashboard.stats",
    "dashboard.visitors_active",
    "dashboard.visitors_page1",
    "visitor_profiles.list",
]


@dataclass(frozen=True)
class _ActorStub:
    """Minimal principal stand-in for ``confirm_checkin``.

    The single-item service only reads ``user_id`` off the principal —
    we don't need the full ``AuthPrincipal`` shape (role, tenant_id,
    token id) for the approval payload. A stub avoids a service-layer
    refactor and keeps the bulk path identical to the single path.
    """

    user_id: str


@write_handler("checkin.bulk_approve", invalidates=_CHECKIN_INVALIDATES)
async def _checkin_bulk_approve(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    actor_id = str(extras.get("actor_id") or "bulk")
    notes = extras.get("notes")
    principal = _ActorStub(user_id=actor_id)

    async def _handle(checkin_id: str) -> dict[str, Any]:
        req = CheckinConfirmRequest(action="approve", notes=notes)
        result = await confirm_checkin(checkin_id, principal, req)
        out: dict[str, Any] = {"id": checkin_id, "state": "approved"}
        # Surface badge object key (not bytes) so a printer UI can fetch
        # each one via the existing single-badge download endpoint.
        if isinstance(result, dict):
            badge = result.get("badge")
            if badge is not None:
                badge_id = getattr(badge, "badge_id", None) or (
                    badge.get("badge_id") if isinstance(badge, dict) else None
                )
                if badge_id:
                    out["badgeId"] = badge_id
        return out

    return await run_bulk_handlers(ids, _handle, atomic=atomic)


@write_handler("checkin.bulk_reject", invalidates=_CHECKIN_INVALIDATES)
async def _checkin_bulk_reject(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    actor_id = str(extras.get("actor_id") or "bulk")
    reason = str(extras.get("reason") or "")[:500] or None
    principal = _ActorStub(user_id=actor_id)

    async def _handle(checkin_id: str) -> dict[str, Any]:
        req = CheckinConfirmRequest(action="reject", notes=reason)
        await confirm_checkin(checkin_id, principal, req)
        return {"id": checkin_id, "state": "rejected"}

    return await run_bulk_handlers(ids, _handle, atomic=atomic)


@write_handler(
    "checkin.bulk_force_approve_pending", invalidates=_CHECKIN_INVALIDATES
)
async def _checkin_bulk_force_approve_pending(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Batch-unstick check-ins parked in ``PENDING_VERIFICATION``.

    Mirrors the single-item ``POST /v1/checkins/{id}/force-approve-pending``
    endpoint. The underlying service raises 409 on any check-in not in
    ``PENDING_VERIFICATION``; ``run_bulk_handlers`` captures that per id
    and surfaces it in the ``failed`` array — partial success is normal
    when an operator selects a mixed batch.
    """
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    actor_id = str(extras.get("actor_id") or "bulk")
    actor_role = str(extras.get("actor_role") or "super_admin")
    request_id = extras.get("request_id")

    async def _handle(checkin_id: str) -> dict[str, Any]:
        await force_approve_pending_verification(
            checkin_id,
            actor_id=actor_id,
            actor_role=actor_role,
            request_id=request_id,
        )
        return {"id": checkin_id, "state": "pending_approval"}

    return await run_bulk_handlers(ids, _handle, atomic=atomic)
