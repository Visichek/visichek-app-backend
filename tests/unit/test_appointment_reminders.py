"""Appointment reminder sweeper — hosts get a heads-up ~30 min ahead.

``notify_appointment_reminder`` (and the ``email_on_appointment_reminder``
preference in the UI) existed with no caller; the sweeper added in
``services/appointment_lifecycle_service`` is that caller. These tests
pin the claim semantics (atomic ``reminder_sent_at`` flip → no
double-sends) and the host-resolution fallbacks.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

from schemas.imports import UserType
from services.appointment_lifecycle_service import (
    _resolve_reminder_recipient,
    send_due_appointment_reminders,
)

pytestmark = pytest.mark.unit


class _FakeCursor:
    """Async-iterable stand-in for a Motor find().sort().limit() chain."""

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
    """Build a db mock whose expected_appointments supports the sweep."""
    collection = MagicMock()
    collection.find.return_value = _FakeCursor(docs)
    if claim_result == "claim":
        collection.find_one_and_update = AsyncMock(side_effect=lambda *a, **k: docs[0])
    else:
        collection.find_one_and_update = AsyncMock(return_value=None)
    fake = MagicMock()
    fake.expected_appointments = collection
    return fake


def _appointment_doc() -> dict:
    return {
        "_id": ObjectId(),
        "tenant_id": "t1",
        "host_id": str(ObjectId()),
        "visitor_name_snapshot": "Jane Visitor",
        "status": "scheduled",
        "scheduled_datetime": 1_900_000_000,
    }


# ── Sweep behavior ───────────────────────────────────────────────────


@patch(
    "services.notification_service.notify_appointment_reminder",
    new_callable=AsyncMock,
)
@patch(
    "services.appointment_lifecycle_service._resolve_reminder_recipient",
    new_callable=AsyncMock,
)
async def test_reminder_sent_to_system_user_host(
    mock_resolve: AsyncMock, mock_notify: AsyncMock
) -> None:
    doc = _appointment_doc()
    mock_resolve.return_value = ("host-user-1", {})

    with patch("services.appointment_lifecycle_service.db", _fake_db([doc])):
        result = await send_due_appointment_reminders()

    assert result["sent"] == 1
    mock_notify.assert_awaited_once()
    kwargs = mock_notify.await_args
    assert kwargs is not None
    assert kwargs.kwargs["user_id"] == "host-user-1"
    assert kwargs.kwargs["user_type"] == UserType.SYSTEM_USER
    assert kwargs.kwargs["visitor_name"] == "Jane Visitor"
    assert kwargs.kwargs["tenant_id"] == "t1"


@patch(
    "services.appointment_lifecycle_service._send_dedicated_host_reminder",
    new_callable=AsyncMock,
)
@patch(
    "services.appointment_lifecycle_service._resolve_reminder_recipient",
    new_callable=AsyncMock,
)
async def test_reminder_emailed_to_dedicated_host(
    mock_resolve: AsyncMock, mock_email: AsyncMock
) -> None:
    doc = _appointment_doc()
    mock_resolve.return_value = (None, {"name": "Dede Host", "email": "dede@acme.test"})

    with patch("services.appointment_lifecycle_service.db", _fake_db([doc])):
        result = await send_due_appointment_reminders()

    assert result["sent"] == 1
    mock_email.assert_awaited_once()
    call = mock_email.await_args
    assert call is not None
    assert call.kwargs["host_email"] == "dede@acme.test"
    assert call.kwargs["visitor_name"] == "Jane Visitor"


@patch(
    "services.notification_service.notify_appointment_reminder",
    new_callable=AsyncMock,
)
@patch(
    "services.appointment_lifecycle_service._resolve_reminder_recipient",
    new_callable=AsyncMock,
)
async def test_lost_claim_never_double_sends(
    mock_resolve: AsyncMock, mock_notify: AsyncMock
) -> None:
    """If another sweep instance claimed the row first, we skip it."""
    doc = _appointment_doc()

    with patch(
        "services.appointment_lifecycle_service.db",
        _fake_db([doc], claim_result="lost"),
    ):
        result = await send_due_appointment_reminders()

    assert result["sent"] == 0
    assert result["skipped"] == 1
    mock_notify.assert_not_awaited()
    mock_resolve.assert_not_awaited()


@patch(
    "services.appointment_lifecycle_service._resolve_reminder_recipient",
    new_callable=AsyncMock,
)
async def test_unreachable_host_is_skipped(mock_resolve: AsyncMock) -> None:
    doc = _appointment_doc()
    mock_resolve.return_value = (None, {})

    with patch("services.appointment_lifecycle_service.db", _fake_db([doc])):
        result = await send_due_appointment_reminders()

    assert result["sent"] == 0
    assert result["skipped"] == 1


async def test_sweep_query_filters_on_claim_marker() -> None:
    """The find filter must exclude already-reminded rows."""
    fake = _fake_db([])
    with patch("services.appointment_lifecycle_service.db", fake):
        await send_due_appointment_reminders()

    filter_used = fake.expected_appointments.find.call_args.args[0]
    assert filter_used["status"] == "scheduled"
    assert filter_used["reminder_sent_at"] is None
    assert "$gte" in filter_used["scheduled_datetime"]
    assert "$lte" in filter_used["scheduled_datetime"]


# ── Host resolution fallbacks ────────────────────────────────────────


@patch("repositories.host_repo.get_host", new_callable=AsyncMock)
async def test_resolver_prefers_source_system_user(mock_get_host: AsyncMock) -> None:
    mock_get_host.return_value = SimpleNamespace(
        source_system_user_id="su-1", name="H", email="h@x.test"
    )
    user_id, contact = await _resolve_reminder_recipient(str(ObjectId()))
    assert user_id == "su-1"
    assert contact == {}


@patch("repositories.host_repo.get_host", new_callable=AsyncMock)
async def test_resolver_returns_contact_for_dedicated_host(
    mock_get_host: AsyncMock,
) -> None:
    mock_get_host.return_value = SimpleNamespace(
        source_system_user_id=None, name="Dede", email="dede@acme.test"
    )
    user_id, contact = await _resolve_reminder_recipient(str(ObjectId()))
    assert user_id is None
    assert contact == {"name": "Dede", "email": "dede@acme.test"}


@patch("repositories.system_user_repo.get_system_user", new_callable=AsyncMock)
@patch("repositories.host_repo.get_host", new_callable=AsyncMock)
async def test_resolver_falls_back_to_legacy_system_user(
    mock_get_host: AsyncMock, mock_get_user: AsyncMock
) -> None:
    mock_get_host.return_value = None
    mock_get_user.return_value = SimpleNamespace(id="legacy-1")
    user_id, contact = await _resolve_reminder_recipient(str(ObjectId()))
    assert user_id == "legacy-1"
    assert contact == {}


async def test_resolver_rejects_malformed_host_id() -> None:
    user_id, contact = await _resolve_reminder_recipient("not-an-objectid")
    assert user_id is None
    assert contact == {}
