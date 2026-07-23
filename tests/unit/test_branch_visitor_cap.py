"""Unit tests for Task 3: per-branch visitor cap enforcement, new-visitors-only
(WS0.2).

Covers:
- ``services.plan_limits.enforce_branch_visitor_cap`` — per-branch cap,
  tenant-wide fallback (incl. addon top-up), returning-visitor bypass,
  fail-open when plan unresolved.
- Call-site behavior at ``visit_session_service.check_in_visitor`` and
  ``public_registration_service.register_visitor_public``: returning
  visitor never blocked even when branch is "full"; branch A at cap does
  not block branch B; kiosk path (``checkin_service._submit_verified_checkin_core``)
  now enforces where it previously didn't.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from services.plan_limits import enforce_branch_visitor_cap


@pytest.mark.unit
@pytest.mark.asyncio
class TestEnforceBranchVisitorCap:
    async def test_returning_visitor_never_blocks_even_at_branch_cap(self):
        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(return_value=1000),
        ):
            await enforce_branch_visitor_cap(
                "tenant-1",
                "branch-1",
                {"tenant_caps": {"visitors_per_branch_per_month": 5}},
                is_new_visitor=False,
            )
        # No exception raised — returning visitors are never capped.

    async def test_new_visitor_blocked_at_branch_cap(self):
        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(return_value=5),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await enforce_branch_visitor_cap(
                    "tenant-1",
                    "branch-1",
                    {"tenant_caps": {"visitors_per_branch_per_month": 5}},
                    is_new_visitor=True,
                )
        assert exc_info.value.status_code == 429

    async def test_new_visitor_under_branch_cap_allowed(self):
        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(return_value=4),
        ):
            await enforce_branch_visitor_cap(
                "tenant-1",
                "branch-1",
                {"tenant_caps": {"visitors_per_branch_per_month": 5}},
                is_new_visitor=True,
            )

    async def test_branch_a_at_cap_does_not_block_branch_b(self):
        """count_new_for_month is scoped per-branch — a full branch-A count
        must not leak into the branch-B check."""
        async def fake_count(tenant_id, start, end, branch_id=None):
            return 5 if branch_id == "branch-A" else 0

        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(side_effect=fake_count),
        ):
            with pytest.raises(HTTPException):
                await enforce_branch_visitor_cap(
                    "tenant-1",
                    "branch-A",
                    {"tenant_caps": {"visitors_per_branch_per_month": 5}},
                    is_new_visitor=True,
                )
            # Branch B is empty — must succeed.
            await enforce_branch_visitor_cap(
                "tenant-1",
                "branch-B",
                {"tenant_caps": {"visitors_per_branch_per_month": 5}},
                is_new_visitor=True,
            )

    async def test_tenant_wide_fallback_when_no_per_branch_cap(self):
        """Free/Starter plans have no ``visitors_per_branch_per_month`` —
        falls back to the tenant-wide ``max_visitors_per_month``."""
        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(return_value=50),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await enforce_branch_visitor_cap(
                    "tenant-1",
                    "branch-1",
                    {
                        "tenant_caps": {
                            "visitors_per_branch_per_month": None,
                            "max_visitors_per_month": 50,
                        }
                    },
                    is_new_visitor=True,
                )
        assert exc_info.value.status_code == 429

    async def test_tenant_wide_fallback_includes_addon_top_up(self):
        """``extra_visitors_per_month`` (Task 1 visitor_quota addon) is
        added to the base ``max_visitors_per_month`` before comparing."""
        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(return_value=60),
        ):
            # 60 >= 50 base cap alone would block, but +20 addon lifts it.
            await enforce_branch_visitor_cap(
                "tenant-1",
                "branch-1",
                {
                    "tenant_caps": {
                        "visitors_per_branch_per_month": None,
                        "max_visitors_per_month": 50,
                    },
                    "extra_visitors_per_month": 20,
                },
                is_new_visitor=True,
            )

    async def test_unlimited_tenant_wide_cap_never_blocks(self):
        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(return_value=999999),
        ):
            await enforce_branch_visitor_cap(
                "tenant-1",
                "branch-1",
                {
                    "tenant_caps": {
                        "visitors_per_branch_per_month": None,
                        "max_visitors_per_month": None,
                    }
                },
                is_new_visitor=True,
            )

    async def test_fail_open_when_plan_unresolved(self):
        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(return_value=999999),
        ) as mock_count:
            await enforce_branch_visitor_cap(
                "tenant-1", "branch-1", None, is_new_visitor=True
            )
        mock_count.assert_not_called()

    async def test_new_visitor_no_branch_id_falls_back_tenant_wide(self):
        """If a new visitor's branch couldn't be resolved (shouldn't
        normally happen — HQ fallback always resolves one), the per-branch
        clause is skipped safely and tenant-wide still applies."""
        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(return_value=100),
        ):
            with pytest.raises(HTTPException):
                await enforce_branch_visitor_cap(
                    "tenant-1",
                    None,
                    {
                        "tenant_caps": {
                            "visitors_per_branch_per_month": 10,
                            "max_visitors_per_month": 100,
                        }
                    },
                    is_new_visitor=True,
                )

    async def test_custom_friendly_message_used(self):
        with patch(
            "repositories.visitor_branch_first_repo.count_new_for_month",
            AsyncMock(return_value=5),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await enforce_branch_visitor_cap(
                    "tenant-1",
                    "branch-1",
                    {"tenant_caps": {"visitors_per_branch_per_month": 5}},
                    is_new_visitor=True,
                    friendly_message="custom kiosk message",
                )
        assert exc_info.value.detail == "custom kiosk message"


@pytest.mark.unit
@pytest.mark.asyncio
class TestHasFirstSeenPeek:
    async def test_has_first_seen_true_when_row_exists(self):
        from repositories import visitor_branch_first_repo as repo
        from unittest.mock import MagicMock

        fake_collection = MagicMock()
        fake_collection.find_one = AsyncMock(return_value={"_id": "x"})
        fake_db = {"visitor_branch_firsts": fake_collection}

        with patch.object(repo, "db", fake_db):
            result = await repo.has_first_seen("tenant-1", "branch-1", "profile-1")

        assert result is True

    async def test_has_first_seen_false_when_no_row(self):
        from repositories import visitor_branch_first_repo as repo
        from unittest.mock import MagicMock

        fake_collection = MagicMock()
        fake_collection.find_one = AsyncMock(return_value=None)
        fake_db = {"visitor_branch_firsts": fake_collection}

        with patch.object(repo, "db", fake_db):
            result = await repo.has_first_seen("tenant-1", "branch-1", "profile-1")

        assert result is False


@pytest.mark.unit
class TestPlanTiersVisitorsPerBranch:
    def test_premium_plan_has_branch_cap(self):
        from config.plan_tiers import PREMIUM_PLAN

        assert PREMIUM_PLAN.tenant_caps.visitors_per_branch_per_month == 1000
        assert (
            "visitors_per_branch_per_month" in PREMIUM_PLAN.adjustable_cap_fields
        )

    def test_premium_plan_max_branches_flipped_to_one(self):
        """Task 9: Premium moved from unlimited branches to 1 baked-in
        branch + paid ``additional-branch`` add-ons for more. Existing
        Premium tenants are protected by the grandfathering backfill —
        see services/premium_branch_grandfather_backfill.py."""
        from config.plan_tiers import PREMIUM_PLAN

        assert PREMIUM_PLAN.tenant_caps.max_branches == 1

    def test_enterprise_template_adjustable_fields_include_branch_cap(self):
        from config.plan_tiers import ENTERPRISE_TEMPLATE

        assert (
            "visitors_per_branch_per_month"
            in ENTERPRISE_TEMPLATE.adjustable_cap_fields
        )


@pytest.mark.unit
@pytest.mark.asyncio
class TestKioskPathNowEnforces:
    """WS0.2 deliberately adds cap enforcement to the previously-uncapped
    kiosk paths in checkin_service."""

    async def test_submit_verified_checkin_core_blocks_new_visitor_at_cap(self):
        from services import checkin_service

        with (
            patch.object(
                checkin_service,
                "enforce_consent_if_required",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "_enforce_tenant_geofence",
                AsyncMock(return_value=None),
            ),
            patch(
                "repositories.visitor_repo.find_visitor_by_email_or_phone_any",
                AsyncMock(return_value=None),
            ),
            patch("repositories.visitor_repo.create_visitor", AsyncMock()) as mock_create_visitor,
            patch.object(
                checkin_service,
                "_collect_returning_visitor_fallback",
                AsyncMock(return_value={}),
            ),
            patch.object(
                checkin_service,
                "_upsert_visitor_profile_from_submit",
                AsyncMock(return_value="profile-1"),
            ),
            patch.object(
                checkin_service,
                "get_active_pending_for_visitor",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "_resolve_checkin_branch_id",
                AsyncMock(return_value="branch-1"),
            ),
            patch(
                "repositories.visitor_branch_first_repo.has_first_seen",
                AsyncMock(return_value=False),
            ),
            patch.object(
                checkin_service,
                "get_plan_data_safe",
                AsyncMock(
                    return_value={
                        "tenant_caps": {"visitors_per_branch_per_month": 0}
                    }
                ),
            ),
            patch(
                "repositories.visitor_branch_first_repo.count_new_for_month",
                AsyncMock(return_value=0),
            ),
            patch.object(checkin_service, "create_checkin", AsyncMock()) as mock_create_checkin,
        ):
            from unittest.mock import MagicMock

            from schemas.checkin_schema import CheckinPurpose

            mock_create_visitor.return_value = MagicMock(
                id="visitor-1", portrait_url=None, verified=False
            )

            with pytest.raises(HTTPException) as exc_info:
                await checkin_service._submit_verified_checkin_core(
                    tenant_id="tenant-1",
                    checkin_config_id="config-1",
                    required_field_keys=set(),
                    email=None,
                    phone="+1000000000",
                    bio_data={"full_name": "Jane Doe"},
                    tenant_specific_data={},
                    purpose=CheckinPurpose(purpose="meeting"),
                )

        assert exc_info.value.status_code == 429
        mock_create_checkin.assert_not_called()
        assert exc_info.value.detail == checkin_service.KIOSK_CAP_MESSAGE

    async def test_profile_resolution_failure_at_cap_does_not_block_checkin(self):
        """Task 6 item 2: if ``_upsert_visitor_profile_from_submit`` returns
        None (failure/unresolved), ``is_new_visitor`` must default to False
        so the check-in proceeds even when the branch is at its cap — never
        block on uncertainty about whether this visitor is new."""
        from services import checkin_service

        with (
            patch.object(
                checkin_service,
                "enforce_consent_if_required",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "_enforce_tenant_geofence",
                AsyncMock(return_value=None),
            ),
            patch(
                "repositories.visitor_repo.find_visitor_by_email_or_phone_any",
                AsyncMock(return_value=None),
            ),
            patch("repositories.visitor_repo.create_visitor", AsyncMock()) as mock_create_visitor,
            patch.object(
                checkin_service,
                "_collect_returning_visitor_fallback",
                AsyncMock(return_value={}),
            ),
            patch.object(
                checkin_service,
                "_upsert_visitor_profile_from_submit",
                AsyncMock(return_value=None),  # profile resolution failed
            ),
            patch.object(
                checkin_service,
                "get_active_pending_for_visitor",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "_resolve_checkin_branch_id",
                AsyncMock(return_value="branch-1"),
            ),
            patch.object(
                checkin_service,
                "get_plan_data_safe",
                AsyncMock(
                    return_value={
                        "tenant_caps": {"visitors_per_branch_per_month": 0}
                    }
                ),
            ),
            # Branch is already AT cap — a new visitor here would 429.
            patch(
                "repositories.visitor_branch_first_repo.count_new_for_month",
                AsyncMock(return_value=0),
            ),
            patch.object(checkin_service, "create_checkin", AsyncMock()) as mock_create_checkin,
            patch.object(
                checkin_service,
                "record_visitor_consent",
                AsyncMock(return_value=None),
            ),
        ):
            from unittest.mock import MagicMock

            from schemas.checkin_schema import CheckinPurpose

            mock_create_visitor.return_value = MagicMock(
                id="visitor-1", portrait_url=None, verified=False
            )
            mock_create_checkin.return_value = MagicMock(id="checkin-1")

            # Must NOT raise — profile-resolution failure must never
            # manufacture a "new visitor" verdict that blocks at cap.
            result = await checkin_service._submit_verified_checkin_core(
                tenant_id="tenant-1",
                checkin_config_id="config-1",
                required_field_keys=set(),
                email=None,
                phone="+1000000000",
                bio_data={"full_name": "Jane Doe"},
                tenant_specific_data={},
                purpose=CheckinPurpose(purpose="meeting"),
            )

        assert result is not None
        mock_create_checkin.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
class TestReturningVisitorByIdLedgerInsert:
    """Task 6 item 8: ledger-insert assertion for
    ``submit_returning_visitor_checkin_by_id``."""

    def _base_mocks(self, has_first_seen_return: bool):
        from bson import ObjectId

        visitor_id = str(ObjectId())
        tenant_id = str(ObjectId())

        visitor = MagicMock(
            id=visitor_id,
            email="jane@example.com",
            phone="+1000000000",
            full_name="Jane Doe",
            bio_data={},
            portrait_url=None,
            verified=False,
            verification_method=None,
        )
        tenant = MagicMock(id=tenant_id)

        patches = [
            patch(
                "repositories.tenant_repo.get_tenant",
                AsyncMock(return_value=tenant),
            ),
            patch(
                "repositories.visitor_repo.get_visitor",
                AsyncMock(return_value=visitor),
            ),
            patch(
                "repositories.checkin_config_repo.get_active_checkin_config_for_tenant",
                AsyncMock(return_value=None),
            ),
            patch(
                "services.checkin_config_service.resolve_required_fields_for_tenant",
                AsyncMock(return_value=([], None)),
            ),
        ]
        return tenant_id, visitor_id, patches

    async def test_first_time_at_branch_inserts_ledger_row(self):
        from services import checkin_service

        tenant_id, visitor_id, base_patches = self._base_mocks(
            has_first_seen_return=False
        )

        extra_patches = [
            patch.object(
                checkin_service,
                "enforce_consent_if_required",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "_enforce_tenant_geofence",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "get_active_pending_for_visitor",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "_upsert_visitor_profile_from_submit",
                AsyncMock(return_value="profile-1"),
            ),
            patch.object(
                checkin_service,
                "_resolve_checkin_branch_id",
                AsyncMock(return_value="branch-1"),
            ),
            patch(
                "repositories.visitor_branch_first_repo.has_first_seen",
                AsyncMock(return_value=False),  # never seen at this branch -> new
            ),
            patch.object(
                checkin_service,
                "get_plan_data_safe",
                AsyncMock(return_value=None),  # fail open, cap never checked
            ),
            patch.object(
                checkin_service,
                "create_checkin",
                AsyncMock(return_value=MagicMock(id="checkin-1")),
            ),
            patch.object(
                checkin_service,
                "invalidate_tenant_dashboard_cache",
                MagicMock(),
            ),
            patch.object(
                checkin_service,
                "record_visitor_consent",
                AsyncMock(return_value=None),
            ),
            patch(
                "repositories.visitor_profile_repo.increment_visitor_profile_visits",
                AsyncMock(return_value=None),
            ),
            patch(
                "services.notification_service.notify_checkin_pending_approval",
                AsyncMock(return_value=None),
            ),
        ]
        record_first_seen_patch = patch(
            "repositories.visitor_branch_first_repo.record_first_seen",
            AsyncMock(return_value=True),
        )

        with contextlib.ExitStack() as stack:
            for p in base_patches + extra_patches:
                stack.enter_context(p)
            mock_record_first_seen = stack.enter_context(record_first_seen_patch)

            from schemas.checkin_schema import CheckinPurpose

            await checkin_service.submit_returning_visitor_checkin_by_id(
                tenant_id=tenant_id,
                visitor_id=visitor_id,
                purpose=CheckinPurpose(purpose="meeting"),
                tenant_specific_data={},
            )

        mock_record_first_seen.assert_awaited_once()

    async def test_already_seen_at_branch_no_insert_no_block_at_cap(self):
        from services import checkin_service

        tenant_id, visitor_id, base_patches = self._base_mocks(
            has_first_seen_return=True
        )

        extra_patches = [
            patch.object(
                checkin_service,
                "enforce_consent_if_required",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "_enforce_tenant_geofence",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "get_active_pending_for_visitor",
                AsyncMock(return_value=None),
            ),
            patch.object(
                checkin_service,
                "_upsert_visitor_profile_from_submit",
                AsyncMock(return_value="profile-1"),
            ),
            patch.object(
                checkin_service,
                "_resolve_checkin_branch_id",
                AsyncMock(return_value="branch-1"),
            ),
            patch(
                "repositories.visitor_branch_first_repo.has_first_seen",
                AsyncMock(return_value=True),  # already seen -> returning
            ),
            patch.object(
                checkin_service,
                "get_plan_data_safe",
                AsyncMock(
                    return_value={
                        "tenant_caps": {"visitors_per_branch_per_month": 0}
                    }
                ),
            ),
            # Branch is AT cap for new visitors — must not matter here.
            patch(
                "repositories.visitor_branch_first_repo.count_new_for_month",
                AsyncMock(return_value=0),
            ),
            patch.object(
                checkin_service,
                "create_checkin",
                AsyncMock(return_value=MagicMock(id="checkin-1")),
            ),
            patch.object(
                checkin_service,
                "invalidate_tenant_dashboard_cache",
                MagicMock(),
            ),
            patch.object(
                checkin_service,
                "record_visitor_consent",
                AsyncMock(return_value=None),
            ),
            patch(
                "repositories.visitor_profile_repo.increment_visitor_profile_visits",
                AsyncMock(return_value=None),
            ),
            patch(
                "services.notification_service.notify_checkin_pending_approval",
                AsyncMock(return_value=None),
            ),
        ]
        record_first_seen_patch = patch(
            "repositories.visitor_branch_first_repo.record_first_seen",
            AsyncMock(return_value=True),
        )

        with contextlib.ExitStack() as stack:
            for p in base_patches + extra_patches:
                stack.enter_context(p)
            mock_record_first_seen = stack.enter_context(record_first_seen_patch)

            from schemas.checkin_schema import CheckinPurpose

            result = await checkin_service.submit_returning_visitor_checkin_by_id(
                tenant_id=tenant_id,
                visitor_id=visitor_id,
                purpose=CheckinPurpose(purpose="meeting"),
                tenant_specific_data={},
            )

        assert result is not None
        mock_record_first_seen.assert_not_awaited()
