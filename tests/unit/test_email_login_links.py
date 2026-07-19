"""Email login links must point at the correct portal.

The dual-portal chooser at ``/login`` is retired (the frontend now
redirects it to ``/app/login``), so emails must deep-link the right
sign-in page directly: platform admins → ``/admin/login``, tenant users
→ ``/app/login``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

BASE = "https://client.visichek.app"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_base_url=BASE,
        email_sender_name="VisiChek",
    )


def _manager() -> MagicMock:
    manager = MagicMock()
    manager.send_template = AsyncMock()
    return manager


def _sent_context(manager: MagicMock) -> dict:
    request = manager.send_template.await_args.args[0]
    return dict(request.context)


@patch("core.email.manager.EmailManager.get_instance")
@patch("core.settings.get_settings")
async def test_admin_invite_links_to_admin_login(
    mock_settings: MagicMock, mock_get_instance: MagicMock
) -> None:
    from services.admin_service import _send_admin_invite_email

    mock_settings.return_value = _settings()
    manager = _manager()
    mock_get_instance.return_value = manager

    await _send_admin_invite_email(
        full_name="Pat Admin",
        email="pat@visichek.app",
        temp_password="Temp#Pass123",
        access_preset="all_controls",
        inviter_name="Root Admin",
    )

    ctx = _sent_context(manager)
    assert ctx["login_url"] == f"{BASE}/admin/login"


@patch("core.email.manager.EmailManager.get_instance")
@patch("core.settings.get_settings")
async def test_temp_password_reset_links_to_tenant_login(
    mock_settings: MagicMock, mock_get_instance: MagicMock
) -> None:
    from services.password_change_service import _send_temp_password_reset_email

    mock_settings.return_value = _settings()
    manager = _manager()
    mock_get_instance.return_value = manager

    await _send_temp_password_reset_email(
        full_name="Sam Staff",
        email="sam@acme.test",
        temp_password="Temp#Pass123",
        actor_role="super_admin",
    )

    ctx = _sent_context(manager)
    assert ctx["login_url"] == f"{BASE}/app/login"


@patch("core.email.manager.EmailManager.get_instance")
@patch("core.settings.get_settings")
async def test_super_admin_welcome_links_to_tenant_login(
    mock_settings: MagicMock, mock_get_instance: MagicMock
) -> None:
    from services.system_user_service import _send_super_admin_welcome_email

    mock_settings.return_value = _settings()
    manager = _manager()
    mock_get_instance.return_value = manager

    await _send_super_admin_welcome_email(
        full_name="Sam Super",
        email="sam@acme.test",
        temp_password="Temp#Pass123",
        tenant_company_name="Acme Corp",
    )

    ctx = _sent_context(manager)
    assert ctx["login_url"] == f"{BASE}/app/login"


@patch("core.email.manager.EmailManager.get_instance")
@patch("core.settings.get_settings")
async def test_onboarding_status_email_links_to_tenant_login(
    mock_settings: MagicMock, mock_get_instance: MagicMock
) -> None:
    from services.onboarding_submission_service import _queue_status_email

    mock_settings.return_value = _settings()
    manager = _manager()
    mock_get_instance.return_value = manager

    submission = cast(
        Any,
        SimpleNamespace(
            id="sub1",
            email="founder@acme.test",
            full_name="Fran Founder",
            organization_name="Acme Corp",
            review_notes="",
            pending_field_keys=[],
            pending_field_labels={},
            tenant_id="t1",
        ),
    )
    await _queue_status_email(submission, template_key="onboarding_completed")

    ctx = _sent_context(manager)
    assert ctx["login_url"] == f"{BASE}/app/login"
