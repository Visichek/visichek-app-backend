"""Email outbox repository (Issue 6 / Phase B3).

Every email dispatch attempt (whether sent, queued, skipped, or
failed) writes one row to the ``email_outbox`` collection so admins
can answer "where did my email go?" without grepping server logs.

Schema is intentionally narrow — we don't try to ape a full
mail-provider audit log. Each row carries:

  - ``notification_id`` — links back to the in-app notification that
    triggered the email (when one exists).
  - ``template_key`` — which mounted template was used.
  - ``recipient_email`` — None for skips that didn't have a recipient.
  - ``status`` — ``sent`` | ``queued`` | ``skipped`` | ``failed``.
  - ``skipped_reason`` — stable enum string when status is ``skipped``.
  - ``attempts`` — SMTP retry count (matters when status is ``sent``
    or ``failed``).
  - ``user_id`` / ``user_type`` / ``tenant_id`` — pivot keys.
  - ``error`` — provider/transport error string for failed sends.
  - ``task_id`` — Celery task id when status is ``queued``.
  - ``created_at`` / ``sent_at`` — Unix epoch seconds.

The collection is append-only on the hot path. A future retention
sweep can prune rows older than 90 days; that's intentionally not
implemented here so we can observe the volume before tuning.
"""

from __future__ import annotations

from typing import Any, List, Optional

from core.database import db

COLLECTION = "email_outbox"


async def insert_email_outbox_row(payload: dict[str, Any]) -> str:
    """Insert one outbox row and return its id as a string.

    Best-effort: callers should wrap this in ``try/except`` and
    swallow on failure — the outbox is observability, not data
    integrity. We still raise on database errors so the caller can
    decide whether to log; the caller is expected to do so.
    """
    result = await db[COLLECTION].insert_one(payload)
    return str(result.inserted_id)


async def list_email_outbox_rows(
    *,
    status: Optional[str] = None,
    template_key: Optional[str] = None,
    tenant_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[dict[str, Any]]:
    """Read the most recent outbox rows for the admin diagnostics page.

    Sorted by ``created_at`` descending so the newest attempts
    surface first. ``status`` / ``template_key`` / ``tenant_id`` are
    optional filters that narrow the result set without committing
    to a particular query plan.
    """
    filter_dict: dict[str, Any] = {}
    if status:
        filter_dict["status"] = status
    if template_key:
        filter_dict["template_key"] = template_key
    if tenant_id:
        filter_dict["tenant_id"] = tenant_id

    cursor = (
        db[COLLECTION]
        .find(filter_dict)
        .sort("created_at", -1)
        .skip(max(skip, 0))
        .limit(max(min(limit, 200), 1))
    )

    rows: List[dict[str, Any]] = []
    async for raw in cursor:
        raw["_id"] = str(raw.get("_id", ""))
        rows.append(raw)
    return rows


async def count_email_outbox_rows(
    *,
    status: Optional[str] = None,
    template_key: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> int:
    """Count outbox rows matching the same filter shape as the list query."""
    filter_dict: dict[str, Any] = {}
    if status:
        filter_dict["status"] = status
    if template_key:
        filter_dict["template_key"] = template_key
    if tenant_id:
        filter_dict["tenant_id"] = tenant_id
    return await db[COLLECTION].count_documents(filter_dict)
