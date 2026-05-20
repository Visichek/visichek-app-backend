"""Unit tests for free-trial enablement on paid plans + the one-time-trial
watchdog.

Covers three changes:

1. Canonical config — Starter/Premium carry a trial; Free/Enterprise do not.
   The bootstrap PlanCreate/PlanUpdate payloads forward trial_days.
2. plan_service — Enterprise plans reject trial_days > 0 (custom-priced,
   no self-service trial); paid tiers accept it.
3. trial integrity — mark_trial_code_used is race-safe against the
   tenant_used_trial_unique partial-unique index, and
   reconcile_trial_integrity flags tenants with duplicate redeemed trials.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from schemas.plan_schema import PlanCreate, PlanOut, PlanStatus, PlanTier, PlanUpdate
from schemas.trial_code_schema import TrialCodeOut, TrialCodeStatus

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan_out(**overrides) -> PlanOut:
    defaults = {
        "_id": "5f0a1b2c3d4e5f6a7b8c9d0e",
        "name": "enterprise-acme",
        "display_name": "Acme Enterprise",
        "tier": PlanTier.ENTERPRISE.value,
        "status": PlanStatus.ACTIVE.value,
        "base_price_monthly": 0.0,
        "base_price_yearly": 0.0,
        "currency": "NGN",
        "feature_rules": [],
        "crud_limits": [],
        "retrieval_quotas": [],
        "storage_limits": {},
        "tenant_caps": {},
        "trial_days": 0,
        "date_created": int(time.time()),
        "last_updated": int(time.time()),
    }
    defaults.update(overrides)
    return PlanOut(**defaults)  # type: ignore[arg-type]


def _make_trial_out(**overrides) -> TrialCodeOut:
    defaults = {
        "_id": "6f0a1b2c3d4e5f6a7b8c9d0e",
        "code": "TRIAL-ABC123-DEADBEEF",
        "tenant_id": "tenant_1",
        "plan_id": "5f0a1b2c3d4e5f6a7b8c9d0e",
        "status": TrialCodeStatus.PENDING.value,
        "trial_days_snapshot": 14,
    }
    defaults.update(overrides)
    return TrialCodeOut(**defaults)  # type: ignore[arg-type]


class _AsyncCursor:
    """Minimal async iterator standing in for a Motor aggregate cursor."""

    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeCollection:
    def __init__(self, agg_rows=None, find_one_result=None):
        self._agg_rows = agg_rows or []
        self._find_one_result = find_one_result
        self.inserted = []

    def aggregate(self, pipeline):
        return _AsyncCursor(self._agg_rows)

    async def find_one(self, *args, **kwargs):
        return self._find_one_result

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return AsyncMock(inserted_id="case_1")


class _FakeDB:
    def __init__(self, collections):
        self._collections = collections

    def __getitem__(self, name):
        return self._collections[name]

    def __getattr__(self, name):
        return self._collections[name]


# ---------------------------------------------------------------------------
# 1. Canonical config + bootstrap payloads
# ---------------------------------------------------------------------------


class TestCanonicalTrialDays:
    async def test_paid_singletons_have_trial_free_and_enterprise_do_not(self):
        from config.plan_tiers import (
            ENTERPRISE_TEMPLATE,
            FREE_PLAN,
            PAID_PLAN_TRIAL_DAYS,
            PREMIUM_PLAN,
            STARTER_PLAN,
        )

        assert PAID_PLAN_TRIAL_DAYS > 0
        assert STARTER_PLAN.trial_days == PAID_PLAN_TRIAL_DAYS
        assert PREMIUM_PLAN.trial_days == PAID_PLAN_TRIAL_DAYS
        assert FREE_PLAN.trial_days == 0
        assert ENTERPRISE_TEMPLATE.trial_days == 0

    async def test_bootstrap_create_payload_carries_trial_days(self):
        from config.plan_tiers import STARTER_PLAN
        from services.plan_bootstrap import _canonical_to_plan_create

        payload = _canonical_to_plan_create(STARTER_PLAN)
        assert isinstance(payload, PlanCreate)
        assert payload.trial_days == STARTER_PLAN.trial_days

    async def test_bootstrap_update_payload_refreshes_trial_days(self):
        from config.plan_tiers import PREMIUM_PLAN
        from services.plan_bootstrap import _canonical_to_plan_update

        payload = _canonical_to_plan_update(PREMIUM_PLAN)
        assert isinstance(payload, PlanUpdate)
        assert payload.trial_days == PREMIUM_PLAN.trial_days


# ---------------------------------------------------------------------------
# 2. Enterprise plans cannot carry a trial
# ---------------------------------------------------------------------------


class TestEnterpriseTrialRejection:
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_add_enterprise_plan_with_trial_rejected(self, mock_get):
        mock_get.return_value = None  # no tier clash, no name clash
        from services.plan_service import add_plan

        with pytest.raises(HTTPException) as exc:
            await add_plan(
                PlanCreate(
                    name="enterprise-acme",
                    display_name="Acme",
                    tier=PlanTier.ENTERPRISE,
                    trial_days=14,
                )
            )
        assert exc.value.status_code == 400
        assert "free trial" in str(exc.value.detail).lower()

    @patch("services.plan_service.create_plan", new_callable=AsyncMock)
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_add_premium_plan_with_trial_allowed(self, mock_get, mock_create):
        # premium tier already exists with same name → singleton guard ok;
        # but we want the create path: no existing plan at all.
        mock_get.return_value = None
        mock_create.return_value = _make_plan_out(
            name="premium", tier=PlanTier.PREMIUM.value, trial_days=14
        )
        from services.plan_service import add_plan

        result = await add_plan(
            PlanCreate(
                name="premium",
                display_name="Premium",
                tier=PlanTier.PREMIUM,
                trial_days=14,
            )
        )
        assert result.trial_days == 14
        mock_create.assert_awaited_once()

    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_update_enterprise_plan_with_trial_rejected(self, mock_get):
        mock_get.return_value = _make_plan_out(tier=PlanTier.ENTERPRISE.value)
        from services.plan_service import update_plan_by_id

        with pytest.raises(HTTPException) as exc:
            await update_plan_by_id(
                "5f0a1b2c3d4e5f6a7b8c9d0e", PlanUpdate(trial_days=30)
            )
        assert exc.value.status_code == 400
        assert "free trial" in str(exc.value.detail).lower()


# ---------------------------------------------------------------------------
# 3a. mark_trial_code_used is race-safe
# ---------------------------------------------------------------------------


class TestTrialRedeemRaceSafe:
    @patch("services.trial_code_service.record_audit_event", new_callable=AsyncMock)
    @patch(
        "services.trial_code_service.get_tenant_redeemed_trial", new_callable=AsyncMock
    )
    @patch("services.trial_code_service.update_trial_code", new_callable=AsyncMock)
    @patch("services.trial_code_service.get_trial_code", new_callable=AsyncMock)
    async def test_duplicate_key_treated_as_already_redeemed(
        self, mock_get, mock_update, mock_redeemed, mock_audit
    ):
        # The code we're trying to redeem is still pending...
        mock_get.return_value = _make_trial_out(status=TrialCodeStatus.PENDING.value)
        # ...but the unique index rejects the write because another code
        # already won the race for this tenant.
        mock_update.side_effect = DuplicateKeyError("dup")
        winning = _make_trial_out(
            _id="aaaaaaaaaaaaaaaaaaaaaaaa",
            code="TRIAL-OTHER-CODE",
            status=TrialCodeStatus.USED.value,
        )
        mock_redeemed.return_value = winning

        from services.trial_code_service import mark_trial_code_used

        result = await mark_trial_code_used(
            code="TRIAL-ABC123-DEADBEEF",
            subscription_id="sub_1",
            tenant_id="tenant_1",
        )
        # No exception; returns the trial that actually won.
        assert result is winning
        mock_redeemed.assert_awaited_once_with("tenant_1")


# ---------------------------------------------------------------------------
# 3b. reconcile_trial_integrity watchdog
# ---------------------------------------------------------------------------


class TestTrialWatchdog:
    @patch("services.trial_watchdog_service.record_audit_event", new_callable=AsyncMock)
    async def test_reconcile_flags_duplicate_and_opens_case(self, mock_audit):
        trial_codes = _FakeCollection(
            agg_rows=[
                {
                    "_id": "tenant_dup",
                    "count": 2,
                    "ids": ["id_a", "id_b"],
                }
            ]
        )
        support_cases = _FakeCollection(find_one_result=None)  # no open case yet
        fake_db = _FakeDB({"trial_codes": trial_codes, "support_cases": support_cases})

        with patch("services.trial_watchdog_service.db", fake_db):
            from services.trial_watchdog_service import reconcile_trial_integrity

            summary = await reconcile_trial_integrity()

        assert summary["scanned_violations"] == 1
        assert summary["cases_opened"] == 1
        assert len(support_cases.inserted) == 1
        mock_audit.assert_awaited_once()

    @patch("services.trial_watchdog_service.record_audit_event", new_callable=AsyncMock)
    async def test_reconcile_clean_platform_is_noop(self, mock_audit):
        fake_db = _FakeDB(
            {
                "trial_codes": _FakeCollection(agg_rows=[]),
                "support_cases": _FakeCollection(),
            }
        )
        with patch("services.trial_watchdog_service.db", fake_db):
            from services.trial_watchdog_service import reconcile_trial_integrity

            summary = await reconcile_trial_integrity()

        assert summary == {"scanned_violations": 0, "cases_opened": 0}
        mock_audit.assert_not_awaited()

    @patch("services.trial_watchdog_service.record_audit_event", new_callable=AsyncMock)
    async def test_reconcile_reuses_existing_open_case(self, mock_audit):
        support_cases = _FakeCollection(find_one_result={"_id": "existing_case"})
        fake_db = _FakeDB(
            {
                "trial_codes": _FakeCollection(
                    agg_rows=[{"_id": "tenant_dup", "count": 3, "ids": ["a", "b", "c"]}]
                ),
                "support_cases": support_cases,
            }
        )
        with patch("services.trial_watchdog_service.db", fake_db):
            from services.trial_watchdog_service import reconcile_trial_integrity

            summary = await reconcile_trial_integrity()

        # violation still counted + audited, but no NEW case opened
        assert summary["scanned_violations"] == 1
        assert summary["cases_opened"] == 0
        assert support_cases.inserted == []
        mock_audit.assert_awaited_once()
