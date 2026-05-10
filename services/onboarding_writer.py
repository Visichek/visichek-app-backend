"""Queued bulk handlers for onboarding submissions.

Single-item accept/reject/archive flows stay synchronous on the
existing routes — they have to return the provisioned tenant + super
admin immediately for the admin UI. Only the bulk paths go through
the queue.
"""

from __future__ import annotations

from typing import Any

from core.bulk import run_bulk_handlers
from core.queue.write_pipeline import write_handler
from schemas.onboarding_submission_schema import OnboardingRejectRequest
from services.onboarding_submission_service import (
    archive_onboarding_submission,
    reject_onboarding_submission,
)


@write_handler("onboarding.bulk_archive", invalidates=[])
async def _onboarding_bulk_archive(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    actor_id = ""  # writer doesn't have direct access; safe default

    async def _handle(submission_id: str) -> dict[str, Any]:
        await archive_onboarding_submission(
            submission_id=submission_id, actor_id=actor_id
        )
        return {"id": submission_id, "archived": True}

    return await run_bulk_handlers(ids, _handle, atomic=atomic)


@write_handler("onboarding.bulk_reject", invalidates=[])
async def _onboarding_bulk_reject(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    notes = str(extras.get("notes") or "")
    actor_id = ""

    async def _handle(submission_id: str) -> dict[str, Any]:
        payload = OnboardingRejectRequest(review_notes=notes or "rejected via bulk")
        await reject_onboarding_submission(
            submission_id=submission_id, payload=payload, actor_id=actor_id
        )
        return {"id": submission_id, "status": "rejected"}

    return await run_bulk_handlers(ids, _handle, atomic=atomic)
