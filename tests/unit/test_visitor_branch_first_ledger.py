"""Unit tests for the new-visitor first-seen ledger (WS0.3).

Covers:
- repositories/visitor_branch_first_repo.py — insert/dedupe/count semantics
- services/visitor_first_seen_backfill.py — idempotent backfill
- services/usage_service.get_tenant_usage_summary — ledger-backed numbers
- insertion call sites — visit_session_service, public_registration_service,
  checkin_service (all three create_checkin call sites — kiosk verified
  submit, submit-by-visitor-id, and the legacy checkin-configs submit_checkin
  path) record a ledger row on success
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from pymongo.errors import DuplicateKeyError


# ---------------------------------------------------------------------------
# repositories/visitor_branch_first_repo.py
# ---------------------------------------------------------------------------


class TestVisitorBranchFirstRepo:
    @pytest.mark.asyncio
    async def test_record_first_seen_inserts_new_row(self):
        from repositories import visitor_branch_first_repo as repo

        fake_collection = MagicMock()
        fake_collection.insert_one = AsyncMock(return_value=MagicMock())
        fake_db = {"visitor_branch_firsts": fake_collection}

        with patch.object(repo, "db", fake_db):
            result = await repo.record_first_seen(
                "tenant-1", "branch-1", "profile-1", 1_700_000_000
            )

        assert result is True
        fake_collection.insert_one.assert_awaited_once()
        inserted_doc = fake_collection.insert_one.await_args.args[0]
        assert inserted_doc == {
            "tenant_id": "tenant-1",
            "branch_id": "branch-1",
            "visitor_profile_id": "profile-1",
            "first_seen_at": 1_700_000_000,
        }

    @pytest.mark.asyncio
    async def test_record_first_seen_duplicate_returns_false(self):
        from repositories import visitor_branch_first_repo as repo

        fake_collection = MagicMock()
        fake_collection.insert_one = AsyncMock(
            side_effect=DuplicateKeyError("dup")
        )
        fake_db = {"visitor_branch_firsts": fake_collection}

        with patch.object(repo, "db", fake_db):
            result = await repo.record_first_seen(
                "tenant-1", "branch-1", "profile-1", 1_700_000_000
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_record_first_seen_swallows_unexpected_errors(self):
        from repositories import visitor_branch_first_repo as repo

        fake_collection = MagicMock()
        fake_collection.insert_one = AsyncMock(side_effect=RuntimeError("boom"))
        fake_db = {"visitor_branch_firsts": fake_collection}

        with patch.object(repo, "db", fake_db):
            result = await repo.record_first_seen(
                "tenant-1", "branch-1", "profile-1", 1_700_000_000
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_count_new_for_month_tenant_wide(self):
        from repositories import visitor_branch_first_repo as repo

        fake_collection = MagicMock()
        fake_collection.count_documents = AsyncMock(return_value=7)
        fake_db = {"visitor_branch_firsts": fake_collection}

        with patch.object(repo, "db", fake_db):
            count = await repo.count_new_for_month("tenant-1", 1000, 2000)

        assert count == 7
        query = fake_collection.count_documents.await_args.args[0]
        assert query == {
            "tenant_id": "tenant-1",
            "first_seen_at": {"$gte": 1000, "$lt": 2000},
        }

    @pytest.mark.asyncio
    async def test_count_new_for_month_branch_scoped(self):
        from repositories import visitor_branch_first_repo as repo

        fake_collection = MagicMock()
        fake_collection.count_documents = AsyncMock(return_value=3)
        fake_db = {"visitor_branch_firsts": fake_collection}

        with patch.object(repo, "db", fake_db):
            count = await repo.count_new_for_month(
                "tenant-1", 1000, 2000, branch_id="branch-1"
            )

        assert count == 3
        query = fake_collection.count_documents.await_args.args[0]
        assert query["branch_id"] == "branch-1"

    @pytest.mark.asyncio
    async def test_counts_by_branch_for_month(self):
        from repositories import visitor_branch_first_repo as repo

        async def _agen():
            yield {"_id": "branch-1", "count": 4}
            yield {"_id": "branch-2", "count": 2}

        fake_collection = MagicMock()
        fake_collection.aggregate = MagicMock(return_value=_agen())
        fake_db = {"visitor_branch_firsts": fake_collection}

        with patch.object(repo, "db", fake_db):
            result = await repo.counts_by_branch_for_month("tenant-1", 1000, 2000)

        assert result == {"branch-1": 4, "branch-2": 2}


# ---------------------------------------------------------------------------
# services/visitor_first_seen_backfill.py
# ---------------------------------------------------------------------------


class TestVisitorFirstSeenBackfill:
    @pytest.mark.asyncio
    async def test_earliest_from_visit_sessions_picks_min_timestamp(self):
        from services import visitor_first_seen_backfill as backfill

        async def _agen():
            yield {
                "tenant_id": "t1",
                "branch_id": "b1",
                "visitor_profile_id": "p1",
                "check_in_time": 2000,
                "date_created": 1900,
            }
            yield {
                "tenant_id": "t1",
                "branch_id": "b1",
                "visitor_profile_id": "p1",
                "check_in_time": 1000,
                "date_created": 900,
            }

        fake_cursor = MagicMock()
        fake_cursor.batch_size = MagicMock(return_value=_agen())
        fake_visit_sessions = MagicMock()
        fake_visit_sessions.find = MagicMock(return_value=fake_cursor)

        with patch.object(backfill, "db", MagicMock(visit_sessions=fake_visit_sessions)):
            earliest = await backfill._earliest_from_visit_sessions()

        assert earliest == {("t1", "b1", "p1"): 1000}

    @pytest.mark.asyncio
    async def test_backfill_inserts_missing_rows_idempotently(self):
        from services import visitor_first_seen_backfill as backfill

        earliest_map = {("t1", "b1", "p1"): 1000, ("t1", "b2", "p2"): 2000}

        with (
            patch.object(
                backfill, "_earliest_from_visit_sessions",
                AsyncMock(return_value=dict(earliest_map)),
            ),
            patch.object(
                backfill, "_earliest_from_checkins", AsyncMock(return_value=None)
            ),
            patch.object(
                backfill, "record_first_seen", AsyncMock(return_value=True)
            ) as mock_record,
        ):
            summary = await backfill.backfill_visitor_first_seen()

        assert summary["candidates"] == 2
        assert summary["inserted"] == 2
        assert summary["already_present"] == 0
        assert mock_record.await_count == 2

    @pytest.mark.asyncio
    async def test_backfill_run_twice_is_idempotent(self):
        """Second run sees the rows already present (record_first_seen -> False)."""
        from services import visitor_first_seen_backfill as backfill

        earliest_map = {("t1", "b1", "p1"): 1000}

        with (
            patch.object(
                backfill, "_earliest_from_visit_sessions",
                AsyncMock(return_value=dict(earliest_map)),
            ),
            patch.object(
                backfill, "_earliest_from_checkins", AsyncMock(return_value=None)
            ),
            patch.object(
                backfill, "record_first_seen", AsyncMock(return_value=False)
            ),
        ):
            summary = await backfill.backfill_visitor_first_seen()

        assert summary["candidates"] == 1
        assert summary["inserted"] == 0
        assert summary["already_present"] == 1


# ---------------------------------------------------------------------------
# services/usage_service.get_tenant_usage_summary
# ---------------------------------------------------------------------------


class TestUsageSummaryLedgerNumbers:
    @pytest.mark.asyncio
    async def test_usage_summary_reports_ledger_counts_and_branch_map(self):
        from services import usage_service

        fake_db = MagicMock()
        fake_db.__getitem__.return_value.count_documents = AsyncMock(return_value=0)

        with (
            patch("core.database.db", fake_db),
            patch(
                "repositories.visitor_branch_first_repo.count_new_for_month",
                AsyncMock(return_value=5),
            ),
            patch(
                "repositories.visitor_branch_first_repo.counts_by_branch_for_month",
                AsyncMock(return_value={"branch-1": 3, "branch-2": 2}),
            ),
            patch(
                "services.usage_service.get_current_count",
                AsyncMock(return_value=0),
            ),
        ):
            summary = await usage_service.get_tenant_usage_summary(
                tenant_id="tenant-1",
                subscription_id="sub-1",
                plan_data={
                    "plan_name": "Premium",
                    "tier": "premium",
                    "subscription_status": "active",
                    "crud_limits": [],
                    "retrieval_quotas": [],
                    "tenant_caps": {},
                    "storage_limits": {},
                },
            )

        assert summary.entity_counts["visitors_this_month"] == 5
        assert summary.entity_counts["visitors_by_branch"] == {
            "branch-1": 3,
            "branch-2": 2,
        }
        assert "total_checkins_this_month" in summary.entity_counts


# ---------------------------------------------------------------------------
# services/checkin_service.submit_checkin — the legacy checkin-configs kiosk
# path (api/v1/checkin_config_route.py:97 → submit_checkin). A third,
# distinct create_checkin() call site alongside _submit_verified_checkin_core
# and submit_returning_visitor_checkin_by_id — it uses upsert_visitor_from_checkin
# (legacy `visitors` collection), so it has no visitor_profile_id in scope
# without an explicit profile upsert.
# ---------------------------------------------------------------------------


class TestSubmitCheckinLedger:
    @pytest.mark.asyncio
    async def test_submit_checkin_records_ledger_row(self):
        from schemas.checkin_schema import (
            CheckinPurpose,
            CheckinSubmitRequest,
        )
        from services import checkin_service

        config = MagicMock(tenant_id="tenant-1")
        visitor = MagicMock(
            id="visitor-1",
            email="visitor@example.com",
            phone="+1000000000",
            full_name="Jane Doe",
            bio_data={},
            portrait_url=None,
            verified=False,
            verification_method=None,
        )
        checkin_out = MagicMock(id="checkin-1")

        req = CheckinSubmitRequest(
            bio_data={"full_name": "Jane Doe"},
            tenant_specific_data={},
            purpose=CheckinPurpose(purpose="meeting"),
        )

        with (
            patch.object(
                checkin_service, "get_checkin_config", AsyncMock(return_value=config)
            ),
            patch(
                "services.checkin_config_service.resolve_required_fields_for_tenant",
                AsyncMock(return_value=([], None)),
            ),
            patch(
                "services.visitor_service.upsert_visitor_from_checkin",
                AsyncMock(return_value=visitor),
            ),
            patch.object(
                checkin_service,
                "get_active_pending_for_visitor",
                AsyncMock(return_value=None),
            ),
            patch(
                "services.kyc_service.kyc_available_for_tenant",
                AsyncMock(return_value=(False, False, None)),
            ),
            patch.object(
                checkin_service,
                "_resolve_checkin_branch_id",
                AsyncMock(return_value="branch-1"),
            ),
            patch.object(
                checkin_service, "create_checkin", AsyncMock(return_value=checkin_out)
            ),
            patch.object(
                checkin_service, "invalidate_tenant_dashboard_cache", MagicMock()
            ),
            patch.object(
                checkin_service,
                "_upsert_visitor_profile_from_submit",
                AsyncMock(return_value="profile-1"),
            ),
            patch(
                "repositories.visitor_branch_first_repo.has_first_seen",
                AsyncMock(return_value=False),
            ),
            patch.object(
                checkin_service, "_get_plan_data", AsyncMock(return_value=None)
            ),
            patch(
                "repositories.visitor_branch_first_repo.record_first_seen",
                AsyncMock(return_value=True),
            ) as mock_record,
            patch(
                "services.notification_service.notify_checkin_pending_approval",
                AsyncMock(return_value=None),
            ),
            patch(
                "repositories.visitor_profile_repo.increment_visitor_profile_visits",
                AsyncMock(return_value=None),
            ),
        ):
            result = await checkin_service.submit_checkin("config-1", req)

        assert result is checkin_out
        mock_record.assert_awaited_once_with(
            "tenant-1", "branch-1", "profile-1", pytest.approx(int(time.time()), abs=5)
        )

    @pytest.mark.asyncio
    async def test_submit_checkin_skips_ledger_when_profile_unresolved(self):
        """No visitor_profile_id (profile upsert failed) -> ledger insert
        skipped, but the check-in itself still succeeds."""
        from schemas.checkin_schema import (
            CheckinPurpose,
            CheckinSubmitRequest,
        )
        from services import checkin_service

        config = MagicMock(tenant_id="tenant-1")
        visitor = MagicMock(
            id="visitor-1",
            email=None,
            phone="+1000000000",
            full_name="Jane Doe",
            bio_data={},
            portrait_url=None,
            verified=False,
            verification_method=None,
        )
        checkin_out = MagicMock(id="checkin-1")

        req = CheckinSubmitRequest(
            bio_data={"full_name": "Jane Doe"},
            tenant_specific_data={},
            purpose=CheckinPurpose(purpose="meeting"),
        )

        with (
            patch.object(
                checkin_service, "get_checkin_config", AsyncMock(return_value=config)
            ),
            patch(
                "services.checkin_config_service.resolve_required_fields_for_tenant",
                AsyncMock(return_value=([], None)),
            ),
            patch(
                "services.visitor_service.upsert_visitor_from_checkin",
                AsyncMock(return_value=visitor),
            ),
            patch.object(
                checkin_service,
                "get_active_pending_for_visitor",
                AsyncMock(return_value=None),
            ),
            patch(
                "services.kyc_service.kyc_available_for_tenant",
                AsyncMock(return_value=(False, False, None)),
            ),
            patch.object(
                checkin_service,
                "_resolve_checkin_branch_id",
                AsyncMock(return_value="branch-1"),
            ),
            patch.object(
                checkin_service, "create_checkin", AsyncMock(return_value=checkin_out)
            ),
            patch.object(
                checkin_service, "invalidate_tenant_dashboard_cache", MagicMock()
            ),
            patch.object(
                checkin_service,
                "_upsert_visitor_profile_from_submit",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service, "_get_plan_data", AsyncMock(return_value=None)
            ),
            patch(
                "repositories.visitor_branch_first_repo.record_first_seen",
                AsyncMock(return_value=True),
            ) as mock_record,
            patch(
                "services.notification_service.notify_checkin_pending_approval",
                AsyncMock(return_value=None),
            ),
        ):
            result = await checkin_service.submit_checkin("config-1", req)

        assert result is checkin_out
        mock_record.assert_not_awaited()
