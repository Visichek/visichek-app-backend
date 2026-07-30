from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.imports import DeletionAction
from schemas.retention_policy_schema import RetentionPolicyOut
from services.retention_service import (
    _implicit_policies_from_settings,
    run_retention_cleanup,
)


def _fake_db(settings_doc):
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=settings_doc)
    fake = MagicMock()
    fake.__getitem__.return_value = coll
    fake.tenant_settings = coll
    return fake


@pytest.mark.unit
@pytest.mark.asyncio
class TestImplicitPoliciesFromSettings:
    async def test_derives_visitor_scopes_from_settings(self):
        fake = _fake_db(
            {
                "tenant_id": "t1",
                "visitor_data_retention_days": 90,
                "deletion_action": "delete",
            }
        )
        with patch("services.retention_service.db", fake):
            policies = await _implicit_policies_from_settings("t1")

        by_scope = {p.scope: p for p in policies}
        # visitor_profiles is deliberately excluded from the implicit set —
        # see FIX 1 / _IMPLICIT_VISITOR_SCOPES: the settings defaults are
        # not an affirmative opt-in to destructive profile erasure.
        assert set(by_scope) == {"visit_sessions", "checkins"}
        assert all(p.retention_days == 90 for p in policies)
        assert all(p.action == DeletionAction.DELETE for p in policies)

    async def test_defaults_to_anonymise(self):
        fake = _fake_db({"tenant_id": "t1", "visitor_data_retention_days": 365})
        with patch("services.retention_service.db", fake):
            policies = await _implicit_policies_from_settings("t1")

        assert all(p.action == DeletionAction.ANONYMISE for p in policies)

    async def test_no_settings_doc_yields_nothing(self):
        fake = _fake_db(None)
        with patch("services.retention_service.db", fake):
            assert await _implicit_policies_from_settings("t1") == []

    async def test_zero_or_missing_days_yields_nothing(self):
        """0 / None means 'retain indefinitely' — never invent a purge."""
        fake = _fake_db({"tenant_id": "t1", "visitor_data_retention_days": 0})
        with patch("services.retention_service.db", fake):
            assert await _implicit_policies_from_settings("t1") == []


@pytest.mark.unit
@pytest.mark.asyncio
class TestRunRetentionCleanupDispatch:
    """End-to-end coverage for run_retention_cleanup's per-scope dispatch.

    Previously untested: nothing asserted that a policy's ``scope`` value
    actually routes to the matching ``_cleanup_*`` function."""

    async def test_explicit_policies_dispatch_to_matching_cleanup_functions(self):
        tenant = SimpleNamespace(id="t1")
        policies = [
            RetentionPolicyOut(
                _id="1", tenant_id="t1", scope="visit_sessions",
                retention_days=30, action=DeletionAction.ANONYMISE,
            ),
            RetentionPolicyOut(
                _id="2", tenant_id="t1", scope="checkins",
                retention_days=30, action=DeletionAction.ANONYMISE,
            ),
            RetentionPolicyOut(
                _id="3", tenant_id="t1", scope="id_images",
                retention_days=30, action=DeletionAction.DELETE,
            ),
            RetentionPolicyOut(
                _id="4", tenant_id="t1", scope="visitor_profiles",
                retention_days=30, action=DeletionAction.DELETE,
            ),
        ]
        with patch(
            "services.retention_service.get_tenants", AsyncMock(return_value=[tenant])
        ), patch(
            "services.retention_service.get_retention_policies",
            AsyncMock(return_value=policies),
        ), patch(
            "services.retention_service._cleanup_visit_sessions", AsyncMock()
        ) as mock_sessions, patch(
            "services.retention_service._cleanup_checkins", AsyncMock()
        ) as mock_checkins, patch(
            "services.retention_service._cleanup_id_images", AsyncMock()
        ) as mock_images, patch(
            "services.retention_service._cleanup_visitor_profiles", AsyncMock()
        ) as mock_profiles:
            await run_retention_cleanup()

        mock_sessions.assert_awaited_once()
        mock_checkins.assert_awaited_once()
        mock_images.assert_awaited_once()
        mock_profiles.assert_awaited_once()

    async def test_implicit_settings_derived_policies_never_touch_visitor_profiles(self):
        """Given Fix 1: an implicit (settings-derived) policy set must never
        dispatch to _cleanup_visitor_profiles, since that scope is no longer
        part of _IMPLICIT_VISITOR_SCOPES."""
        tenant = SimpleNamespace(id="t1")
        implicit_policies = [
            RetentionPolicyOut(
                _id="1", tenant_id="t1", scope="visit_sessions",
                retention_days=90, action=DeletionAction.ANONYMISE,
            ),
            RetentionPolicyOut(
                _id="2", tenant_id="t1", scope="checkins",
                retention_days=90, action=DeletionAction.ANONYMISE,
            ),
        ]
        with patch(
            "services.retention_service.get_tenants", AsyncMock(return_value=[tenant])
        ), patch(
            "services.retention_service.get_retention_policies",
            AsyncMock(return_value=[]),
        ), patch(
            "services.retention_service._implicit_policies_from_settings",
            AsyncMock(return_value=implicit_policies),
        ), patch(
            "services.retention_service._cleanup_visit_sessions", AsyncMock()
        ) as mock_sessions, patch(
            "services.retention_service._cleanup_checkins", AsyncMock()
        ) as mock_checkins, patch(
            "services.retention_service._cleanup_visitor_profiles", AsyncMock()
        ) as mock_profiles, patch(
            "services.retention_service._cleanup_id_images", AsyncMock()
        ) as mock_images:
            await run_retention_cleanup()

        mock_sessions.assert_awaited_once()
        mock_checkins.assert_awaited_once()
        mock_profiles.assert_not_awaited()
        mock_images.assert_not_awaited()
