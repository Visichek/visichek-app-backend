from __future__ import annotations

import logging
from typing import Optional

from pymongo.errors import DuplicateKeyError

from core.database import db

logger = logging.getLogger(__name__)

COLLECTION = "visitor_branch_firsts"


async def record_first_seen(
    tenant_id: str,
    branch_id: str,
    visitor_profile_id: str,
    now: int,
) -> bool:
    """Insert the first-seen ledger row for (tenant, branch, visitor_profile).

    Returns ``True`` when this call actually inserted the row — i.e. the
    visitor is NEW to this branch this ledger has ever seen. Returns
    ``False`` when the row already existed (a returning visitor at this
    branch), backed by the unique compound index so concurrent check-ins
    for the same visitor/branch never double-count.
    """
    try:
        await db[COLLECTION].insert_one(
            {
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "visitor_profile_id": visitor_profile_id,
                "first_seen_at": now,
            }
        )
        return True
    except DuplicateKeyError:
        return False
    except Exception:
        logger.warning(
            "visitor_branch_first_repo: record_first_seen failed tenant=%s branch=%s "
            "visitor_profile=%s",
            tenant_id,
            branch_id,
            visitor_profile_id,
            exc_info=True,
        )
        return False


async def has_first_seen(
    tenant_id: str,
    branch_id: str,
    visitor_profile_id: str,
) -> bool:
    """Peek (no insert) whether a ledger row already exists for this
    (tenant, branch, visitor_profile) triple — i.e. whether this visitor is
    a RETURNING visitor at this branch. Used by cap enforcement to
    determine ``is_new_visitor`` before deciding whether to insert the
    check-in/session at all (enforcement must run before the ledger insert,
    not after).
    """
    doc = await db[COLLECTION].find_one(
        {
            "tenant_id": tenant_id,
            "branch_id": branch_id,
            "visitor_profile_id": visitor_profile_id,
        },
        {"_id": 1},
    )
    return doc is not None


async def count_new_for_month(
    tenant_id: str,
    month_start: int,
    month_end: int,
    branch_id: Optional[str] = None,
) -> int:
    """Count ledger rows (i.e. NEW visitors) first seen within the given month.

    Scoped tenant-wide when ``branch_id`` is omitted, or to a single branch
    when supplied.
    """
    query: dict = {
        "tenant_id": tenant_id,
        "first_seen_at": {"$gte": month_start, "$lt": month_end},
    }
    if branch_id:
        query["branch_id"] = branch_id
    return await db[COLLECTION].count_documents(query)


async def counts_by_branch_for_month(
    tenant_id: str,
    month_start: int,
    month_end: int,
) -> dict[str, int]:
    """Return {branch_id: new_visitor_count} for the given month."""
    pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "first_seen_at": {"$gte": month_start, "$lt": month_end},
            }
        },
        {"$group": {"_id": "$branch_id", "count": {"$sum": 1}}},
    ]
    result: dict[str, int] = {}
    async for row in db[COLLECTION].aggregate(pipeline):
        branch_id = row.get("_id")
        if branch_id:
            result[str(branch_id)] = int(row.get("count", 0))
    return result
