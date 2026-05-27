"""One-off backfill: migrate every tenant's active visitor privacy notice
onto the BlockNote ``body`` format.

Background: notices created before the BlockNote migration store their copy as
plain ``title`` / ``summary`` / ``full_text`` and carry no ``body``. This
routine regenerates the standardized approved document as BlockNote blocks
(with each tenant's details substituted) for those legacy notices.

Idempotent and safe to re-run:

* notice already has a non-empty ``body``  -> skipped (already migrated)
* active notice exists but has no ``body``  -> regenerated as blocks + new version
* no active notice and tenant is active     -> seeded with the new default
* no active notice and tenant is inactive   -> skipped (no point)

Per-tenant failures are logged and counted, never fatal — one broken tenant
must not stop the rest of the backfill.

Note: the active-notice read path is served from a 60s precompute cache, so
migrated copy may take up to a minute to appear on the kiosk after this runs.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict

from bson import ObjectId

from repositories.privacy_notice_repo import (
    get_active_notice_for_tenant,
    update_privacy_notice,
)
from repositories.tenant_repo import get_tenants
from schemas.privacy_notice_schema import PrivacyNoticeUpdate
from services.audit_service import record_audit_event
from services.privacy_notice_defaults import build_default_notice_content
from services.privacy_notice_service import (
    _mint_version_code,
    _resolve_main_super_admin_email,
    seed_default_privacy_notice,
)

logger = logging.getLogger(__name__)

_PAGE = 200


async def _iter_all_tenants() -> AsyncIterator[Any]:
    """Yield every tenant, paging through the collection so we never load the
    whole table into memory at once."""
    start = 0
    while True:
        batch = await get_tenants(filter_dict={}, start=start, stop=start + _PAGE)
        if not batch:
            return
        for tenant in batch:
            yield tenant
        if len(batch) < _PAGE:
            return
        start += _PAGE


async def backfill_blocknote_privacy_notices(*, dry_run: bool = False) -> Dict[str, Any]:
    """Walk every tenant and bring its active privacy notice onto BlockNote.

    Returns a summary dict with per-outcome counts and any per-tenant errors.
    When ``dry_run`` is True nothing is written — the counts reflect what
    *would* change.
    """
    summary: Dict[str, Any] = {
        "tenants_scanned": 0,
        "migrated": 0,
        "seeded": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    async for tenant in _iter_all_tenants():
        summary["tenants_scanned"] += 1
        tenant_id = getattr(tenant, "id", None) or ""
        if not tenant_id:
            summary["skipped"] += 1
            continue
        try:
            notice = await get_active_notice_for_tenant(tenant_id)

            if notice is None:
                # No active notice at all. Seed the new default only for active
                # tenants — offboarded/inactive tenants don't need one.
                if getattr(tenant, "is_active", True):
                    if not dry_run:
                        await seed_default_privacy_notice(tenant_id)
                    summary["seeded"] += 1
                else:
                    summary["skipped"] += 1
                continue

            if notice.body:
                # Already on the block format — leave it untouched.
                summary["skipped"] += 1
                continue

            # Legacy plain-text notice: regenerate the standardized document as
            # BlockNote blocks with this tenant's details substituted.
            contact_email = await _resolve_main_super_admin_email(tenant_id)
            content = build_default_notice_content(
                company_name=getattr(tenant, "company_name", None) or "Our organisation",
                contact_email=contact_email,
                privacy_contact=getattr(tenant, "dpo_contact_email", None),
                retention_days=getattr(tenant, "retention_days", None),
            )

            if dry_run:
                summary["migrated"] += 1
                continue

            upd = PrivacyNoticeUpdate(
                title=content["title"],
                summary=content["summary"],
                full_text=content["full_text"],
                body=content["body"],
                version_code=_mint_version_code(),
            )
            await update_privacy_notice(
                {"_id": ObjectId(notice.id), "tenant_id": tenant_id}, upd
            )
            await record_audit_event(
                actor_id="privacy_notice_backfill",
                actor_role="admin",
                action="privacy_notice.updated",
                resource_type="privacy_notice",
                resource_id=notice.id or "",
                tenant_id=tenant_id,
                details={
                    "changes": {"body": "migrated_to_blocknote"},
                    "version_code": upd.version_code,
                    "migration": "blocknote_backfill",
                },
            )
            summary["migrated"] += 1
        except Exception as exc:  # noqa: BLE001 - best-effort per tenant
            summary["failed"] += 1
            summary["errors"].append({"tenant_id": tenant_id, "error": str(exc)})
            logger.warning(
                "blocknote privacy-notice backfill failed for tenant=%s",
                tenant_id,
                exc_info=True,
            )

    return summary
