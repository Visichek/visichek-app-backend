"""One-shot new-visitor first-seen ledger backfill (WS0.3).

The ``visitor_branch_firsts`` collection (see
``repositories/visitor_branch_first_repo.py``) only gets rows going forward
from the live check-in insertion points. Historical visits — everything
recorded before this feature shipped — never populated it. This backfill
derives, for every ``visitor_profile``, the earliest visit per
``(tenant_id, branch_id)`` from both ``visit_sessions`` (by
``check_in_time``) and ``checkins`` (by ``date_created`` — checkins have no
``check_in_time`` field), and inserts a ledger row if one doesn't already
exist for that key.

Idempotent and safe to re-run on every boot (no done-marker, per repo
convention): ``record_first_seen`` is itself insert-if-absent, backed by the
unique compound index, so a second run over the same data is a no-op.
Cursor-batched to avoid materialising a huge in-memory map on large tenants.
"""

from __future__ import annotations

import logging
from typing import Any

from core.database import db
from repositories.visitor_branch_first_repo import record_first_seen

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


async def _earliest_from_visit_sessions() -> dict[tuple[str, str, str], int]:
    """Earliest ``check_in_time`` per (tenant_id, branch_id, visitor_profile_id)."""
    earliest: dict[tuple[str, str, str], int] = {}
    cursor = db.visit_sessions.find(
        {
            "tenant_id": {"$exists": True, "$ne": None},
            "branch_id": {"$exists": True, "$ne": None},
            "visitor_profile_id": {"$exists": True, "$ne": None},
        },
        {
            "tenant_id": 1,
            "branch_id": 1,
            "visitor_profile_id": 1,
            "check_in_time": 1,
            "date_created": 1,
        },
    ).batch_size(_BATCH_SIZE)

    async for doc in cursor:
        tenant_id = doc.get("tenant_id")
        branch_id = doc.get("branch_id")
        visitor_profile_id = doc.get("visitor_profile_id")
        if not tenant_id or not branch_id or not visitor_profile_id:
            continue
        ts = doc.get("check_in_time") or doc.get("date_created")
        if not ts:
            continue
        key = (str(tenant_id), str(branch_id), str(visitor_profile_id))
        if key not in earliest or ts < earliest[key]:
            earliest[key] = int(ts)
    return earliest


async def _earliest_from_checkins(
    earliest: dict[tuple[str, str, str], int],
) -> None:
    """Merge earliest ``date_created`` per key from ``checkins`` into ``earliest``.

    ``checkins`` rows reference ``visitor_id`` (the ``visitors`` collection),
    not ``visitor_profile_id`` directly, so we resolve each distinct visitor
    to its profile via phone/email — the same identity keys the checkin
    submit flow itself upserts on. Visitors with no resolvable profile are
    skipped (nothing to backfill for them).
    """
    from repositories.visitor_profile_repo import (
        get_visitor_profile_by_email,
        get_visitor_profile_by_phone,
    )

    visitor_cache: dict[str, str | None] = {}

    cursor = db.checkins.find(
        {
            "tenant_id": {"$exists": True, "$ne": None},
            "branch_id": {"$exists": True, "$ne": None},
            "visitor_id": {"$exists": True, "$ne": None},
        },
        {
            "tenant_id": 1,
            "branch_id": 1,
            "visitor_id": 1,
            "date_created": 1,
        },
    ).batch_size(_BATCH_SIZE)

    async for doc in cursor:
        tenant_id = doc.get("tenant_id")
        branch_id = doc.get("branch_id")
        visitor_id = doc.get("visitor_id")
        ts = doc.get("date_created")
        if not tenant_id or not branch_id or not visitor_id or not ts:
            continue

        cache_key = f"{tenant_id}:{visitor_id}"
        if cache_key not in visitor_cache:
            profile_id: str | None = None
            try:
                visitor_doc = await db.visitors.find_one({"_id": _to_object_id(visitor_id)})
                if visitor_doc:
                    phone = visitor_doc.get("phone")
                    email = visitor_doc.get("email")
                    profile = None
                    if phone:
                        profile = await get_visitor_profile_by_phone(
                            tenant_id=str(tenant_id), phone=phone
                        )
                    if profile is None and email:
                        profile = await get_visitor_profile_by_email(
                            tenant_id=str(tenant_id), email=email
                        )
                    if profile is not None:
                        profile_id = profile.id
            except Exception:
                logger.debug(
                    "visitor_first_seen_backfill: profile resolution failed "
                    "for visitor_id=%s",
                    visitor_id,
                    exc_info=True,
                )
            visitor_cache[cache_key] = profile_id

        profile_id = visitor_cache[cache_key]
        if not profile_id:
            continue

        key = (str(tenant_id), str(branch_id), str(profile_id))
        ts = int(ts)
        if key not in earliest or ts < earliest[key]:
            earliest[key] = ts


def _to_object_id(value: Any) -> Any:
    from bson import ObjectId

    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return value


async def backfill_visitor_first_seen() -> dict[str, int]:
    """Derive and insert missing ``visitor_branch_firsts`` rows.

    Returns a summary dict for startup logging.
    """
    summary = {"candidates": 0, "inserted": 0, "already_present": 0, "errors": 0}

    earliest = await _earliest_from_visit_sessions()
    await _earliest_from_checkins(earliest)

    summary["candidates"] = len(earliest)

    for (tenant_id, branch_id, visitor_profile_id), ts in earliest.items():
        try:
            inserted = await record_first_seen(
                tenant_id, branch_id, visitor_profile_id, ts
            )
            if inserted:
                summary["inserted"] += 1
            else:
                summary["already_present"] += 1
        except Exception:
            summary["errors"] += 1
            logger.warning(
                "visitor_first_seen_backfill: failed for tenant=%s branch=%s "
                "visitor_profile=%s",
                tenant_id,
                branch_id,
                visitor_profile_id,
                exc_info=True,
            )

    return summary
