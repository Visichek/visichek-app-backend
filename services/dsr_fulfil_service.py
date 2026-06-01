"""DSR fulfilment for consent-withdrawal, correction, and deletion requests.

These three sit alongside the access export (``dsr_access_export_service``) and
turn a status-flip into a real action on the subject's data:

* **consent_withdrawal** — opt the profile out of profiling and mark every
  consent record / visit session withdrawn.
* **correction** — apply an allowlisted set of profile field corrections and
  record the before/after diff.
* **deletion** — schedule the visitor-profile erasure (soft-delete + 14-day
  purge) and close the DSR in one job.

Each returns a JSON-serialisable dict and is invoked from the matching
``dsr.fulfil_*`` writer in ``services/dsr_writer.py``, which owns the audit
event. Profile mutations go through ``update_profile_by_id`` /
``schedule_profile_erasure`` so the DPO-gated profile-write path is preserved.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from schemas.data_subject_request_schema import DSRUpdate
from schemas.imports import DSRStatus, ProfilingPreference
from schemas.visitor_profile_schema import VisitorProfileUpdate

# Fields a data subject can have corrected (mirror DSRCorrectionRequest).
_CORRECTABLE_FIELDS = ("full_name", "phone", "email_address", "company")


async def fulfil_consent_withdrawal(dsr_id: str, tenant_id: str) -> Dict[str, Any]:
    """Revoke the subject's consent and disable profiling, then close the DSR."""
    from repositories.consent_record_repo import mark_consent_withdrawn
    from repositories.visit_session_repo import mark_consent_withdrawn_for_visitor
    from services.data_subject_request_service import (
        retrieve_dsr_by_id,
        update_dsr_by_id,
    )
    from services.visitor_profile_service import update_profile_by_id

    dsr = await retrieve_dsr_by_id(dsr_id=dsr_id, tenant_id=tenant_id)
    visitor_profile_id = dsr.visitor_profile_id

    await update_profile_by_id(
        profile_id=visitor_profile_id,
        tenant_id=tenant_id,
        profile_data=VisitorProfileUpdate(
            profiling_preference=ProfilingPreference.OPTED_OUT
        ),
    )

    now = int(time.time())
    consent_marked = await mark_consent_withdrawn(tenant_id, visitor_profile_id, now)
    sessions_marked = await mark_consent_withdrawn_for_visitor(
        tenant_id, visitor_profile_id, now
    )

    await update_dsr_by_id(
        dsr_id=dsr_id,
        tenant_id=tenant_id,
        dsr_data=DSRUpdate(
            status=DSRStatus.COMPLETED,
            resolution=(
                "Consent withdrawn — profiling disabled and consent records marked "
                f"withdrawn ({consent_marked} consent record(s), {sessions_marked} "
                "visit session(s))."
            ),
        ),
    )
    return {
        "id": dsr_id,
        "status": "completed",
        "visitor_profile_id": visitor_profile_id,
        "consent_records_marked": consent_marked,
        "visit_sessions_marked": sessions_marked,
    }


async def fulfil_correction(
    dsr_id: str, tenant_id: str, corrections: Dict[str, Any]
) -> Dict[str, Any]:
    """Apply allowlisted profile corrections, recording a before/after diff."""
    from bson import ObjectId

    from core.errors import AppException, ErrorCode
    from repositories.visitor_profile_repo import get_visitor_profile
    from services.data_subject_request_service import (
        retrieve_dsr_by_id,
        update_dsr_by_id,
    )
    from services.visitor_profile_service import update_profile_by_id

    allowed = {
        k: v
        for k, v in corrections.items()
        if k in _CORRECTABLE_FIELDS and v is not None
    }
    if not allowed:
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="No correctable fields supplied",
            details={
                "code": "DSR_CORRECTION_EMPTY",
                "allowed": list(_CORRECTABLE_FIELDS),
            },
        )

    dsr = await retrieve_dsr_by_id(dsr_id=dsr_id, tenant_id=tenant_id)
    visitor_profile_id = dsr.visitor_profile_id

    before = None
    if ObjectId.is_valid(visitor_profile_id):
        before = await get_visitor_profile(
            {"_id": ObjectId(visitor_profile_id), "tenant_id": tenant_id}
        )

    after = await update_profile_by_id(
        profile_id=visitor_profile_id,
        tenant_id=tenant_id,
        profile_data=VisitorProfileUpdate(**allowed),
    )

    changes: Dict[str, Dict[str, Any]] = {}
    for field in allowed:
        old = getattr(before, field, None) if before is not None else None
        new = getattr(after, field, None)
        if old != new:
            changes[field] = {"from": old, "to": new}

    summary = ", ".join(changes.keys()) if changes else "no effective change"
    await update_dsr_by_id(
        dsr_id=dsr_id,
        tenant_id=tenant_id,
        dsr_data=DSRUpdate(
            status=DSRStatus.COMPLETED,
            resolution=f"Profile corrected ({summary}).",
        ),
    )
    return {
        "id": dsr_id,
        "status": "completed",
        "visitor_profile_id": visitor_profile_id,
        "changes": changes,
    }


async def fulfil_deletion(
    dsr_id: str, tenant_id: str, *, actor_id: str = ""
) -> Dict[str, Any]:
    """Schedule the visitor-profile erasure and close the deletion DSR atomically."""
    from services.data_subject_request_service import (
        retrieve_dsr_by_id,
        update_dsr_by_id,
    )
    from services.visitor_profile_service import schedule_profile_erasure

    dsr = await retrieve_dsr_by_id(dsr_id=dsr_id, tenant_id=tenant_id)
    visitor_profile_id = dsr.visitor_profile_id

    await schedule_profile_erasure(
        profile_id=visitor_profile_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        reason="dsr_deletion_request",
    )

    await update_dsr_by_id(
        dsr_id=dsr_id,
        tenant_id=tenant_id,
        dsr_data=DSRUpdate(
            status=DSRStatus.COMPLETED,
            resolution=(
                "Erasure scheduled — profile soft-deleted; permanent purge in 14 days. "
                "Restorable within the grace window from the scheduled-erasures queue."
            ),
        ),
    )
    return {
        "id": dsr_id,
        "status": "completed",
        "visitor_profile_id": visitor_profile_id,
    }
