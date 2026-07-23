"""Task 9: one-shot migration flipping the STORED premium plan's max_branches.

``config.plan_tiers.PREMIUM_PLAN.tenant_caps.max_branches`` is the canonical
source of truth going forward, but flipping it alone does NOT change the
plan document already persisted in Mongo for existing installs:
``services.plan_bootstrap.ensure_canonical_plans`` refreshes existing
canonical plans via ``_canonical_to_plan_update``, which deliberately
EXCLUDES ``tenant_caps`` from the ``$set`` payload — that exclusion exists so
an admin's hand-tuned cap (e.g. a bumped Free ``max_visitors_per_month``)
survives a redeploy. Premium's ``max_branches`` flip needs the opposite: it
MUST land in the stored doc. Hence this dedicated, narrowly-scoped one-shot
migration instead of touching the general bootstrap refresh rule.

Ordering (enforced by ``main.py`` startup, not by this module): this MUST
run AFTER ``services.premium_branch_grandfather_backfill`` grants existing
Premium tenants their perpetual per-branch entitlements — otherwise a
tenant could observe the tightened cap before their grandfathered grant
exists.

Idempotent: no-op once the stored value already matches the canonical
config's target.
"""

from __future__ import annotations

import logging
import time

from config.plan_tiers import PREMIUM_PLAN, PREMIUM_PLAN_NAME
from core.database import db
from services.plan_cache_service import invalidate_plan_cache

logger = logging.getLogger(__name__)

PLAN_COLLECTION = "plans"


async def flip_stored_premium_max_branches() -> bool:
    """Set the stored premium plan doc's ``tenant_caps.max_branches`` to the
    canonical target. Returns True if a write happened, False if already
    up to date (or the plan doc doesn't exist yet, e.g. first-ever boot —
    ``ensure_canonical_plans`` creates it WITH the correct value already,
    so there is nothing to fix in that case)."""
    target = PREMIUM_PLAN.tenant_caps.max_branches

    plan_doc = await db[PLAN_COLLECTION].find_one({"name": PREMIUM_PLAN_NAME})
    if plan_doc is None:
        logger.info(
            "premium_max_branches_flip: no stored premium plan doc yet — "
            "nothing to migrate (fresh install will be created correctly)"
        )
        return False

    current = (plan_doc.get("tenant_caps") or {}).get("max_branches")
    if current == target:
        return False

    try:
        await db[PLAN_COLLECTION].update_one(
            {"_id": plan_doc["_id"]},
            {
                "$set": {
                    "tenant_caps.max_branches": target,
                    "last_updated": int(time.time()),
                }
            },
        )
    except Exception:
        logger.exception(
            "premium_max_branches_flip: failed to update stored premium plan doc"
        )
        return False

    try:
        await invalidate_plan_cache(str(plan_doc["_id"]))
    except Exception:
        logger.warning(
            "premium_max_branches_flip: plan cache invalidation failed",
            exc_info=True,
        )

    logger.info(
        "premium_max_branches_flip: stored premium plan max_branches %s -> %s",
        current,
        target,
    )
    return True
