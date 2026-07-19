"""Support-case emails must respect the recipient's email preferences.

Historically ``support_case_service._queue_email`` sent unconditionally,
so the ``email_on_support_case`` toggle (and the master email switch) in
the notification settings UI did nothing. The gate fails OPEN — lookup
errors must never silently drop a support email.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.imports import UserType
from services.support_case_service import _email_pref_allows, _queue_email

pytestmark = pytest.mark.unit


def _prefs(email_enabled: bool = True, email_on_support_case: bool = True):
    return SimpleNamespace(
        email_enabled=email_enabled,
        email_on_support_case=email_on_support_case,
    )


# ── _email_pref_allows ───────────────────────────────────────────────


@patch(
    "services.notification_service.retrieve_or_create_notification_preferences",
    new_callable=AsyncMock,
)
@patch("repositories.user_settings_repo.get_user_settings", new_callable=AsyncMock)
async def test_master_toggle_off_blocks(
    mock_settings: AsyncMock, mock_prefs: AsyncMock
) -> None:
    mock_settings.return_value = SimpleNamespace(email_notifications=False)
    mock_prefs.return_value = _prefs()
    assert await _email_pref_allows("u1", UserType.SYSTEM_USER) is False


@patch(
    "services.notification_service.retrieve_or_create_notification_preferences",
    new_callable=AsyncMock,
)
@patch("repositories.user_settings_repo.get_user_settings", new_callable=AsyncMock)
async def test_channel_disabled_blocks(
    mock_settings: AsyncMock, mock_prefs: AsyncMock
) -> None:
    mock_settings.return_value = None
    mock_prefs.return_value = _prefs(email_enabled=False)
    assert await _email_pref_allows("u1", UserType.SYSTEM_USER) is False


@patch(
    "services.notification_service.retrieve_or_create_notification_preferences",
    new_callable=AsyncMock,
)
@patch("repositories.user_settings_repo.get_user_settings", new_callable=AsyncMock)
async def test_event_flag_off_blocks(
    mock_settings: AsyncMock, mock_prefs: AsyncMock
) -> None:
    mock_settings.return_value = None
    mock_prefs.return_value = _prefs(email_on_support_case=False)
    assert await _email_pref_allows("u1", UserType.ADMIN) is False


@patch(
    "services.notification_service.retrieve_or_create_notification_preferences",
    new_callable=AsyncMock,
)
@patch("repositories.user_settings_repo.get_user_settings", new_callable=AsyncMock)
async def test_all_enabled_allows(
    mock_settings: AsyncMock, mock_prefs: AsyncMock
) -> None:
    mock_settings.return_value = SimpleNamespace(email_notifications=True)
    mock_prefs.return_value = _prefs()
    assert await _email_pref_allows("u1", UserType.SYSTEM_USER) is True


@patch(
    "services.notification_service.retrieve_or_create_notification_preferences",
    new_callable=AsyncMock,
)
@patch("repositories.user_settings_repo.get_user_settings", new_callable=AsyncMock)
async def test_lookup_errors_fail_open(
    mock_settings: AsyncMock, mock_prefs: AsyncMock
) -> None:
    mock_settings.side_effect = RuntimeError("mongo down")
    mock_prefs.side_effect = RuntimeError("mongo down")
    assert await _email_pref_allows("u1", UserType.SYSTEM_USER) is True


# ── _queue_email gating ──────────────────────────────────────────────


@patch("core.email.manager.EmailManager.get_instance")
@patch("services.support_case_service._email_pref_allows", new_callable=AsyncMock)
async def test_queue_email_skips_when_prefs_deny(
    mock_allows: AsyncMock, mock_get_instance: MagicMock
) -> None:
    mock_allows.return_value = False
    await _queue_email(
        "user@acme.test",
        "support_case.opened.tenant",
        {},
        recipient_user_id="u1",
        recipient_user_type=UserType.SYSTEM_USER,
    )
    mock_get_instance.assert_not_called()


@patch("core.email.manager.EmailManager.get_instance")
@patch("services.support_case_service._email_pref_allows", new_callable=AsyncMock)
async def test_queue_email_sends_when_prefs_allow(
    mock_allows: AsyncMock, mock_get_instance: MagicMock
) -> None:
    mock_allows.return_value = True
    manager = MagicMock()
    manager.send_template = AsyncMock()
    mock_get_instance.return_value = manager

    await _queue_email(
        "user@acme.test",
        "support_case.opened.tenant",
        {"case_id": "c1"},
        recipient_user_id="u1",
        recipient_user_type=UserType.SYSTEM_USER,
    )
    manager.send_template.assert_awaited_once()
    request = manager.send_template.await_args.args[0]
    assert request.to_email == "user@acme.test"
    assert request.template_key == "support_case.opened.tenant"
    assert request.dispatch == "queued"


@patch("core.email.manager.EmailManager.get_instance")
@patch("services.support_case_service._email_pref_allows", new_callable=AsyncMock)
async def test_queue_email_without_identity_sends_unconditionally(
    mock_allows: AsyncMock, mock_get_instance: MagicMock
) -> None:
    """No recipient identity (e.g. legacy caller) → send as before."""
    manager = MagicMock()
    manager.send_template = AsyncMock()
    mock_get_instance.return_value = manager

    await _queue_email("user@acme.test", "support_case.opened.tenant", {})

    mock_allows.assert_not_awaited()
    manager.send_template.assert_awaited_once()
