"""Queued write handlers + precompute loader for the FAQ overlay."""

from __future__ import annotations

import logging
from typing import Any

from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.faq_schema import FaqOverlayPatch
from services.audit_service import record_audit_event
from services.faq_service import (
    apply_overlay_patch,
    delete_overlay_row,
    get_overlay,
    render_faqs,
)

logger = logging.getLogger(__name__)


@write_handler("faq.update", invalidates=["faqs.template"])
async def _faq_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or "admin"
    request_id = data.pop("_request_id", None)

    before = await get_overlay()
    before_dump = before.model_dump(mode="json", by_alias=True) if before else None

    patch = FaqOverlayPatch(**data)
    refreshed = await apply_overlay_patch(patch)

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role,
            action="faq.updated",
            resource_type="faq",
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
        logger.warning("faq.updated audit failed", exc_info=True)
    return {"id": refreshed.id or "singleton", "status": "updated"}


@write_handler("faq.delete_row", invalidates=["faqs.template"])
async def _faq_delete_row(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    kind = (data.pop("kind", "") or "").strip()
    key = (data.pop("key", "") or "").strip()
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or "admin"
    request_id = data.pop("_request_id", None)

    if not kind or not key:
        raise ValueError("faq.delete_row requires kind + key")

    refreshed = await delete_overlay_row(kind=kind, key=key)

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role,
            action="faq.row_deleted",
            resource_type="faq",
            resource_id="singleton",
            details={"kind": kind, "key": key},
            request_id=request_id,
        )
    except Exception:
        logger.warning("faq.row_deleted audit failed", exc_info=True)

    return {
        "id": refreshed.id or "singleton",
        "kind": kind,
        "key": key,
        "deleted": True,
    }


@register_precompute("faqs.template", scope=PrecomputeScope.GLOBAL)
async def _precompute_faqs(_scope_id: str) -> dict[str, Any]:
    rendered = await render_faqs()
    return rendered.model_dump(mode="json", by_alias=True)
