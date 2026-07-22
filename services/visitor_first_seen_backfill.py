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

Idempotent and safe to re-run on every boot: ``record_first_seen`` is itself
insert-if-absent, backed by the unique compound index, so a second run over
the same data is a no-op even without any marker.

High-water mark: a ``backfill_markers`` document (``_id="visitor_first_seen"``)
records the max source timestamp (``check_in_time``/``date_created``)
processed by the last run. Every boot after the first only scans source rows
newer than that mark, so once the historical backlog is cleared, subsequent
boots do effectively zero work instead of re-scanning the tenant's entire
``visit_sessions``/``checkins`` history on every restart. The mark is purely
an optimization, not a correctness gate — per-row idempotency (insert-if-
absent via the unique index) is what actually prevents duplicates, so a
stale or reset mark just means some already-processed rows get re-scanned
and no-op through ``record_first_seen``, never a wrong result.

Note on memory: the earliest-per-key map IS still materialized in memory for
the span being scanned (cursors are ``batch_size``-paged for network/DB
efficiency, not to bound memory) — but because the high-water mark keeps
that span to "whatever was created since the last boot" after the first
catch-up run, this stays small in steady state. The one large scan is the
initial catch-up over pre-deploy history.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from core.database import db
from repositories.visitor_branch_first_repo import record_first_seen

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500

MARKERS_COLLECTION = "backfill_markers"
_MARKER_ID = "visitor_first_seen"


async def _get_high_water_mark() -> int:
    """Return the max source timestamp processed by the previous run (0 if none)."""
    try:
        doc = await db[MARKERS_COLLECTION].find_one({"_id": _MARKER_ID})
        return int(doc.get("high_water_mark", 0)) if doc else 0
    except Exception:
        logger.warning(
            "visitor_first_seen_backfill: failed to read high-water mark — "
            "falling back to a full scan",
            exc_info=True,
        )
        return 0


async def _set_high_water_mark(ts: int) -> None:
    try:
        await db[MARKERS_COLLECTION].update_one(
            {"_id": _MARKER_ID},
            {"$set": {"high_water_mark": ts, "last_run_at": int(time.time())}},
            upsert=True,
        )
    except Exception:
        logger.warning(
            "visitor_first_seen_backfill: failed to persist high-water mark "
            "(next boot will re-scan from the previous mark)",
            exc_info=True,
        )


async def _warn_if_unique_index_missing() -> None:
    """Log loudly if the ``visitor_branch_firsts`` unique index is absent.

    ``record_first_seen`` relies on a ``DuplicateKeyError`` from this index
    to detect returning visitors — without it, this backfill (and every
    live check-in) can silently insert duplicate ledger rows.
    """
    try:
        indexes = await db.visitor_branch_firsts.index_information()
        has_unique = any(
            spec.get("unique") for spec in indexes.values() if isinstance(spec, dict)
        )
        if not has_unique:
            logger.warning(
                "visitor_first_seen_backfill: STARTING WITHOUT the "
                "visitor_branch_firsts unique compound index "
                "(tenant_id, branch_id, visitor_profile_id) — "
                "record_first_seen dedup relies on this index; duplicate "
                "ledger rows are possible until core.indexes.ensure_indexes runs."
            )
    except Exception:
        logger.warning(
            "visitor_first_seen_backfill: could not verify visitor_branch_firsts "
            "unique index presence",
            exc_info=True,
        )


async def _earliest_from_visit_sessions(
    since_ts: int = 0,
) -> dict[tuple[str, str, str], int]:
    """Earliest ``check_in_time`` per (tenant_id, branch_id, visitor_profile_id).

    ``since_ts`` restricts the scan to rows whose ``check_in_time`` OR
    ``date_created`` is >= the high-water mark, so a catch-up run after the
    first boot only re-reads what changed since the previous run.
    """
    earliest: dict[tuple[str, str, str], int] = {}
    query: dict[str, Any] = {
        "tenant_id": {"$exists": True, "$ne": None},
        "branch_id": {"$exists": True, "$ne": None},
        "visitor_profile_id": {"$exists": True, "$ne": None},
    }
    if since_ts:
        query["$or"] = [
            {"check_in_time": {"$gte": since_ts}},
            {"date_created": {"$gte": since_ts}},
        ]
    cursor = db.visit_sessions.find(
        query,
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
    since_ts: int = 0,
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

    query: dict[str, Any] = {
        "tenant_id": {"$exists": True, "$ne": None},
        "branch_id": {"$exists": True, "$ne": None},
        "visitor_id": {"$exists": True, "$ne": None},
    }
    if since_ts:
        query["date_created"] = {"$gte": since_ts}

    cursor = db.checkins.find(
        query,
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


async def backfill_visitor_first_seen(
    since_ts: Optional[int] = None,
) -> dict[str, int]:
    """Derive and insert missing ``visitor_branch_firsts`` rows.

    Scoped to source rows newer than the stored high-water mark (see module
    docstring) unless ``since_ts`` is explicitly passed (mainly for tests /
    a manual forced re-scan). On success, advances the mark to the max
    source timestamp seen this run so the NEXT boot only scans what's new.

    Returns a summary dict for startup logging.
    """
    summary = {"candidates": 0, "inserted": 0, "already_present": 0, "errors": 0}

    await _warn_if_unique_index_missing()

    effective_since = await _get_high_water_mark() if since_ts is None else since_ts

    earliest = await _earliest_from_visit_sessions(effective_since)
    await _earliest_from_checkins(earliest, effective_since)

    summary["candidates"] = len(earliest)

    max_ts_seen = effective_since
    for (tenant_id, branch_id, visitor_profile_id), ts in earliest.items():
        if ts > max_ts_seen:
            max_ts_seen = ts
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

    if max_ts_seen > effective_since:
        await _set_high_water_mark(max_ts_seen)

    return summary
