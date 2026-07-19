"""Notification email dispatch: transport gate + check-in email wiring.

Covers two production bugs:

* The fan-out used to gate on ``settings.email_host`` — under
  ``EMAIL_PROVIDER=resend`` (no SMTP host) every notification email was
  skipped as "smtp_not_configured" even though the Resend transport was
  fully working. The gate now asks the EmailManager whether ANY
  transport is configured.
* The check-in notifiers never passed ``email_template_key`` /
  ``preference_flag``, so the "Visitor check-in" email preference in the
  UI was inert.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

from core.email.manager import EmailManager
from schemas.imports import UserType
from services.notification_service import (
    _dispatch_email_for_notification,
    notify_checkin_approved,
    notify_checkin_pending_approval,
    notify_checkin_rejected,
)

pytestmark = pytest.mark.unit


# ── EmailManager.has_transport ───────────────────────────────────────


def test_has_transport_false_without_transport() -> None:
    manager = EmailManager(
        transport=None,
        sender_display_name="VisiChek",
        retry_attempts=1,
        retry_backoff_seconds=0.0,
        queue_enabled=False,
    )
    assert manager.has_transport() is False


def test_has_transport_true_with_transport() -> None:
    manager = EmailManager(
        transport=MagicMock(),
        sender_display_name="VisiChek",
        retry_attempts=1,
        retry_backoff_seconds=0.0,
        queue_enabled=False,
        provider="resend",
    )
    assert manager.has_transport() is True


# ── _dispatch_email_for_notification transport gate ──────────────────


def _dispatch_kwargs() -> dict:
    return {
        "notification": MagicMock(),
        "user_id": str(ObjectId()),
        "user_type": UserType.SYSTEM_USER,
        "email_template_key": "notif_visitor_check_in",
        "email_context": {"visitor_name": "Jane Visitor"},
        "preference_flag": "email_on_visitor_check_in",
        "fallback_title": "Visitor Checked In",
        "fallback_body": "Jane Visitor has checked in.",
        "link": None,
    }


def _recipient() -> SimpleNamespace:
    return SimpleNamespace(email="host@acme.test", full_name="Host User")


def _prefs_all_on() -> SimpleNamespace:
    return SimpleNamespace(
        email_enabled=True,
        email_on_visitor_check_in=True,
    )


@patch(
    "services.notification_service._record_email_outbox_send", new_callable=AsyncMock
)
@patch(
    "services.notification_service._record_email_outbox_skip", new_callable=AsyncMock
)
@patch("core.email.manager.EmailManager.get_instance")
@patch(
    "services.notification_service.retrieve_or_create_notification_preferences",
    new_callable=AsyncMock,
)
@patch("repositories.system_user_repo.get_system_user", new_callable=AsyncMock)
@patch("repositories.user_settings_repo.get_user_settings", new_callable=AsyncMock)
async def test_no_transport_records_skip_not_smtp_reason(
    mock_settings_row: AsyncMock,
    mock_get_user: AsyncMock,
    mock_prefs: AsyncMock,
    mock_get_instance: MagicMock,
    mock_skip: AsyncMock,
    mock_send_row: AsyncMock,
) -> None:
    mock_settings_row.return_value = None
    mock_get_user.return_value = _recipient()
    mock_prefs.return_value = _prefs_all_on()

    manager = MagicMock()
    manager.has_transport.return_value = False
    manager.send_template = AsyncMock()
    mock_get_instance.return_value = manager

    await _dispatch_email_for_notification(**_dispatch_kwargs())

    manager.send_template.assert_not_awaited()
    mock_skip.assert_awaited_once()
    skip_call = mock_skip.await_args
    assert skip_call is not None
    assert skip_call.kwargs["reason"] == "email_transport_not_configured"
    mock_send_row.assert_not_awaited()


@patch(
    "services.notification_service._record_email_outbox_send", new_callable=AsyncMock
)
@patch(
    "services.notification_service._record_email_outbox_skip", new_callable=AsyncMock
)
@patch("core.email.manager.EmailManager.get_instance")
@patch(
    "services.notification_service.retrieve_or_create_notification_preferences",
    new_callable=AsyncMock,
)
@patch("repositories.system_user_repo.get_system_user", new_callable=AsyncMock)
@patch("repositories.user_settings_repo.get_user_settings", new_callable=AsyncMock)
async def test_resend_style_transport_sends_without_email_host(
    mock_settings_row: AsyncMock,
    mock_get_user: AsyncMock,
    mock_prefs: AsyncMock,
    mock_get_instance: MagicMock,
    mock_skip: AsyncMock,
    mock_send_row: AsyncMock,
) -> None:
    """A configured Resend transport must dispatch even with no EMAIL_HOST."""
    mock_settings_row.return_value = None
    mock_get_user.return_value = _recipient()
    mock_prefs.return_value = _prefs_all_on()

    manager = MagicMock()
    manager.has_transport.return_value = True
    manager.send_template = AsyncMock(
        return_value=SimpleNamespace(status="queued", task_id="task-1", attempts=0)
    )
    mock_get_instance.return_value = manager

    await _dispatch_email_for_notification(**_dispatch_kwargs())

    manager.send_template.assert_awaited_once()
    request = manager.send_template.await_args.args[0]
    assert request.to_email == "host@acme.test"
    assert request.template_key == "notif_visitor_check_in"
    mock_skip.assert_not_awaited()
    mock_send_row.assert_awaited_once()
    send_row_call = mock_send_row.await_args
    assert send_row_call is not None
    assert send_row_call.kwargs["status"] == "queued"


# ── Check-in notifiers carry the email keys ──────────────────────────


def _approver() -> SimpleNamespace:
    return SimpleNamespace(id=str(ObjectId()))


@patch("services.notification_service.send_notification", new_callable=AsyncMock)
@patch(
    "services.notification_service._get_active_checkin_approvers",
    new_callable=AsyncMock,
)
async def test_checkin_pending_approval_passes_email_keys(
    mock_approvers: AsyncMock, mock_send: AsyncMock
) -> None:
    mock_approvers.return_value = [_approver()]
    await notify_checkin_pending_approval(
        tenant_id="t1",
        checkin_id="c1",
        visitor_name="Jane Visitor",
        verified=True,
        purpose="Meeting",
    )
    mock_send.assert_awaited_once()
    call = mock_send.await_args
    assert call is not None
    kwargs = call.kwargs
    assert kwargs["email_template_key"] == "notif_visitor_check_in"
    assert kwargs["preference_flag"] == "email_on_visitor_check_in"
    assert kwargs["email_context"] == {"visitor_name": "Jane Visitor"}


@patch("services.notification_service.send_notification", new_callable=AsyncMock)
@patch(
    "services.notification_service._get_active_checkin_approvers",
    new_callable=AsyncMock,
)
async def test_checkin_approved_passes_email_keys(
    mock_approvers: AsyncMock, mock_send: AsyncMock
) -> None:
    mock_approvers.return_value = [_approver()]
    await notify_checkin_approved(
        tenant_id="t1",
        checkin_id="c1",
        badge_id="b1",
        visitor_name="Jane Visitor",
        approved_by_user_id="u1",
    )
    call = mock_send.await_args
    assert call is not None
    kwargs = call.kwargs
    assert kwargs["email_template_key"] == "notif_visitor_check_in"
    assert kwargs["preference_flag"] == "email_on_visitor_check_in"


@patch("services.notification_service.send_notification", new_callable=AsyncMock)
@patch(
    "services.notification_service._get_active_checkin_approvers",
    new_callable=AsyncMock,
)
async def test_checkin_rejected_passes_email_keys(
    mock_approvers: AsyncMock, mock_send: AsyncMock
) -> None:
    mock_approvers.return_value = [_approver()]
    await notify_checkin_rejected(
        tenant_id="t1",
        checkin_id="c1",
        visitor_name="Jane Visitor",
        rejected_by_user_id="u1",
        reason="No appointment",
    )
    call = mock_send.await_args
    assert call is not None
    kwargs = call.kwargs
    assert kwargs["email_template_key"] == "notif_visitor_check_in"
    assert kwargs["preference_flag"] == "email_on_visitor_check_in"
