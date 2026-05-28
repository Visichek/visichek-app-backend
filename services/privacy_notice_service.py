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
from schemas.imports import NoticeDisplayMode
from schemas.privacy_notice_schema import (
    PrivacyNoticeCreate,
    PrivacyNoticeUpdate,
    PrivacyNoticeOut,
)
from services.privacy_notice_defaults import build_default_notice_content

# Fields whose change mints a new version (A.1). Toggling is_active or editing
# effective_date alone does NOT bump the version.
_VERSIONED_FIELDS = ("title", "summary", "full_text", "body", "display_mode")


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


async def _resolve_main_super_admin_email(tenant_id: str) -> Optional[str]:
    """Best-effort lookup of the tenant's main super_admin email (the general
    privacy-contact address baked into the seeded notice). Never raises —
    seeding must not fail because the lookup hiccuped."""
    if not ObjectId.is_valid(tenant_id):
        return None
    try:
        from repositories.system_user_repo import get_main_super_admin

        main_sa = await get_main_super_admin(tenant_id)
        return getattr(main_sa, "email", None)
    except Exception:
        return None


async def seed_default_privacy_notice(
    tenant_id: str,
    *,
    company_name: Optional[str] = None,
    dpo_contact_email: Optional[str] = None,
    contact_email: Optional[str] = None,
    retention_days: Optional[int] = None,
) -> Optional[PrivacyNoticeOut]:
    """Create the VisiChek-style default active notice for a tenant.

    Idempotent: returns the existing active notice if one already exists.
    Best-effort callers should swallow exceptions — a seed failure must never
    block tenant provisioning or a read.
    """
    existing = await get_active_notice_for_tenant(tenant_id)
    if existing:
        return existing

    if company_name is None or dpo_contact_email is None or retention_days is None:
        tenant = None
        if ObjectId.is_valid(tenant_id):
            tenant = await get_tenant({"_id": ObjectId(tenant_id)})
        if company_name is None:
            company_name = getattr(tenant, "company_name", None) or "Our organisation"
        if dpo_contact_email is None:
            dpo_contact_email = getattr(tenant, "dpo_contact_email", None)
        if retention_days is None:
            retention_days = getattr(tenant, "retention_days", None)

    # General contact = the tenant's main super_admin email (per product
    # decision); the privacy/DPO contact prefers the DPO address and falls
    # back to that same general contact inside the builder.
    if contact_email is None:
        contact_email = await _resolve_main_super_admin_email(tenant_id)

    content = build_default_notice_content(
        company_name=company_name or "Our organisation",
        contact_email=contact_email,
        privacy_contact=dpo_contact_email,
        retention_days=retention_days,
    )
    now = int(time.time())
    notice = PrivacyNoticeCreate(
        tenant_id=tenant_id,
        title=content["title"],
        summary=content["summary"],
        full_text=content["full_text"],
        body=content["body"],
        display_mode=NoticeDisplayMode.ACTIVE_CONSENT,
        is_active=True,
        effective_date=now,
    )
    return await add_privacy_notice(notice_data=notice)


async def _derive_active_notice_from_master(
    tenant_id: str,
) -> Optional[PrivacyNoticeOut]:
    """Build the active visitor notice from the platform Visitor Privacy Policy
    master, with the tenant's ``[placeholder]`` tokens substituted.

    This is the source of truth now — the kiosk notice is no longer authored by
    the tenant. ``version_code`` mirrors the master's published version (an int)
    so visitor consent records still pin the exact version they agreed to.
    Returns ``None`` when no master is configured (caller falls back).
    """
    try:
        from services.tenant_agreement_service import retrieve_or_build

        agreement = await retrieve_or_build(tenant_id, "visitor_privacy_policy")
    except Exception:
        return None
    if agreement is None:
        return None
    return PrivacyNoticeOut.model_validate(
        {
            "_id": agreement.id,
            "tenant_id": tenant_id,
            "version_code": str(agreement.version),
            "title": agreement.title,
            "summary": agreement.summary,
            "full_text": agreement.full_text,
            "body": agreement.body,
            "display_mode": NoticeDisplayMode.ACTIVE_CONSENT,
            "is_active": True,
            "effective_date": agreement.accepted_at or agreement.created_at,
            "created_at": agreement.created_at,
            "updated_at": agreement.updated_at,
        }
    )


async def retrieve_active_notice(
    tenant_id: str, *, seed_if_missing: bool = False
) -> PrivacyNoticeOut:
    # The active visitor notice is derived from the platform Visitor Privacy
    # Policy master (tenants no longer author it). Fall back to any legacy
    # tenant-authored / seeded notice so the kiosk never breaks in an
    # environment where the master is not configured yet.
    if ObjectId.is_valid(tenant_id):
        derived = await _derive_active_notice_from_master(tenant_id)
        if derived is not None:
            return derived

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
