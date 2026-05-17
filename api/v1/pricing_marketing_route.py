"""Pricing-marketing page endpoints.

* ``GET  /v1/pricing-marketing`` — public (no auth). Returns the fully
  rendered pricing page: summary cards + grouped comparison tables.
  Served from the global precompute cache; refreshed automatically on
  any plan write and on every overlay PATCH.

* ``PATCH /v1/pricing-marketing`` — application admin only. Upserts
  marketing copy (headlines, plan taglines, CTAs, comparison-row
  labels, category names). Lists merge by natural key (``plan_name`` /
  ``row_key`` / ``category_key``).

* ``DELETE /v1/pricing-marketing/{kind}/{key}`` — application admin
  only. Removes one overlay row. ``kind`` ∈ ``plan`` | ``feature`` |
  ``category``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.pricing_marketing_schema import (
    PricingMarketingOverlayPatch,
    PricingMarketingOverlayRowKind,
)
from security.account_status_check import check_admin_account_status_and_permissions
from services.pricing_marketing_service import render_pricing_marketing

router = APIRouter(prefix="/pricing-marketing", tags=["Pricing Marketing"])


async def _load_template() -> dict[str, Any]:
    rendered = await render_pricing_marketing()
    return rendered.model_dump(mode="json", by_alias=True)


@router.get("")
@document_response(
    message="Pricing marketing template retrieved",
    description=(
        "Public landing-page payload. Renders one card per active+public "
        "plan (Enterprise collapsed to a single 'Contact sales' card) "
        "plus a per-category comparison table generated from each plan's "
        "feature_rules, tenant_caps, storage_limits, CRUD/retrieval "
        "quotas, support tier, SLA, trial days, and the togglable-"
        "feature catalog. Marketing copy (taglines, CTAs, row labels, "
        "category names) layers on top from the saved overlay."
    ),
    summary="Get pricing-marketing template (public)",
)
async def get_pricing_marketing_endpoint() -> Any:
    return await get_or_compute(
        scope_key=PrecomputeScope.GLOBAL.value,
        resource="pricing_marketing.template",
        ttl=300,
        loader=_load_template,
    )


@router.patch("")
@document_response(
    message="Pricing marketing update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Partial update of the marketing overlay. Application admin "
        "only. Top-level fields (headline, subheadline, currency_display) "
        "are upserted; the list fields (plans, features, categories) "
        "merge per item by their natural key — pass the existing key to "
        "update one row, a new key to add a row, or an empty list to "
        "clear the section. Omit the field to leave it untouched."
    ),
    summary="Patch pricing-marketing overlay (async)",
)
async def patch_pricing_marketing_endpoint(
    payload: PricingMarketingOverlayPatch,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    body = payload.model_dump(exclude_none=True)
    body["_actor_id"] = getattr(admin, "id", None) or ""
    body["_actor_role"] = "admin"
    body["_request_id"] = getattr(request.state, "request_id", None)
    return await enqueue_write(
        writer_key="pricing_marketing.update",
        payload=body,
        resource_type="pricing_marketing",
        resource_id="singleton",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{kind}/{key}")
@document_response(
    message="Pricing marketing row delete queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Remove one overlay row by natural key. ``kind`` is one of "
        "``plan`` (key = plan_name), ``feature`` (key = row_key), or "
        "``category`` (key = category_key). No-ops cleanly if the row "
        "isn't present. Use this to clear stale copy for a plan or "
        "feature that's been retired."
    ),
    summary="Delete pricing-marketing overlay row (async)",
)
async def delete_pricing_marketing_row_endpoint(
    kind: PricingMarketingOverlayRowKind,
    key: str,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="pricing_marketing.delete_row",
        payload={
            "kind": kind.value,
            "key": key,
            "_actor_id": getattr(admin, "id", None) or "",
            "_actor_role": "admin",
            "_request_id": getattr(request.state, "request_id", None),
        },
        resource_type="pricing_marketing",
        resource_id="singleton",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )
