"""Dunning emails must actually reach the EmailManager.

The old ``_queue_dunning_email`` enqueued a
``services.email_service:send_dunning_email`` task that was never
implemented anywhere, so every payment-failure / downgrade email
silently vanished. The rewrite resolves the tenant's billing contact
(main super_admin) and sends the mounted ``billing_dunning`` template.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.dunning_service import (
    _dunning_stage_copy,
    _queue_dunning_email,
    _resolve_billing_recipient,
)

pytestmark = pytest.mark.unit


def _subscription() -> Any:
    return cast(Any, SimpleNamespace(tenant_id="t1", id="s1"))


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        max_dunning_attempts=5,
        app_base_url="https://client.visichek.app",
        email_sender_name="VisiChek",
    )


# ── Stage copy mapping ───────────────────────────────────────────────


def test_stage_copy_mapping() -> None:
    assert _dunning_stage_copy(1, 5)[0] == "payment_failed"
    assert _dunning_stage_copy(2, 5)[0] == "payment_action_required"
    assert _dunning_stage_copy(3, 5)[0] == "payment_action_required"
    assert _dunning_stage_copy(4, 5)[0] == "payment_last_chance"
    assert _dunning_stage_copy(5, 5)[0] == "subscription_downgraded"
    assert _dunning_stage_copy(None, 5)[0] == "subscription_downgraded"


def test_stage_copy_has_subject_title_body() -> None:
    for attempt in (1, 2, 4, None):
        stage, subject_line, title, body = _dunning_stage_copy(attempt, 5)
        assert stage and subject_line and title and body


# ── _queue_dunning_email ─────────────────────────────────────────────


@patch("core.email.manager.EmailManager.get_instance")
@patch("services.tenant_service.retrieve_tenant_by_id", new_callable=AsyncMock)
@patch("services.dunning_service._resolve_billing_recipient", new_callable=AsyncMock)
@patch("services.dunning_service.get_settings")
async def test_dunning_email_sends_billing_dunning_template(
    mock_settings: MagicMock,
    mock_recipient: AsyncMock,
    mock_tenant: AsyncMock,
    mock_get_instance: MagicMock,
) -> None:
    mock_settings.return_value = _settings()
    mock_recipient.return_value = ("owner@acme.test", "Olive Owner")
    mock_tenant.return_value = SimpleNamespace(company_name="Acme Corp")

    manager = MagicMock()
    manager.send_template = AsyncMock()
    mock_get_instance.return_value = manager

    await _queue_dunning_email(_subscription(), attempt_number=1)

    manager.send_template.assert_awaited_once()
    request = manager.send_template.await_args.args[0]
    assert request.template_key == "billing_dunning"
    assert request.to_email == "owner@acme.test"
    ctx = dict(request.context)
    assert ctx["stage"] == "payment_failed"
    assert ctx["billing_url"] == "https://client.visichek.app/app/billing"
    assert ctx["organization_name"] == "Acme Corp"
    assert ctx["subject_line"]


@patch("core.email.manager.EmailManager.get_instance")
@patch("services.dunning_service._resolve_billing_recipient", new_callable=AsyncMock)
@patch("services.dunning_service.get_settings")
async def test_dunning_email_skipped_without_recipient(
    mock_settings: MagicMock,
    mock_recipient: AsyncMock,
    mock_get_instance: MagicMock,
) -> None:
    mock_settings.return_value = _settings()
    mock_recipient.return_value = (None, None)

    await _queue_dunning_email(_subscription(), attempt_number=None)

    mock_get_instance.assert_not_called()


@patch("repositories.system_user_repo.get_system_users", new_callable=AsyncMock)
async def test_billing_recipient_prefers_main_super_admin(
    mock_users: AsyncMock,
) -> None:
    mock_users.return_value = [
        SimpleNamespace(
            email="second@acme.test",
            full_name="Second SA",
            is_main_super_admin=False,
        ),
        SimpleNamespace(
            email="main@acme.test",
            full_name="Main SA",
            is_main_super_admin=True,
        ),
    ]
    email, name = await _resolve_billing_recipient("t1")
    assert email == "main@acme.test"
    assert name == "Main SA"


@patch("repositories.system_user_repo.get_system_users", new_callable=AsyncMock)
async def test_billing_recipient_none_when_no_super_admins(
    mock_users: AsyncMock,
) -> None:
    mock_users.return_value = []
    email, name = await _resolve_billing_recipient("t1")
    assert email is None and name is None
