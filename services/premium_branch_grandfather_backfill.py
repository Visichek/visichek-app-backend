"""Task 9: grandfather existing Premium tenants ahead of the branch-quota flip.

Historical context (verified): Premium's ``max_branches`` cap has always been
``None`` (unlimited) — NOBODY ever actually paid per-location; the frontend's
location-count stepper metadata was ignored server-side. Task 9 flips
``PREMIUM_PLAN.tenant_caps.max_branches`` to ``1`` and makes every branch
beyond the first require the ``additional-branch`` add-on going forward.

This backfill runs BEFORE that flip takes effect (both ship in the same
deploy, before traffic — see ``main.py`` startup ordering) and grants every
existing Premium tenant a perpetual, zero-price ``additional-branch``
``tenant_addons`` row sized to their CURRENT active-branch count minus one,
so no existing tenant loses branch access the moment the cap goes live.

Idempotent and safe to re-run on every boot:

* Grants: a tenant already carrying a ``branch_quota`` tenant_addon row
  tagged ``metadata.granted == "premium-per-location-migration"`` is
  skipped — no duplicate grant row is ever created.
* Notifications: gated by a dedicated ``backfill_markers`` document per
  tenant (``premium_grandfather_notified:<tenant_id>``) so a tenant that
  needed zero grant (single-branch Premium) still gets notified exactly
  once, and a tenant processed on an earlier boot never gets notified
  again.

See also ``services.premium_max_branches_flip_migration`` — the sibling
one-shot migration that updates the STORED premium plan document (the
canonical config flip alone does not do this — plan_bootstrap's refresh
excludes ``tenant_caps``). That migration MUST run after this backfill.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from config.plan_tiers import PREMIUM_PLAN_NAME
from core.database import db
from repositories.addon_repo import get_addon
from repositories.system_user_repo import get_main_super_admin
from repositories.tenant_addon_repo import create_tenant_addon
from schemas.addon_schema import AddonKind, TenantAddonCreate, TenantAddonStatus
from schemas.imports import UserType
from services.addon_service import (
    ADDITIONAL_BRANCH_SLUG,
    _invalidate_tenant_addon_caches,
)
from services.notification_service import send_notification

logger = logging.getLogger(__name__)

MARKERS_COLLECTION = "backfill_markers"

# Sentinel written to ``tenant_addons.metadata.granted`` on every row this
# backfill creates — the marker the task spec calls for, and the read-side
# idempotency check for grants.
GRANT_METADATA_MARKER = "premium-per-location-migration"

# Per-tenant notification idempotency marker prefix (stored in the shared
# ``backfill_markers`` collection, one doc per tenant — distinct from the
# single high-water-mark doc pattern other backfills use, since here every
# tenant needs its own gate).
_NOTIFY_MARKER_PREFIX = "premium_grandfather_notified:"


async def _count_active_branches(tenant_id: str) -> int:
    """Active branches only — locked/deactivated branches never count
    toward the grant, mirroring Task 8's usage-count alignment."""
    return await db.branches.count_documents(
        {"tenant_id": tenant_id, "status": {"$ne": "inactive"}}
    )


async def _already_granted(tenant_id: str) -> bool:
    row = await db.tenant_addons.find_one(
        {
            "tenant_id": tenant_id,
            "addon_kind": AddonKind.BRANCH_QUOTA.value,
            "metadata.granted": GRANT_METADATA_MARKER,
        }
    )
    return row is not None


async def _already_notified(tenant_id: str) -> bool:
    doc = await db[MARKERS_COLLECTION].find_one(
        {"_id": f"{_NOTIFY_MARKER_PREFIX}{tenant_id}"}
    )
    return doc is not None


async def _mark_notified(tenant_id: str) -> None:
    try:
        await db[MARKERS_COLLECTION].update_one(
            {"_id": f"{_NOTIFY_MARKER_PREFIX}{tenant_id}"},
            {"$set": {"notified_at": int(time.time())}},
            upsert=True,
        )
    except Exception:
        logger.warning(
            "premium_branch_grandfather_backfill: failed to persist notify "
            "marker for tenant=%s (may re-notify next boot)",
            tenant_id,
            exc_info=True,
        )


async def _list_premium_tenant_ids() -> list[str]:
    """tenant_ids with an active/trialing subscription on the Premium plan."""
    plan = await db.plans.find_one({"name": PREMIUM_PLAN_NAME})
    if not plan:
        return []
    plan_id = str(plan["_id"])
    cursor = db.subscriptions.find(
        {"plan_id": plan_id, "status": {"$in": ["active", "trialing"]}},
        projection={"tenant_id": 1},
    )
    tenant_ids: list[str] = []
    async for doc in cursor:
        tenant_id = doc.get("tenant_id")
        if tenant_id:
            tenant_ids.append(str(tenant_id))
    return tenant_ids


