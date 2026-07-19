"""NDPC incident-deadline watchdog.

``notify_incident_deadline`` (and the ``email_on_incident`` preference)
existed with no caller — nothing proactively paged anyone as the 72-hour
reporting deadline approached. The hourly sweep added in
``services/incident_deadline_alert_service`` closes that gap. These
tests pin the query window, the atomic claim, and the recipient roles.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

from services.incident_deadline_alert_service import (
    _alert_recipients_for_tenant,
    alert_approaching_incident_deadlines,
)

pytestmark = pytest.mark.unit


class _FakeCursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


def _fake_db(docs: list[dict], claim_result="claim") -> MagicMock:
    collection = MagicMock()
    collection.find.return_value = _FakeCursor(docs)
    if claim_result == "claim":
        collection.find_one_and_update = AsyncMock(side_effect=lambda *a, **k: docs[0])
    else:
        collection.find_one_and_update = AsyncMock(return_value=None)
    fake = MagicMock()
    fake.incident_logs = collection
    return fake


def _incident_doc() -> dict:
    return {
        "_id": ObjectId(),
        "tenant_id": "t1",
        "notification_deadline": 1_900_000_000,
        "notification_sent_at": None,
        "ndpc_notified": False,
    }


@patch(
    "services.notification_service.notify_incident_deadline",
    new_callable=AsyncMock,
)
@patch(
    "services.incident_deadline_alert_service._alert_recipients_for_tenant",
    new_callable=AsyncMock,
)
async def test_alerts_every_compliance_recipient(
    mock_recipients: AsyncMock, mock_notify: AsyncMock
) -> None:
    doc = _incident_doc()
    mock_recipients.return_value = [
        SimpleNamespace(id="dpo-1"),
        SimpleNamespace(id="sec-1"),
    ]

    with patch("services.incident_deadline_alert_service.db", _fake_db([doc])):
        result = await alert_approaching_incident_deadlines()

    assert result["alerted"] == 1
    assert mock_notify.await_count == 2
    notified_ids = {c.kwargs["user_id"] for c in mock_notify.await_args_list}
    assert notified_ids == {"dpo-1", "sec-1"}
    for call in mock_notify.await_args_list:
        assert call.kwargs["incident_id"] == str(doc["_id"])
        assert call.kwargs["tenant_id"] == "t1"


@patch(
    "services.notification_service.notify_incident_deadline",
    new_callable=AsyncMock,
)
@patch(
    "services.incident_deadline_alert_service._alert_recipients_for_tenant",
    new_callable=AsyncMock,
)
async def test_lost_claim_never_double_pages(
    mock_recipients: AsyncMock, mock_notify: AsyncMock
) -> None:
    doc = _incident_doc()

    with patch(
        "services.incident_deadline_alert_service.db",
        _fake_db([doc], claim_result="lost"),
    ):
        result = await alert_approaching_incident_deadlines()

    assert result["alerted"] == 0
    assert result["skipped"] == 1
    mock_notify.assert_not_awaited()
    mock_recipients.assert_not_awaited()


@patch(
    "services.notification_service.notify_incident_deadline",
    new_callable=AsyncMock,
)
@patch(
    "services.incident_deadline_alert_service._alert_recipients_for_tenant",
    new_callable=AsyncMock,
)
async def test_no_recipients_counts_as_skipped(
    mock_recipients: AsyncMock, mock_notify: AsyncMock
) -> None:
    doc = _incident_doc()
    mock_recipients.return_value = []

    with patch("services.incident_deadline_alert_service.db", _fake_db([doc])):
        result = await alert_approaching_incident_deadlines()

    assert result["alerted"] == 0
    assert result["skipped"] == 1
    mock_notify.assert_not_awaited()


async def test_query_window_and_dedup_filters() -> None:
    fake = _fake_db([])
    with patch("services.incident_deadline_alert_service.db", fake):
        await alert_approaching_incident_deadlines()

    filter_used = fake.incident_logs.find.call_args.args[0]
    assert filter_used["notification_sent_at"] is None
    assert filter_used["ndpc_notified"] == {"$ne": True}
    assert filter_used["deadline_alert_sent_at"] is None
    window = filter_used["notification_deadline"]
    assert window["$lte"] - window["$gte"] == 24 * 3600


@patch("repositories.system_user_repo.get_system_users", new_callable=AsyncMock)
async def test_recipient_query_targets_compliance_roles(
    mock_users: AsyncMock,
) -> None:
    mock_users.return_value = []
    await _alert_recipients_for_tenant("t1")

    call = mock_users.await_args
    assert call is not None
    filter_used = call.args[0]
    assert filter_used["tenant_id"] == "t1"
    assert set(filter_used["role"]["$in"]) == {
        "dpo",
        "security_officer",
        "super_admin",
    }
    assert filter_used["is_active"] is True
