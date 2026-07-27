from __future__ import annotations

import logging
import time

from core.database import db
from repositories.retention_policy_repo import get_retention_policies
from repositories.deletion_log_repo import create_deletion_log
from repositories.tenant_repo import get_tenants
from schemas.deletion_log_schema import DeletionLogCreate
from schemas.imports import CheckinState, DeletionAction, RetentionScope
from schemas.retention_policy_schema import RetentionPolicyOut

logger = logging.getLogger(__name__)

# Scopes an implicit (settings-derived) policy covers. ``id_images`` is
# deliberately excluded — image retention is a separate, deliberate decision
# and must stay opt-in via an explicit RetentionPolicy row.
_IMPLICIT_VISITOR_SCOPES: tuple[str, ...] = (
    RetentionScope.VISIT_SESSIONS.value,
    RetentionScope.CHECKINS.value,
    RetentionScope.VISITOR_PROFILES.value,
)


async def _implicit_policies_from_settings(tenant_id: str) -> list[RetentionPolicyOut]:
    """Derive retention policies from ``tenant_settings`` for tenants with none.

    The Settings UI presents ``visitor_data_retention_days`` as *the*
    retention control, but the sweep only ever honoured explicit
    ``retention_policies`` rows — so a tenant who configured retention in
    Settings and never created a policy retained visitor data forever.

    These are computed per sweep and never persisted: an explicit policy row
    always wins, and the tenant can still see exactly one source of truth in
    the UI. Returns ``[]`` when there is no settings document, or when the
    configured window is 0 / None (which means "retain indefinitely" — we
    never invent a purge the operator did not ask for).
    """
    try:
        settings_doc = await db["tenant_settings"].find_one({"tenant_id": tenant_id})
    except Exception:
        logger.warning(
            "retention: tenant_settings lookup failed tenant=%s", tenant_id,
            exc_info=True,
        )
        return []

    if not settings_doc:
        return []

    days = settings_doc.get("visitor_data_retention_days")
    if not isinstance(days, int) or days <= 0:
        return []

    try:
        action = DeletionAction(settings_doc.get("deletion_action") or "anonymise")
    except ValueError:
        action = DeletionAction.ANONYMISE

    return [
        RetentionPolicyOut(
            tenant_id=tenant_id,
            scope=scope,
            retention_days=days,
            action=action,
        )
        for scope in _IMPLICIT_VISITOR_SCOPES
    ]


async def run_retention_cleanup():
    """Scheduled job: query expired data per tenant's retention policies, delete or anonymise."""
    tenants = await get_tenants()
    for tenant in tenants:
        policies = await get_retention_policies({"tenant_id": tenant.id})
        if not policies:
            policies = await _implicit_policies_from_settings(tenant.id)
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
