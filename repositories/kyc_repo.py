from __future__ import annotations

import time
from typing import Any, Optional

from pymongo import ReturnDocument

from core.database import db
from schemas.imports import KYCStatus
from schemas.kyc_schema import (
    KYCVerificationCreate,
    KYCVerificationOut,
    KYCVerificationUpdate,
)

VERIFICATION_COLLECTION = "kyc_verifications"
WEBHOOK_EVENT_COLLECTION = "kyc_webhook_events"


# ── Verification rows ────────────────────────────────────────────────

async def create_kyc_verification(
    payload: KYCVerificationCreate,
) -> KYCVerificationOut:
    doc = payload.model_dump()
    doc["status"] = payload.status.value
    result = await db[VERIFICATION_COLLECTION].insert_one(doc)
    fetched = await db[VERIFICATION_COLLECTION].find_one(
        {"_id": result.inserted_id}
    )
    return KYCVerificationOut(**fetched)


async def get_kyc_verification(
    filter_dict: dict,
) -> Optional[KYCVerificationOut]:
    doc = await db[VERIFICATION_COLLECTION].find_one(filter_dict)
    if doc is None:
        return None
    return KYCVerificationOut(**doc)


async def get_kyc_by_checkin(
    checkin_id: str,
) -> Optional[KYCVerificationOut]:
    return await get_kyc_verification({"checkin_id": checkin_id})


async def get_kyc_by_reference(
    reference_id: str,
) -> Optional[KYCVerificationOut]:
    return await get_kyc_verification({"reference_id": reference_id})


async def update_kyc_verification(
    filter_dict: dict, data: KYCVerificationUpdate
) -> Optional[KYCVerificationOut]:
    update_dict = {
        k: v for k, v in data.model_dump(exclude_none=True).items() if v is not None
    }
    if "status" in update_dict and isinstance(update_dict["status"], KYCStatus):
        update_dict["status"] = update_dict["status"].value
    doc = await db[VERIFICATION_COLLECTION].find_one_and_update(
        filter_dict,
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        return None
    return KYCVerificationOut(**doc)


async def delete_kyc_verification(filter_dict: dict) -> int:
    result = await db[VERIFICATION_COLLECTION].delete_one(filter_dict)
    return int(result.deleted_count)


# ── Webhook idempotency ──────────────────────────────────────────────

async def is_webhook_event_processed(
    *, provider: str, event_id: str
) -> bool:
    """Idempotency check.

    The unique sparse index on ``event_id`` already enforces this at the
    database level — but a cheap pre-check avoids round-tripping a full
    insert on duplicate webhook deliveries.
    """
    doc = await db[WEBHOOK_EVENT_COLLECTION].find_one(
        {"provider": provider, "event_id": event_id}
    )
    if doc is None:
        return False
    return doc.get("processing_status") in {
        "processed",
        "noop",
        "replayed_manually",
    }


async def find_latest_webhook_event_for_checkin(
    checkin_id: str,
) -> Optional[dict[str, Any]]:
    """Return the most recent stored webhook whose echoed metadata
    points at this check-in.

    Used by the manual replay path when a webhook was captured but
    rejected at signature time — the raw_payload is trusted enough for
    a super_admin to re-apply once the underlying cause is fixed (or
    bypassed via this endpoint). Handles both snake_case and camelCase
    metadata key variants and both common payload roots so a small
    change in Dojah's envelope doesn't strand stuck check-ins.
    """
    return await db[WEBHOOK_EVENT_COLLECTION].find_one(
        {
            "$or": [
                {"raw_payload.data.metadata.checkin_id": checkin_id},
                {"raw_payload.data.metadata.checkinId": checkin_id},
                {"raw_payload.metadata.checkin_id": checkin_id},
                {"raw_payload.metadata.checkinId": checkin_id},
            ]
        },
        sort=[("received_at", -1)],
    )


async def update_webhook_event_status(
    *,
    event_id: str,
    provider: str,
    processing_status: str,
    error: Optional[str] = None,
) -> None:
    """Stamp the processing_status / error on a stored event.

    No-ops silently if the event isn't found — the replay flow uses
    this for audit, not control-flow.
    """
    try:
        update_doc = {
            "processing_status": processing_status,
            "error": error,
            "last_processed_at": int(time.time()),
        }
        if processing_status == "replayed_manually":
            update_doc["last_replayed_at"] = int(time.time())
        await db[WEBHOOK_EVENT_COLLECTION].update_one(
            {"provider": provider, "event_id": event_id},
            {"$set": update_doc},
        )
    except Exception:
        pass


async def record_webhook_event(
    *,
    provider: str,
    event_id: str,
    event_type: str,
    reference_id: Optional[str],
    raw_payload: dict[str, Any],
    signature_valid: bool,
    processing_status: str,
    error: Optional[str] = None,
) -> Optional[str]:
    """Insert the audit row. Returns the new id, or ``None`` on
    duplicate (the ``event_id`` unique index will reject the second
    write — that's a feature, not a bug).
    """
    try:
        result = await db[WEBHOOK_EVENT_COLLECTION].insert_one(
            {
                "provider": provider,
                "event_id": event_id,
                "event_type": event_type,
                "reference_id": reference_id,
                "raw_payload": raw_payload,
                "signature_valid": signature_valid,
                "processing_status": processing_status,
                "error": error,
                "received_at": int(time.time()),
            }
        )
        return str(result.inserted_id)
    except Exception:
        return None
