from __future__ import annotations

import logging
import time
from bson import ObjectId
from fastapi import HTTPException
from typing import List, Optional

from repositories.deletion_log_repo import create_deletion_log
from repositories.visitor_profile_repo import (
    create_visitor_profile,
    get_visitor_profile,
    get_visitor_profile_by_phone,
    get_visitor_profile_by_email,
    get_visitor_profile_by_id_number,
    get_visitor_profiles,
    get_profiles_due_for_purge,
    get_scheduled_for_deletion_profiles,
    hard_delete_visitor_profile,
    update_visitor_profile,
    soft_delete_visitor_profile,
    schedule_visitor_profile_purge,
    restore_visitor_profile,
    search_visitor_profiles,
)
from schemas.deletion_log_schema import DeletionLogCreate
from schemas.imports import DeletionAction
from schemas.visitor_profile_schema import (
    VisitorProfileCreate,
    VisitorProfileUpdate,
    VisitorProfileOut,
    VisitorProfileWithSummaryOut,
)

logger = logging.getLogger(__name__)

# Grace window between a DSR erasure (soft-delete) and permanent deletion.
# Mirrors the statutory "cooling-off" period so an accidental or contested
# erasure can be reversed before the data is irrecoverably purged.
ERASURE_GRACE_SECONDS = 14 * 24 * 60 * 60  # 14 days


async def get_or_create_visitor_profile(
    tenant_id: str,
    phone: str | None = None,
    full_name: str = "Unknown",
    company: str | None = None,
    photo_object_key: str | None = None,
    email: str | None = None,
    id_number: str | None = None,
) -> VisitorProfileOut:
    """Find an existing visitor profile or create a new one.

    Lookup order is phone → email → id_number, with phone as the
    canonical identity key per tenant. The MongoDB sparse-unique index
    on ``(tenant_id, phone)`` enforces this at the database layer, so a
    racing concurrent submit will fail the insert; the helper retries
    the phone lookup so the second writer reuses the first writer's row.
    """
    if phone:
        existing = await get_visitor_profile_by_phone(tenant_id=tenant_id, phone=phone)
        if existing:
            return existing
    if email:
        existing = await get_visitor_profile_by_email(tenant_id=tenant_id, email=email)
        if existing:
            return existing
    if id_number:
        existing = await get_visitor_profile_by_id_number(
            tenant_id=tenant_id, id_number=id_number
        )
        if existing:
            return existing

    profile = VisitorProfileCreate(
        tenant_id=tenant_id,
        phone=phone,
        full_name=full_name,
        company=company,
        photo_object_key=photo_object_key,
        email_address=email,
        id_number=id_number,
    )
    try:
        return await create_visitor_profile(profile)
    except Exception as exc:
        # Phone uniqueness collision: another concurrent writer just
        # created the profile we wanted. Re-fetch by phone and surface
        # that record. Anything else (no phone supplied, or a different
        # error) re-raises.
        if not phone:
            raise
        msg = str(exc).lower()
        if "duplicate key" not in msg and "e11000" not in msg:
            raise
        retry = await get_visitor_profile_by_phone(tenant_id=tenant_id, phone=phone)
        if retry is not None:
            return retry
        raise


