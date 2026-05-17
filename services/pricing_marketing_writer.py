"""Queued write handlers + precompute loader for the marketing pricing page.

Two writers are registered:

* ``pricing_marketing.update``   — apply a PATCH overlay merge.
* ``pricing_marketing.delete_row`` — remove one overlay row (plan,
  feature, or category) by its natural key.

Both invalidate ``pricing_marketing.template`` so the next GET serves
fresh data. ``services.plan_writer`` adds the same resource to every
plan-writer's ``invalidates`` list so plan edits also refresh the
marketing template — that's the bidirectional sync.
"""

from __future__ import annotations

import logging
from typing import Any

from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.pricing_marketing_schema import PricingMarketingOverlayPatch
from services.audit_service import record_audit_event
from services.pricing_marketing_service import (
    apply_overlay_patch,
    delete_overlay_row,
    get_overlay,
    render_pricing_marketing,
)

logger = logging.getLogger(__name__)


# ── Writers ──────────────────────────────────────────────────────────


@write_handler(
    "pricing_marketing.update",
    invalidates=["pricing_marketing.template"],
)
async def _pricing_marketing_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or "admin"
    request_id = data.pop("_request_id", None)

    before = await get_overlay()
    before_dump = before.model_dump(mode="json", by_alias=True) if before else None

    patch = PricingMarketingOverlayPatch(**data)
    refreshed = await apply_overlay_patch(patch)

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role,
            action="pricing_marketing.updated",
            resource_type="pricing_marketing",
            resource_id="singleton",
            details={
                "changes": {
                    "before": before_dump,
                    "after": refreshed.model_dump(mode="json", by_alias=True),
                }
            },
            request_id=request_id,
        )
    except Exception:
        logger.warning(
            "pricing_marketing.updated audit failed", exc_info=True
        )
    return {"id": refreshed.id or "singleton", "status": "updated"}


@write_handler(
    "pricing_marketing.delete_row",
    invalidates=["pricing_marketing.template"],
)
async def _pricing_marketing_delete_row(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    kind = (data.pop("kind", "") or "").strip()
    key = (data.pop("key", "") or "").strip()
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or "admin"
    request_id = data.pop("_request_id", None)

    if not kind or not key:
        raise ValueError("pricing_marketing.delete_row requires kind + key")

    refreshed = await delete_overlay_row(kind=kind, key=key)

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role,
            action="pricing_marketing.row_deleted",
            resource_type="pricing_marketing",
            resource_id="singleton",
            details={"kind": kind, "key": key},
            request_id=request_id,
        )
    except Exception:
        logger.warning(
            "pricing_marketing.row_deleted audit failed", exc_info=True
        )

    return {
        "id": refreshed.id or "singleton",
        "kind": kind,
        "key": key,
        "deleted": True,
    }


# ── Precompute loader ────────────────────────────────────────────────


@register_precompute("pricing_marketing.template", scope=PrecomputeScope.GLOBAL)
async def _precompute_pricing_marketing(_scope_id: str) -> dict[str, Any]:
    rendered = await render_pricing_marketing()
    return rendered.model_dump(mode="json", by_alias=True)
