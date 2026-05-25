import time
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException
from typing import List, Optional

from core.errors import AppException, ErrorCode
from repositories.privacy_notice_repo import (
    count_privacy_notices,
    create_privacy_notice,
    get_active_notice_for_tenant,
    get_privacy_notices,
    update_privacy_notice,
)
from repositories.tenant_repo import get_tenant
from schemas.privacy_notice_schema import (
    PrivacyNoticeCreate,
    PrivacyNoticeUpdate,
    PrivacyNoticeOut,
)
from services.privacy_notice_defaults import build_default_notice_content

# Fields whose change mints a new version (A.1). Toggling is_active or editing
# effective_date alone does NOT bump the version.
_VERSIONED_FIELDS = ("title", "summary", "full_text", "display_mode")


def _mint_version_code() -> str:
    """Opaque, monotonic version identifier exposed publicly as ``versionId``.

    ISO-8601 UTC down to the microsecond so two edits in the same second still
    mint distinct versions; the frontend treats it as an opaque string.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_title(title: Optional[str]) -> None:
    if title is None:
        return
    if not (1 <= len(title) <= 200):
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="title must be between 1 and 200 characters",
            details={"title": "title must be between 1 and 200 characters"},
        )


async def add_privacy_notice(
    notice_data: PrivacyNoticeCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> PrivacyNoticeOut:
    _validate_title(notice_data.title)
    # Auto-mint the version identifier when the client did not supply one.
    if not notice_data.version_code:
        notice_data.version_code = _mint_version_code()
    # Deactivate any existing active notice for this tenant (single-active).
    existing_active = await get_active_notice_for_tenant(notice_data.tenant_id)
    if existing_active and notice_data.is_active:
        await update_privacy_notice(
            {"_id": ObjectId(existing_active.id)},
            PrivacyNoticeUpdate(is_active=False),
        )
    return await create_privacy_notice(notice_data, preassigned_id=preassigned_id)


async def seed_default_privacy_notice(
    tenant_id: str,
    *,
    company_name: Optional[str] = None,
    dpo_contact_email: Optional[str] = None,
) -> Optional[PrivacyNoticeOut]:
    """Create the VisiChek-style default active notice for a tenant.

    Idempotent: returns the existing active notice if one already exists.
    Best-effort callers should swallow exceptions — a seed failure must never
    block tenant provisioning or a read.
    """
    existing = await get_active_notice_for_tenant(tenant_id)
    if existing:
        return existing

    if company_name is None or dpo_contact_email is None:
        tenant = None
        if ObjectId.is_valid(tenant_id):
            tenant = await get_tenant({"_id": ObjectId(tenant_id)})
        if company_name is None:
            company_name = getattr(tenant, "company_name", None) or "Our organisation"
        if dpo_contact_email is None:
            dpo_contact_email = getattr(tenant, "dpo_contact_email", None)

    content = build_default_notice_content(
        company_name=company_name or "Our organisation",
        dpo_contact_email=dpo_contact_email,
    )
    now = int(time.time())
    notice = PrivacyNoticeCreate(
        tenant_id=tenant_id,
        title=content["title"],
        summary=content["summary"],
        full_text=content["full_text"],
        display_mode=__import__(
            "schemas.imports", fromlist=["NoticeDisplayMode"]
        ).NoticeDisplayMode.ACTIVE_CONSENT,
        is_active=True,
        effective_date=now,
    )
    return await add_privacy_notice(notice_data=notice)


async def retrieve_active_notice(
    tenant_id: str, *, seed_if_missing: bool = False
) -> PrivacyNoticeOut:
    result = await get_active_notice_for_tenant(tenant_id)
    if not result and seed_if_missing and ObjectId.is_valid(tenant_id):
        seeded = await seed_default_privacy_notice(tenant_id)
        if seeded:
            return seeded
    if not result:
        raise HTTPException(
            status_code=404, detail="No active privacy notice found for tenant"
        )
    return result


async def retrieve_privacy_notices(
    tenant_id: str, start=0, stop=100
) -> List[PrivacyNoticeOut]:
    return await get_privacy_notices(
        filter_dict={"tenant_id": tenant_id}, start=start, stop=stop
    )


async def count_privacy_notices_for_tenant(tenant_id: str) -> int:
    return await count_privacy_notices({"tenant_id": tenant_id})


async def update_notice_by_id(
    notice_id: str, tenant_id: str, notice_data: PrivacyNoticeUpdate
) -> PrivacyNoticeOut:
    if not ObjectId.is_valid(notice_id):
        raise HTTPException(status_code=400, detail="Invalid notice ID format")
    _validate_title(notice_data.title)

    # Mint a new version when any version-gated field actually changes (A.1).
    changed = notice_data.model_dump(exclude_none=True)
    if any(field in changed for field in _VERSIONED_FIELDS):
        current = await get_active_notice_for_tenant(tenant_id)
        # Fall back to minting regardless if we can't load the current row.
        if current is None or current.id != notice_id:
            from repositories.privacy_notice_repo import get_privacy_notice

            current = await get_privacy_notice(
                {"_id": ObjectId(notice_id), "tenant_id": tenant_id}
            )
        content_actually_changed = current is None or any(
            getattr(current, field, None) != changed[field]
            for field in _VERSIONED_FIELDS
            if field in changed
        )
        if content_actually_changed:
            notice_data.version_code = _mint_version_code()

    # If this update re-activates the notice, deactivate any other active one.
    if notice_data.is_active is True:
        existing_active = await get_active_notice_for_tenant(tenant_id)
        if existing_active and existing_active.id != notice_id:
            await update_privacy_notice(
                {"_id": ObjectId(existing_active.id)},
                PrivacyNoticeUpdate(is_active=False),
            )

    result = await update_privacy_notice(
        {"_id": ObjectId(notice_id), "tenant_id": tenant_id}, notice_data
    )
    if not result:
        raise HTTPException(
            status_code=404, detail="Privacy notice not found or update failed"
        )
    return result
