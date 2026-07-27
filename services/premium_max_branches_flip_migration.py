"""One-shot migration forcing an allowlist of caps onto the STORED premium
plan doc (started life as the Task 9 ``max_branches`` flip; generalised to
also cover ``visitors_per_branch_per_month``).

Canonical ``config.plan_tiers.PREMIUM_PLAN.tenant_caps`` values are the
source of truth going forward, but changing them alone does NOT change the
plan document already persisted in Mongo for existing installs:
``services.plan_bootstrap.ensure_canonical_plans`` refreshes existing
canonical plans via ``_canonical_to_plan_update``, which deliberately
EXCLUDES ``tenant_caps`` from the ``$set`` payload — that exclusion exists so
an admin's hand-tuned cap (e.g. a bumped Free ``max_visitors_per_month``)
survives a redeploy. The keys in ``_FORCED_CAP_KEYS`` need the opposite:
they MUST land in the stored doc. Hence this dedicated, narrowly-scoped
one-shot migration instead of touching the general bootstrap refresh rule.

Ordering (enforced by ``main.py`` startup, not by this module): this MUST
run AFTER ``services.premium_branch_grandfather_backfill`` grants existing
Premium tenants their perpetual per-branch entitlements — otherwise a
tenant could observe the tightened ``max_branches`` cap before their
grandfathered grant exists.

Idempotent: no-op once the stored values already match the canonical
config's targets.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from config.plan_tiers import PREMIUM_PLAN, PREMIUM_PLAN_NAME
from core.database import db
from services.plan_cache_service import invalidate_plan_cache

logger = logging.getLogger(__name__)

PLAN_COLLECTION = "plans"


# Cap keys on the STORED premium plan doc that must track canonical config
# rather than an admin's tuned value.
#
# * ``max_branches`` — the Task 9 per-location pricing flip.
# * ``visitors_per_branch_per_month`` — the per-location visitor allowance.
#   ``services.plan_limits.enforce_branch_visitor_cap`` only takes its
#   per-branch branch when this key is PRESENT; when it is missing the
#   function silently falls through to the tenant-wide
#   ``max_visitors_per_month``, so a stored doc predating this field
#   enforces 500/month tenant-wide instead of 1000/month per branch.
_FORCED_CAP_KEYS: tuple[str, ...] = (
    "max_branches",
    "visitors_per_branch_per_month",
)


async def sync_stored_premium_caps(
    keys: tuple[str, ...] = _FORCED_CAP_KEYS,
) -> dict[str, tuple[Any, Any]]:
    """Force the given cap ``keys`` on the stored premium plan doc to their
    canonical values.

    Returns ``{cap_key: (old_value, new_value)}`` for every key changed.
    Empty dict means already in sync (or the plan doc does not exist yet —
    a fresh install's ``ensure_canonical_plans`` creates it correctly).
    Idempotent.
    """
    canonical_caps = PREMIUM_PLAN.tenant_caps.model_dump()

    plan_doc = await db[PLAN_COLLECTION].find_one({"name": PREMIUM_PLAN_NAME})
    if plan_doc is None:
        logger.info(
            "sync_stored_premium_caps: no stored premium plan doc yet — "
            "nothing to migrate (fresh install will be created correctly)"
        )
        return {}

    stored_caps = plan_doc.get("tenant_caps") or {}
    changed: dict[str, tuple[Any, Any]] = {}
    set_payload: dict[str, Any] = {}

    for key in keys:
        target = canonical_caps.get(key)
        current = stored_caps.get(key)
        if current == target:
            continue
        changed[key] = (current, target)
        set_payload[f"tenant_caps.{key}"] = target

    if not set_payload:
        return {}

    set_payload["last_updated"] = int(time.time())

    try:
        await db[PLAN_COLLECTION].update_one(
            {"_id": plan_doc["_id"]}, {"$set": set_payload}
        )
    except Exception:
        logger.exception(
            "sync_stored_premium_caps: failed to update stored premium plan doc"
        )
        return {}

    try:
        await invalidate_plan_cache(str(plan_doc["_id"]))
    except Exception:
        logger.warning(
            "sync_stored_premium_caps: plan cache invalidation failed", exc_info=True
        )

    logger.info("sync_stored_premium_caps: applied %s", changed)
    return changed


async def flip_stored_premium_max_branches() -> bool:
    """Set the stored premium plan doc's ``tenant_caps.max_branches`` to the
    canonical target. Returns True if a write happened, False if already
    up to date (or the plan doc doesn't exist yet, e.g. first-ever boot —
    ``ensure_canonical_plans`` creates it WITH the correct value already,
    so there is nothing to fix in that case).

    Thin wrapper retained for backward compatibility with existing callers
    and tests — delegates to ``sync_stored_premium_caps`` scoped to just
    ``max_branches`` so its behaviour (including idempotency against docs
    that predate other forced keys) is unchanged.
    """
    changed = await sync_stored_premium_caps(keys=("max_branches",))
    return "max_branches" in changed
