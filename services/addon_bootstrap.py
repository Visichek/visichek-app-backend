"""Idempotent add-on catalog seed.

Run once on app startup, alongside ``services.plan_bootstrap``, to
upsert the singleton catalog add-ons that ship with the platform
(currently just the derived-price "additional-branch" quota add-on
introduced by WS2). Follows the same preserve-admin-edits pattern as
``plan_bootstrap._canonical_to_plan_update``: on refresh we never
clobber the admin-editable ``description`` field, only the mechanical
fields (kind, benefit, pricing, cycle) that must stay in sync with
code.
"""

from __future__ import annotations

import logging
from typing import Any

from repositories.addon_repo import create_addon, get_addon, update_addon
from schemas.addon_schema import (
    AddonCreate,
    AddonKind,
    AddonStatus,
    AddonUpdate,
)
from services.addon_service import resolve_derived_addon_price

logger = logging.getLogger(__name__)


# Singleton catalog add-ons, keyed by their stable ``slug``.
DEFAULT_ADDON_CATALOG: dict[str, dict[str, Any]] = {
    "additional-branch": {
        "name": "Additional Branch",
        "description": (
            "Each additional branch includes its own 1,000 new visitors per month."
        ),
        "kind": AddonKind.BRANCH_QUOTA,
        "status": AddonStatus.ACTIVE,
        "currency": "NGN",
        "benefit_per_unit": {"branches": 1},
        "pricing_mode": "derived",
        "derived_from": {
            "plan": "premium",
            "field": "base_price_monthly",
            "multiplier": 0.8,
        },
        "recurring": True,
        "billing_cycle": "monthly",
        "validity_days": None,
        "max_units_per_purchase": None,
    },
}


async def ensure_addon_catalog() -> dict[str, str]:
    """Upsert every entry in ``DEFAULT_ADDON_CATALOG`` by slug. Idempotent.

    Returns ``{slug: addon_id}`` for the seeded rows.
    """
    slug_to_id: dict[str, str] = {}

    for slug, spec in DEFAULT_ADDON_CATALOG.items():
        existing = await get_addon({"slug": slug})

        if existing is None:
            initial_price = 0.0
            derived_from = spec.get("derived_from")
            if spec.get("pricing_mode") == "derived" and derived_from:
                resolved = await resolve_derived_addon_price(derived_from)
                if resolved is not None:
                    initial_price = resolved
            try:
                created = await create_addon(
                    AddonCreate(slug=slug, unit_price=initial_price, **spec)
                )
                if created.id:
                    slug_to_id[slug] = created.id
                    logger.info(
                        "addon_bootstrap: created catalog addon %s id=%s",
                        slug,
                        created.id,
                    )
            except Exception:
                logger.exception("addon_bootstrap: failed to create %s", slug)
            continue

        if not existing.id:
            continue
        slug_to_id[slug] = existing.id

        try:
            resolved_price = existing.unit_price
            derived_from = spec.get("derived_from")
            if spec.get("pricing_mode") == "derived" and derived_from:
                live = await resolve_derived_addon_price(derived_from)
                if live is not None:
                    resolved_price = live

            # Deliberately EXCLUDE ``description`` from the refresh update
            # so an admin's edited copy survives redeploys — mirrors
            # plan_bootstrap's tenant_caps exclusion rule.
            update = AddonUpdate(
                status=spec.get("status"),
                currency=spec.get("currency"),
                unit_price=resolved_price,
                pricing_mode=spec.get("pricing_mode"),
                derived_from=derived_from,
                recurring=spec.get("recurring"),
                billing_cycle=spec.get("billing_cycle"),
                benefit_per_unit=spec.get("benefit_per_unit"),
                validity_days=spec.get("validity_days"),
                max_units_per_purchase=spec.get("max_units_per_purchase"),
            )
            update_dict = update.model_dump(exclude_unset=True)
            if update_dict:
                await update_addon(existing.id, update)
                logger.info(
                    "addon_bootstrap: refreshed catalog addon %s id=%s",
                    slug,
                    existing.id,
                )
        except Exception:
            logger.exception("addon_bootstrap: failed to refresh %s", slug)

    return slug_to_id
