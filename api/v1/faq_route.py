"""Public FAQ page endpoints.

* ``GET    /v1/faqs``            — public (no auth). Renders the FAQ
  page from the overlay + defaults.
* ``PATCH  /v1/faqs``            — application admin only. Upserts
  hero copy, items, and categories.
* ``DELETE /v1/faqs/{kind}/{key}`` — application admin only. Removes
  one overlay row. ``kind`` ∈ ``item`` | ``category``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status

from core.queue.precompute import PrecomputeScope, get_or_compute
from core.queue.write_pipeline import enqueue_write
from core.response_envelope import document_response
from schemas.faq_schema import FaqOverlayPatch, FaqOverlayRowKind
from security.account_status_check import check_admin_account_status_and_permissions
from services.faq_service import render_faqs

router = APIRouter(prefix="/faqs", tags=["FAQs"])


async def _load_template() -> dict[str, Any]:
    rendered = await render_faqs()
    return rendered.model_dump(mode="json", by_alias=True)


@router.get("")
@document_response(
    message="FAQs retrieved",
    description=(
        "Public landing-page FAQ payload. Returns hero copy, an "
        "optional HTML footer block (eg the contact-us callout), and "
        "the FAQ items grouped into sections. Cached server-side; "
        "refreshes within seconds of any PATCH."
    ),
    summary="Get FAQs (public)",
)
async def get_faqs_endpoint() -> Any:
    return await get_or_compute(
        scope_key=PrecomputeScope.GLOBAL.value,
        resource="faqs.template",
        ttl=300,
        loader=_load_template,
    )


@router.patch("")
@document_response(
    message="FAQ update queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Partial update of the FAQ overlay. Application admin only. "
        "``items`` and ``categories`` merge per item by natural key "
        "(``itemKey`` / ``categoryKey``) — matching keys upsert, new "
        "keys append, empty list clears, omit field to leave it "
        "untouched."
    ),
    summary="Patch FAQ overlay (async)",
)
async def patch_faqs_endpoint(
    payload: FaqOverlayPatch,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    body = payload.model_dump(exclude_none=True)
    body["_actor_id"] = getattr(admin, "id", None) or ""
    body["_actor_role"] = "admin"
    body["_request_id"] = getattr(request.state, "request_id", None)
    return await enqueue_write(
        writer_key="faq.update",
        payload=body,
        resource_type="faq",
        resource_id="singleton",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )


@router.delete("/{kind}/{key}")
@document_response(
    message="FAQ row delete queued",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Remove one overlay row by natural key. ``kind`` ∈ ``item`` "
        "(key = itemKey) | ``category`` (key = categoryKey). No-ops "
        "cleanly if the row is already gone."
    ),
    summary="Delete FAQ overlay row (async)",
)
async def delete_faq_row_endpoint(
    kind: FaqOverlayRowKind,
    key: str,
    request: Request,
    admin=Depends(check_admin_account_status_and_permissions),
):
    return await enqueue_write(
        writer_key="faq.delete_row",
        payload={
            "kind": kind.value,
            "key": key,
            "_actor_id": getattr(admin, "id", None) or "",
            "_actor_role": "admin",
            "_request_id": getattr(request.state, "request_id", None),
        },
        resource_type="faq",
        resource_id="singleton",
        actor_id=getattr(admin, "id", None),
        actor_role="admin",
        request_id=getattr(request.state, "request_id", None),
    )