async def _notify_tenant(tenant_id: str, *, quantity: int, branch_count: int) -> None:
    """Best-effort, one-per-tenant in-app notice explaining the new caps."""
    if await _already_notified(tenant_id):
        return
    try:
        admin = await get_main_super_admin(tenant_id)
        if admin and admin.id:
            if quantity > 0:
                entitlement = (
                    f"your organization has been grandfathered with "
                    f"{quantity} free additional branch"
                    f"{'es' if quantity != 1 else ''}, covering all "
                    f"{branch_count} of your current active branches at no "
                    "extra cost."
                )
            else:
                entitlement = (
                    "your organization's current branch is already within "
                    "the new limit, so nothing changes for you today."
                )
            body = (
                "Premium now includes 1 branch, with each branch getting "
                "its own 1,000 new visitors/month. Additional branches are "
                "available as an add-on. As an existing customer, "
                f"{entitlement}"
            )
            await send_notification(
                user_id=admin.id,
                user_type=UserType.SYSTEM_USER,
                title="Your Premium plan now has per-branch limits",
                body=body,
                type="info",
                link="/app/billing",
                tenant_id=tenant_id,
            )
        # Only mark notified once the send actually completed — a
        # transient failure (below) must retry next boot, not be
        # silently treated as delivered.
        await _mark_notified(tenant_id)
    except Exception:
        logger.warning(
            "premium_branch_grandfather_backfill: notify failed for tenant=%s",
            tenant_id,
            exc_info=True,
        )


async def backfill_premium_branch_grandfathering() -> dict[str, int]:
    """Grant + notify every existing Premium tenant. Idempotent, re-runnable.

    Returns a summary dict suitable for startup logging.
    """
    summary: dict[str, int] = {
        "tenants_seen": 0,
        "grants_created": 0,
        "already_granted": 0,
        "no_grant_needed": 0,
        "errors": 0,
    }

    addon = await get_addon({"slug": ADDITIONAL_BRANCH_SLUG})
    addon_id = addon.id if addon and addon.id else ""

    tenant_ids = await _list_premium_tenant_ids()
    for tenant_id in tenant_ids:
        summary["tenants_seen"] += 1
        try:
            branch_count = await _count_active_branches(tenant_id)
            quantity = max(0, branch_count - 1)

            if await _already_granted(tenant_id):
                summary["already_granted"] += 1
                await _notify_tenant(
                    tenant_id, quantity=quantity, branch_count=branch_count
                )
                continue

            if quantity <= 0:
                summary["no_grant_needed"] += 1
                await _notify_tenant(
                    tenant_id, quantity=quantity, branch_count=branch_count
                )
                continue

            now = int(time.time())
            payload = TenantAddonCreate(
                tenant_id=tenant_id,
                addon_id=addon_id,
                addon_kind=AddonKind.BRANCH_QUOTA,
                quantity=quantity,
                unit_price_snapshot=0.0,
                currency_snapshot="NGN",
                benefit_snapshot={"branches": 1},
                recurring_snapshot=False,
                validity_days_snapshot=None,
                billing_cycle_snapshot=None,
                status=TenantAddonStatus.ACTIVE,
                purchased_at=now,
                completed_at=now,
                expires_at=None,
                created_by_user_id="system",
                metadata={"granted": GRANT_METADATA_MARKER},
            )
            await create_tenant_addon(payload)
            summary["grants_created"] += 1
            await _invalidate_tenant_addon_caches(tenant_id)
            await _notify_tenant(
                tenant_id, quantity=quantity, branch_count=branch_count
            )
        except Exception:
            summary["errors"] += 1
            logger.warning(
                "premium_branch_grandfather_backfill: failed for tenant=%s",
                tenant_id,
                exc_info=True,
            )

    return summary


async def check_premium_branch_cap_safety() -> list[str]:
    """Post-backfill safety net: log (never block) tenants that would be
    over-cap after the grants + flip. Expected to return an empty list.

    Resolves the addon-inclusive effective ``max_branches`` per tenant (the
    same resolution path enforcement uses) and compares it against the
    tenant's current active branch count.
    """
    from services.plan_cache_service import resolve_tenant_plan

    violators: list[str] = []
    tenant_ids = await _list_premium_tenant_ids()
    for tenant_id in tenant_ids:
        try:
            branch_count = await _count_active_branches(tenant_id)
            plan_data = await resolve_tenant_plan(tenant_id)
            max_branches: Any = (
                (plan_data.get("tenant_caps") or {}).get("max_branches")
                if plan_data
                else None
            )
            if max_branches is not None and branch_count > int(max_branches):
                violators.append(tenant_id)
        except Exception:
            logger.warning(
                "premium_branch_grandfather_backfill: safety check failed "
                "for tenant=%s",
                tenant_id,
                exc_info=True,
            )

    if violators:
        logger.error(
            "premium_branch_grandfather_backfill: %d tenant(s) exceed "
            "effective max_branches after the grandfathering migration: %s",
            len(violators),
            violators,
        )
    return violators
