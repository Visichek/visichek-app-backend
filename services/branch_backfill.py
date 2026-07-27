"""One-shot branch-assignment backfill.

Run once on startup (idempotent): every tenant that does not yet have a
branch gets a Headquarters branch provisioned, and every system_user whose
``branch_ids`` list is empty / missing is assigned the tenant's
headquarters branch.

This closes the gap left by historical tenants that bootstrapped before
the branch-isolation feature was rolled out (none of them have a branch,
and none of their system_users carry a ``branch_ids`` list).

The function is designed to be safe to call repeatedly:

  * ``ensure_default_branch`` is itself idempotent (no-op when a branch
    exists) — it always returns a branch.
  * The user update only runs for rows where ``branch_ids`` is missing or
    empty, so users that already have assignments are never touched.
"""

from __future__ import annotations

import logging
from typing import Any

from core.database import db
from services.branch_service import ensure_default_branch

logger = logging.getLogger(__name__)


# Collections whose rows carry a ``branch_id``. Legacy rows created before
# branch separation have no branch_id; tag them to HQ so they are not
# orphaned into an unbucketed group.
#
# For ``expected_appointments``, ``visit_sessions`` and ``checkins``, reads
# do a strict ``branch_id $in branch_ids`` match — see
# services.branch_service.branch_scope_filter — so tagging these is what
# makes the rows visible to HQ-assigned branch-scoped users at all.
#
# ``departments`` and ``incident_logs`` are NOT read through that filter —
# access to them is not branch-isolated. There the tag serves per-branch
# cap-bucketing (departments' max_departments cap) and attribution/
# filtering (incident branchId filter) only, not read isolation.
_branch_scoped_collections = (
    "expected_appointments",
    "visit_sessions",
    "checkins",
    "incident_logs",
    "departments",
)


async def backfill_branch_assignments() -> dict[str, int]:
    """Backfill HQ branches and ``branch_ids`` on every existing system_user.

    Returns a dict summary so the caller (``main.py`` lifespan) can log it.
    """
    summary: dict[str, int] = {
        "tenants_seen": 0,
        "tenants_with_new_branch": 0,
        "users_updated": 0,
        "records_tagged_hq": 0,
    }

    cursor = db.tenant_companies.find({})
    async for tenant_doc in cursor:
        tenant_id = str(tenant_doc.get("_id"))
        if not tenant_id:
            continue
        summary["tenants_seen"] += 1

        company_name = tenant_doc.get("company_name") or "HQ"

        # Count branches BEFORE provisioning so we can tell whether
        # ensure_default_branch actually created one.
        try:
            existing_branches = await db.branches.count_documents(
                {"tenant_id": tenant_id}
            )
        except Exception:
            existing_branches = 0

        try:
            hq_branch = await ensure_default_branch(
                tenant_id=tenant_id,
                company_name=company_name,
            )
        except Exception:
            logger.warning(
                "branch_backfill: ensure_default_branch failed for tenant=%s",
                tenant_id,
                exc_info=True,
            )
            continue

        if existing_branches == 0:
            summary["tenants_with_new_branch"] += 1

        hq_id = hq_branch.id if hq_branch else None
        if not hq_id:
            continue

        # Assign HQ to every user whose branch_ids is missing or empty.
        try:
            res: Any = await db.system_users.update_many(
                {
                    "tenant_id": tenant_id,
                    "$or": [
                        {"branch_ids": {"$exists": False}},
                        {"branch_ids": None},
                        {"branch_ids": []},
                    ],
                },
                {"$set": {"branch_ids": [hq_id]}},
            )
            summary["users_updated"] += int(getattr(res, "modified_count", 0) or 0)
        except Exception:
            logger.warning(
                "branch_backfill: user backfill failed for tenant=%s",
                tenant_id,
                exc_info=True,
            )

        # Tag legacy branch-null records to HQ. For appointments / visit
        # sessions / check-ins this keeps them visible to branch-scoped
        # reads; for departments / incident_logs (not read-filtered by
        # branch) it keeps them in the HQ cap-bucket / attribution group
        # instead of an unbucketed "no branch" group.
        for coll in _branch_scoped_collections:
            try:
                res2: Any = await db[coll].update_many(
                    {
                        "tenant_id": tenant_id,
                        "$or": [
                            {"branch_id": {"$exists": False}},
                            {"branch_id": None},
                            {"branch_id": ""},
                        ],
                    },
                    {"$set": {"branch_id": hq_id}},
                )
                summary["records_tagged_hq"] += int(
                    getattr(res2, "modified_count", 0) or 0
                )
            except Exception:
                logger.warning(
                    "branch_backfill: %s branch tagging failed for tenant=%s",
                    coll,
                    tenant_id,
                    exc_info=True,
                )

    return summary