async def retrieve_visitor_profile_by_id(
    profile_id: str, tenant_id: str
) -> VisitorProfileOut:
    if not ObjectId.is_valid(profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID format")
    result = await get_visitor_profile(
        {"_id": ObjectId(profile_id), "tenant_id": tenant_id}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Visitor profile not found")
    return result


async def retrieve_visitor_profiles(
    tenant_id: str, start=0, stop=100
) -> List[VisitorProfileOut]:
    return await get_visitor_profiles(
        filter_dict={"tenant_id": tenant_id}, start=start, stop=stop
    )


async def search_profiles(
    tenant_id: str, query: str, start=0, stop=20
) -> List[VisitorProfileOut]:
    return await search_visitor_profiles(
        tenant_id=tenant_id, query=query, start=start, stop=stop
    )


async def update_profile_by_id(
    profile_id: str, tenant_id: str, profile_data: VisitorProfileUpdate
) -> VisitorProfileOut:
    if not ObjectId.is_valid(profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID format")
    result = await update_visitor_profile(
        {"_id": ObjectId(profile_id), "tenant_id": tenant_id}, profile_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Visitor profile not found or update failed"
        )
    return result


async def _enrich_visitor_profile(
    profile: VisitorProfileOut,
) -> VisitorProfileWithSummaryOut:
    from services.summary_resolver import resolve_tenant_summary

    tenant_summary = await resolve_tenant_summary(profile.tenant_id)
    data = profile.model_dump(by_alias=False)
    data["tenant_summary"] = tenant_summary
    return VisitorProfileWithSummaryOut(**data)


async def retrieve_visitor_profiles_with_summary(
    tenant_id: str, start: int = 0, stop: int = 100
) -> List[VisitorProfileWithSummaryOut]:
    import asyncio

    profiles = await retrieve_visitor_profiles(
        tenant_id=tenant_id, start=start, stop=stop
    )
    return list(await asyncio.gather(*[_enrich_visitor_profile(p) for p in profiles]))


async def retrieve_visitor_profile_by_id_with_summary(
    profile_id: str, tenant_id: str
) -> VisitorProfileWithSummaryOut:
    profile = await retrieve_visitor_profile_by_id(
        profile_id=profile_id, tenant_id=tenant_id
    )
    return await _enrich_visitor_profile(profile)


async def soft_delete_profile(profile_id: str, tenant_id: str) -> VisitorProfileOut:
    if not ObjectId.is_valid(profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID format")
    result = await soft_delete_visitor_profile(
        {"_id": ObjectId(profile_id), "tenant_id": tenant_id}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Visitor profile not found")
    return result


# ---------------------------------------------------------------------------
# DSR erasure: soft-delete now + scheduled permanent deletion
# ---------------------------------------------------------------------------


async def schedule_profile_erasure(
    profile_id: str,
    tenant_id: str,
    actor_id: str = "",
    reason: Optional[str] = None,
) -> VisitorProfileOut:
    """Fulfil a data-subject erasure request.

    Soft-deletes the visitor profile immediately and stamps
    ``scheduled_purge_at`` 14 days out; the ``run_scheduled_erasure_purge``
    sweep performs the irreversible hard delete once that window elapses.
    A ``deletion_logs`` row records the scheduled erasure for compliance.
    """
    if not ObjectId.is_valid(profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID format")
    now = int(time.time())
    purge_at = now + ERASURE_GRACE_SECONDS
    result = await schedule_visitor_profile_purge(
        {"_id": ObjectId(profile_id), "tenant_id": tenant_id, "deleted_at": None},
        deleted_at=now,
        scheduled_purge_at=purge_at,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Visitor profile not found or already scheduled for deletion",
        )
    await create_deletion_log(
        DeletionLogCreate(
            tenant_id=tenant_id,
            entity_type="visitor_profile",
            entity_id=profile_id,
            reason=reason or "dsr_erasure_request",
            action=DeletionAction.SCHEDULED,
            performed_by=actor_id or "system",
        )
    )
    return result


async def restore_profile_erasure(profile_id: str, tenant_id: str) -> VisitorProfileOut:
    """Undo a scheduled erasure while still inside the grace window."""
    if not ObjectId.is_valid(profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile ID format")
    result = await restore_visitor_profile(
        {
            "_id": ObjectId(profile_id),
            "tenant_id": tenant_id,
            "deleted_at": {"$ne": None},
        }
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No soft-deleted visitor profile found to restore",
        )
    return result


async def retrieve_scheduled_for_deletion(
    tenant_id: str, start: int = 0, stop: int = 100
) -> List[VisitorProfileOut]:
    """List this tenant's profiles awaiting permanent deletion."""
    return await get_scheduled_for_deletion_profiles(
        tenant_id=tenant_id, start=start, stop=stop
    )


async def run_scheduled_erasure_purge() -> None:
    """APScheduler sweep: permanently delete profiles past their grace window.

    The schedule for each erasure is the ``scheduled_purge_at`` timestamp
    stamped at erasure time; this hourly sweep enforces it. It runs in the
    web process (where the scheduler lives), mirroring
    ``retention_service.run_retention_cleanup``. Restoring a profile clears
    ``scheduled_purge_at``, so restored profiles are skipped here.
    """
    now = int(time.time())
    due = await get_profiles_due_for_purge(now)
    purged = 0
    for profile in due:
        if not profile.id:
            continue
        try:
            deleted = await hard_delete_visitor_profile(
                {"_id": ObjectId(profile.id), "tenant_id": profile.tenant_id}
            )
            if deleted:
                await create_deletion_log(
                    DeletionLogCreate(
                        tenant_id=profile.tenant_id,
                        entity_type="visitor_profile",
                        entity_id=profile.id,
                        reason="dsr_erasure_grace_expired",
                        action=DeletionAction.DELETE,
                        performed_by="system",
                    )
                )
                purged += 1
        except Exception:
            logger.warning(
                "scheduled_erasure_purge: failed to purge profile=%s",
                profile.id,
                exc_info=True,
            )
    if purged:
        logger.info(
            "scheduled_erasure_purge: permanently deleted %d profile(s)", purged
        )
