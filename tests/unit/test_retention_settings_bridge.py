from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.imports import DeletionAction
from services.retention_service import _implicit_policies_from_settings


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
        assert set(by_scope) == {"visit_sessions", "checkins", "visitor_profiles"}
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
