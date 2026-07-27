from __future__ import annotations

import logging
import time

from core.database import db
from repositories.retention_policy_repo import get_retention_policies
from repositories.deletion_log_repo import create_deletion_log
from repositories.tenant_repo import get_tenants
from schemas.deletion_log_schema import DeletionLogCreate
from schemas.imports import CheckinState, DeletionAction, RetentionScope

logger = logging.getLogger(__name__)


async def run_retention_cleanup():
    """Scheduled job: query expired data per tenant's retention policies, delete or anonymise."""
    tenants = await get_tenants()
    for tenant in tenants:
        policies = await get_retention_policies({"tenant_id": tenant.id})
        for policy in policies:
            cutoff = int(time.time()) - (policy.retention_days * 86400)
            action = policy.action

            if policy.scope == "visit_sessions":
                await _cleanup_visit_sessions(tenant.id, cutoff, action)
            elif policy.scope == "id_images":
                await _cleanup_id_images(tenant.id, cutoff, action)
            elif policy.scope == "visitor_profiles":
                await _cleanup_visitor_profiles(tenant.id, cutoff, action)
            elif policy.scope == RetentionScope.CHECKINS.value:
                await _cleanup_checkins(tenant.id, cutoff, action)

    logger.info("Retention cleanup completed")


async def _cleanup_visit_sessions(tenant_id: str, cutoff: int, action: DeletionAction):
    filter_dict = {
        "tenant_id": tenant_id,
        "check_in_time": {"$lt": cutoff},
        "status": {"$in": ["checked_out", "denied", "cancelled"]},
    }

    if action == DeletionAction.DELETE:
        cursor = db.visit_sessions.find(filter_dict, {"_id": 1})
        async for doc in cursor:
            await db.visit_sessions.delete_one({"_id": doc["_id"]})
            await create_deletion_log(
                DeletionLogCreate(
                    tenant_id=tenant_id,
                    entity_type="visit_session",
                    entity_id=str(doc["_id"]),
                    reason="retention_policy_expired",
                    action=DeletionAction.DELETE,
                    performed_by="system",
                )
            )
    else:
        cursor = db.visit_sessions.find(filter_dict, {"_id": 1})
        async for doc in cursor:
            await db.visit_sessions.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "visitor_name_snapshot": "ANONYMISED",
                        "company_snapshot": "ANONYMISED",
                        "host_name_snapshot": "ANONYMISED",
                        "purpose": "ANONYMISED",
                    }
                },
            )
            await create_deletion_log(
                DeletionLogCreate(
                    tenant_id=tenant_id,
                    entity_type="visit_session",
                    entity_id=str(doc["_id"]),
                    reason="retention_policy_expired",
                    action=DeletionAction.ANONYMISE,
                    performed_by="system",
                )
            )


