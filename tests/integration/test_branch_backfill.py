"""Integration tests for the boot-time branch-assignment backfill.

Covers FOLLOW-UPS.md item 6: ``backfill_branch_assignments`` runs
``update_many`` against expected_appointments, visit_sessions, checkins,
incident_logs and departments for every tenant on every boot, but until now
only tuple-membership assertions existed. These tests run the real function
against real MongoDB and assert the writes it performs on each collection.
"""

from __future__ import annotations

import time

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase

from schemas.imports import LawfulBasis, NoticeDisplayMode
from schemas.tenant_schema import TenantCreate, TenantOut
from repositories.tenant_repo import create_tenant
from services.branch_backfill import (
    _branch_scoped_collections,
    backfill_branch_assignments,
)

pytestmark = pytest.mark.asyncio


async def _seed_tenant(suffix: str) -> TenantOut:
    return await create_tenant(
        TenantCreate(
            company_name=f"Backfill Co {suffix} {int(time.time())}",
            lawful_basis=LawfulBasis("legitimate_interest"),
            notice_display_mode=NoticeDisplayMode("passive"),
            retention_days=1095,
        )
    )


async def test_backfill_tags_legacy_rows_on_all_five_collections(
    mongo_db: AsyncIOMotorDatabase,
) -> None:
    tenant = await _seed_tenant("legacy")
    tenant_id = tenant.id or ""
    assert tenant_id

    # Legacy rows: one branch-less doc per scoped collection (missing key,
    # explicit None, and empty string across the set to hit every $or arm).
    variants: list[dict[str, object]] = [
        {},
        {"branch_id": None},
        {"branch_id": ""},
    ]
    for i, coll in enumerate(_branch_scoped_collections):
        await mongo_db[coll].insert_one(
            {"tenant_id": tenant_id, "marker": "legacy", **variants[i % 3]}
        )

    # A legacy user with no branch assignments.
    await mongo_db.system_users.insert_one(
        {"tenant_id": tenant_id, "email": "legacy@test.example.com"}
    )

    summary = await backfill_branch_assignments()

    # An HQ branch was provisioned for the branch-less tenant.
    hq = await mongo_db.branches.find_one({"tenant_id": tenant_id})
    assert hq is not None
    hq_id = str(hq["_id"])
    assert summary["tenants_with_new_branch"] >= 1

    # Every scoped collection's legacy row now carries the HQ branch id.
    for coll in _branch_scoped_collections:
        doc = await mongo_db[coll].find_one(
            {"tenant_id": tenant_id, "marker": "legacy"}
        )
        assert doc is not None, coll
        assert doc.get("branch_id") == hq_id, coll

    # The user was assigned to HQ.
    user = await mongo_db.system_users.find_one(
        {"tenant_id": tenant_id, "email": "legacy@test.example.com"}
    )
    assert user is not None
    assert user.get("branch_ids") == [hq_id]


async def test_backfill_leaves_already_tagged_rows_alone_and_is_idempotent(
    mongo_db: AsyncIOMotorDatabase,
) -> None:
    tenant = await _seed_tenant("tagged")
    tenant_id = tenant.id or ""

    # Pre-tagged rows must not be rewritten to HQ.
    for coll in _branch_scoped_collections:
        await mongo_db[coll].insert_one(
            {"tenant_id": tenant_id, "marker": "tagged", "branch_id": "other-branch"}
        )
    await mongo_db.system_users.insert_one(
        {
            "tenant_id": tenant_id,
            "email": "assigned@test.example.com",
            "branch_ids": ["other-branch"],
        }
    )

    await backfill_branch_assignments()

    for coll in _branch_scoped_collections:
        doc = await mongo_db[coll].find_one(
            {"tenant_id": tenant_id, "marker": "tagged"}
        )
        assert doc is not None, coll
        assert doc.get("branch_id") == "other-branch", coll
    user = await mongo_db.system_users.find_one(
        {"tenant_id": tenant_id, "email": "assigned@test.example.com"}
    )
    assert user is not None
    assert user.get("branch_ids") == ["other-branch"]

    # Second run: nothing left to tag for this tenant, and no duplicate
    # HQ branch is provisioned.
    summary2 = await backfill_branch_assignments()
    assert summary2["users_updated"] == 0
    assert summary2["records_tagged_hq"] == 0
    branch_count = await mongo_db.branches.count_documents({"tenant_id": tenant_id})
    assert branch_count == 1
