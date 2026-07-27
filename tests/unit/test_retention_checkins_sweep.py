from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.imports import DeletionAction
from services.retention_service import _cleanup_checkins


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d

        return gen()


def _fake_db(docs):
    checkins = MagicMock()
    checkins.find = MagicMock(return_value=_Cursor(docs))
    checkins.delete_one = AsyncMock()
    checkins.update_one = AsyncMock()
    fake = MagicMock()
    fake.checkins = checkins
    fake.__getitem__.return_value = checkins
    return fake, checkins


@pytest.mark.unit
@pytest.mark.asyncio
class TestCleanupCheckins:
    async def test_delete_action_removes_and_logs(self):
        fake, checkins = _fake_db([{"_id": "c1"}, {"_id": "c2"}])
        with patch("services.retention_service.db", fake), patch(
            "services.retention_service.create_deletion_log", AsyncMock()
        ) as mock_log:
            await _cleanup_checkins("t1", 1000, DeletionAction.DELETE)

        assert checkins.delete_one.await_count == 2
        assert mock_log.await_count == 2

    async def test_anonymise_scrubs_tenant_specific_data(self):
        """tenant_specific_data holds the raw kiosk form answers — the
        richest PII on the record. Anonymising must empty it."""
        fake, checkins = _fake_db([{"_id": "c1"}])
        with patch("services.retention_service.db", fake), patch(
            "services.retention_service.create_deletion_log", AsyncMock()
        ):
            await _cleanup_checkins("t1", 1000, DeletionAction.ANONYMISE)

        checkins.delete_one.assert_not_awaited()
        set_payload = checkins.update_one.await_args.args[1]["$set"]
        assert set_payload["tenant_specific_data"] == {}
        assert set_payload["host_name"] == "ANONYMISED"

    async def test_only_terminal_checkins_are_swept(self):
        """A visitor still on-site must never be purged mid-visit."""
        fake, checkins = _fake_db([])
        with patch("services.retention_service.db", fake), patch(
            "services.retention_service.create_deletion_log", AsyncMock()
        ):
            await _cleanup_checkins("t1", 1000, DeletionAction.DELETE)

        state_filter = checkins.find.call_args.args[0]["state"]["$in"]
        assert set(state_filter) == {"checked_out", "rejected"}