async def _cleanup_checkins(tenant_id: str, cutoff: int, action: DeletionAction):
    """Purge or anonymise terminal kiosk check-ins past their retention window.

    The kiosk submit path writes ``checkins`` and creates NO ``visit_sessions``
    row (see services/checkin_service.py), so without this branch kiosk
    visitors were retained indefinitely. ``tenant_specific_data`` carries the
    raw form answers and is the richest PII on the record.
    """
    filter_dict = {
        "tenant_id": tenant_id,
        "date_created": {"$lt": cutoff},
        "state": {
            "$in": [
                CheckinState.CHECKED_OUT.value,
                CheckinState.REJECTED.value,
            ]
        },
    }

    if action == DeletionAction.DELETE:
        cursor = db.checkins.find(filter_dict, {"_id": 1})
        async for doc in cursor:
            await db.checkins.delete_one({"_id": doc["_id"]})
            await create_deletion_log(
                DeletionLogCreate(
                    tenant_id=tenant_id,
                    entity_type="checkin",
                    entity_id=str(doc["_id"]),
                    reason="retention_policy_expired",
                    action=DeletionAction.DELETE,
                    performed_by="system",
                )
            )
    else:
        # Project manual_verification so we can tell whether it is already
        # populated. It is a nested optional subdocument whose sibling
        # fields (verified_by_user_id, verified_at, ...) are required by
        # ManualVerificationInfo when present — a bare
        # "manual_verification.notes" $set on a doc where the field is
        # currently null would fabricate a malformed partial subdocument
        # that doesn't match the schema. Guard it; only scrub the note when
        # the subdocument already exists.
        cursor = db.checkins.find(filter_dict, {"_id": 1, "manual_verification": 1})
        async for doc in cursor:
            set_fields = {
                "tenant_specific_data": {},
                "host_name": "ANONYMISED",
                "department_name": "ANONYMISED",
                # purpose is a required (always-present) subdocument, so
                # scrubbing its free-text detail field is always safe.
                "purpose.purpose_details": "ANONYMISED",
                "approval_notes": "ANONYMISED",
                "rejection_reason": "ANONYMISED",
            }
            if doc.get("manual_verification"):
                set_fields["manual_verification.notes"] = "ANONYMISED"

            await db.checkins.update_one(
                {"_id": doc["_id"]},
                {"$set": set_fields},
            )
            await create_deletion_log(
                DeletionLogCreate(
                    tenant_id=tenant_id,
                    entity_type="checkin",
                    entity_id=str(doc["_id"]),
                    reason="retention_policy_expired",
                    action=DeletionAction.ANONYMISE,
                    performed_by="system",
                )
            )


async def _cleanup_id_images(tenant_id: str, cutoff: int, action: DeletionAction):
    filter_dict = {
        "tenant_id": tenant_id,
        "last_verification_date": {"$lt": cutoff},
        "id_image_object_key": {"$ne": None},
        "deleted_at": None,
    }
    cursor = db.visitor_profiles.find(filter_dict, {"_id": 1})
    async for doc in cursor:
        await db.visitor_profiles.update_one(
            {"_id": doc["_id"]},
            {"$set": {"id_image_object_key": None, "id_number": None}},
        )
        await create_deletion_log(
            DeletionLogCreate(
                tenant_id=tenant_id,
                entity_type="visitor_profile_id_image",
                entity_id=str(doc["_id"]),
                reason="retention_policy_expired",
                action=DeletionAction.DELETE,
                performed_by="system",
            )
        )


async def _cleanup_visitor_profiles(
    tenant_id: str, cutoff: int, action: DeletionAction
):
    import hashlib

    filter_dict = {
        "tenant_id": tenant_id,
        "date_created": {"$lt": cutoff},
        "deleted_at": None,
    }

    if action == DeletionAction.DELETE:
        cursor = db.visitor_profiles.find(filter_dict, {"_id": 1})
        async for doc in cursor:
            await db.visitor_profiles.delete_one({"_id": doc["_id"]})
            await create_deletion_log(
                DeletionLogCreate(
                    tenant_id=tenant_id,
                    entity_type="visitor_profile",
                    entity_id=str(doc["_id"]),
                    reason="retention_policy_expired",
                    action=DeletionAction.DELETE,
                    performed_by="system",
                )
            )
    else:
        cursor = db.visitor_profiles.find(filter_dict, {"_id": 1, "phone": 1})
        async for doc in cursor:
            hashed_phone = hashlib.sha256(
                (doc.get("phone") or "").encode()
            ).hexdigest()[:16]
            await db.visitor_profiles.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "full_name": "ANONYMISED",
                        "phone": hashed_phone,
                        "email_address": None,
                        "company": None,
                        "photo_object_key": None,
                        "id_number": None,
                        "id_image_object_key": None,
                        "deleted_at": int(time.time()),
                    }
                },
            )
            await create_deletion_log(
                DeletionLogCreate(
                    tenant_id=tenant_id,
                    entity_type="visitor_profile",
                    entity_id=str(doc["_id"]),
                    reason="retention_policy_expired",
                    action=DeletionAction.ANONYMISE,
                    performed_by="system",
                )
            )
