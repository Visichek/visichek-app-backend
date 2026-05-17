"""One-shot backfill for mis-typed session rows.

Background
==========
The first version of the session-recording fix (see ``admin-endpoints.txt``
sessions section) stored ``user_type`` by stringifying the principal's
``role`` field. For tenant users the role is a ``SystemUserRole`` enum,
and ``str(SystemUserRole.SUPER_ADMIN)`` returns ``"SystemUserRole.SUPER_ADMIN"``
rather than the underlying value ``"super_admin"``. That formatted name
never matches ``TENANT_USER_ROLES``, so the recorder defaulted those
sessions to ``user_type="admin"``.

Effect on production: every tenant-user session created between the
sessions fix and this backfill was written with ``user_type="admin"``,
and ``GET /v1/system-users/sessions`` (which filters by
``user_type="system_user"``) therefore returned an empty list.

What this fixes
===============
- Looks up every session row that carries ``user_type="admin"`` but whose
  ``user_id`` exists in ``system_users`` and is missing from ``admins``.
- Rewrites the ``user_type`` to ``"system_user"`` so the existing list
  endpoint picks the row up immediately.
- Idempotent: re-running the backfill on a clean DB matches zero rows.
- Safe: rows that genuinely belong to admins (their id is present in
  ``admins``) are NOT touched, even if the user_id ALSO appears in
  ``system_users`` (defensive against mis-shared ids).

Hooked into ``main.lifespan`` alongside the other one-shot backfills.
"""

from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId

from core.database import db

logger = logging.getLogger(__name__)


async def _ids_present_in(collection: str, candidate_ids: list[str]) -> set[str]:
    """Return the subset of ``candidate_ids`` that exists in ``collection``.

    Inputs that are not valid ObjectIds are silently dropped — sessions
    occasionally carry hex sentinel ids (e.g. the env primary admin's
    ``656f7ac12b9d4f6c9e2b9f7d``) that aren't real Mongo rows; we don't
    rewrite those.
    """
    object_ids: list[ObjectId] = []
    for raw in candidate_ids:
        if isinstance(raw, str) and ObjectId.is_valid(raw):
            object_ids.append(ObjectId(raw))
    if not object_ids:
        return set()
    found: set[str] = set()
    cursor = db[collection].find({"_id": {"$in": object_ids}}, {"_id": 1})
    async for doc in cursor:
        found.add(str(doc["_id"]))
    return found


async def backfill_session_user_types() -> dict[str, Any]:
    """Repoint ``user_type="admin"`` rows that actually belong to system users.

    Returns a tiny summary dict suitable for logging. Errors are caught
    and reported on the summary instead of being raised — a failed
    backfill must never block app startup.
    """
    summary: dict[str, Any] = {
        "scanned": 0,
        "candidates": 0,
        "rewritten": 0,
    }
    try:
        candidate_ids: list[str] = []
        cursor = db.sessions.find({"user_type": "admin"}, {"user_id": 1})
        async for doc in cursor:
            summary["scanned"] += 1
            uid = doc.get("user_id")
            if uid:
                candidate_ids.append(str(uid))

        unique_ids = sorted(set(candidate_ids))
        summary["candidates"] = len(unique_ids)
        if not unique_ids:
            return summary

        ids_in_admins = await _ids_present_in("admins", unique_ids)
        ids_in_system_users = await _ids_present_in("system_users", unique_ids)
        # Only rewrite ids that are NOT present in admins (so a real admin
        # row always wins) AND that ARE present in system_users (so we
        # don't accidentally rewrite a session for a deleted account into
        # a tenant-typed row).
        to_rewrite = sorted(ids_in_system_users - ids_in_admins)
        if not to_rewrite:
            return summary

        result = await db.sessions.update_many(
            {"user_type": "admin", "user_id": {"$in": to_rewrite}},
            {"$set": {"user_type": "system_user"}},
        )
        summary["rewritten"] = int(getattr(result, "modified_count", 0) or 0)
    except Exception as exc:
        summary["error"] = repr(exc)
        logger.warning("session_user_type backfill failed", exc_info=True)
    return summary
