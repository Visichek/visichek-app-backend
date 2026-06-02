"""Unit tests for the pluggable email transport layer (SMTP + Resend).

These cover the additive Resend support without touching MongoDB/Redis:

* ``build_email_transport`` provider selection (SMTP default preserved).
* ``ResendTransport`` field mapping + idempotency-key passing (SDK mocked).
* ``SMTPTransport`` tolerates and ignores the ``idempotency_key`` kwarg.
* ``EmailManager.send_message`` mints ONE idempotency key reused across retries.
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.email import (
    EmailManager,
    EmailMessage,
    ResendConfig,
    ResendTransport,
    SMTPTransport,
    build_email_transport,
)

pytestmark = pytest.mark.unit


def _msg() -> EmailMessage:
    return EmailMessage(
        to_email="delivered@resend.dev",
        subject="Hello",
        html_body="<p>Hi</p>",
        text_body="Hi",
        sender_display_name="VisiChek",
    )


# ── Provider selection ───────────────────────────────────────────────
def test_default_provider_is_smtp_when_configured() -> None:
    t = build_email_transport(
        provider="smtp",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="pass",
        smtp_from_email="from@example.com",
        resend_api_key=None,
        resend_from_email=None,
    )
    assert isinstance(t, SMTPTransport)


def test_unknown_provider_falls_back_to_smtp() -> None:
    t = build_email_transport(
        provider="carrier-pigeon",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="pass",
        smtp_from_email="from@example.com",
        resend_api_key=None,
        resend_from_email=None,
    )
    assert isinstance(t, SMTPTransport)


def test_smtp_without_credentials_returns_none() -> None:
    assert (
        build_email_transport(
            provider="smtp",
            smtp_host=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_from_email=None,
            resend_api_key=None,
            resend_from_email=None,
        )
        is None
    )


def test_resend_provider_selected_when_key_present() -> None:
    t = build_email_transport(
        provider="resend",
        smtp_host=None,
        smtp_port=587,
        smtp_username=None,
        smtp_password=None,
        smtp_from_email=None,
        resend_api_key="re_test_123",
        resend_from_email="no-reply@visichek.app",
    )
    assert isinstance(t, ResendTransport)


def test_resend_from_falls_back_to_smtp_from_then_username() -> None:
    t = build_email_transport(
        provider="resend",
        smtp_host=None,
        smtp_port=587,
        smtp_username="login@visichek.app",
        smtp_password=None,
        smtp_from_email=None,
        resend_api_key="re_test_123",
        resend_from_email=None,
    )
    assert isinstance(t, ResendTransport)
    assert t._config.from_email == "login@visichek.app"


def test_resend_without_api_key_returns_none() -> None:
    assert (
        build_email_transport(
            provider="resend",
            smtp_host=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_from_email=None,
            resend_api_key=None,
            resend_from_email="no-reply@visichek.app",
        )
        is None
    )


# ── ResendTransport behaviour ────────────────────────────────────────
def test_resend_transport_maps_fields_and_passes_options_idempotency() -> None:
    # Matches the installed SDK (>=2.21): send(params, options) where options
    # is a dict carrying ``idempotency_key``.
    captured: dict[str, Any] = {}

    def _send(params: dict[str, Any], options: dict[str, Any] | None = None) -> dict:
        captured["params"] = params
        captured["options"] = options
        return {"id": "re_abc123"}

    fake_resend = types.SimpleNamespace(
        api_key=None, Emails=types.SimpleNamespace(send=_send)
    )

    with patch("core.email.transport._import_resend", return_value=fake_resend):
        transport = ResendTransport(
            ResendConfig(api_key="re_secret", from_email="no-reply@visichek.app")
        )
        transport.send_message(_msg(), idempotency_key="visichek-email/xyz")

    assert fake_resend.api_key == "re_secret"
    assert captured["options"] == {"idempotency_key": "visichek-email/xyz"}
    p = captured["params"]
    assert p["to"] == ["delivered@resend.dev"]
    assert p["subject"] == "Hello"
    assert p["html"] == "<p>Hi</p>"
    assert p["text"] == "Hi"
    # display name folded into the from header
    assert "no-reply@visichek.app" in p["from"]
    assert "VisiChek" in p["from"]


def test_resend_transport_supports_kwarg_style_sdk() -> None:
    # Some SDK builds take an ``idempotency_key=`` kwarg instead of options.
    captured: dict[str, Any] = {}

    def _send(params: dict[str, Any], idempotency_key: str | None = None) -> dict:
        captured["idem"] = idempotency_key
        return {"id": "re_ok"}

    fake_resend = types.SimpleNamespace(
        api_key=None, Emails=types.SimpleNamespace(send=_send)
    )

    with patch("core.email.transport._import_resend", return_value=fake_resend):
        transport = ResendTransport(
            ResendConfig(api_key="re_secret", from_email="no-reply@visichek.app")
        )
        transport.send_message(_msg(), idempotency_key="visichek-email/xyz")

    assert captured["idem"] == "visichek-email/xyz"


def test_resend_transport_handles_sdk_without_idempotency_support() -> None:
    # Hypothetical minimal SDK exposing only send(params): no error, just sends.
    calls: list[dict[str, Any]] = []

    def _send(params: dict[str, Any]) -> dict:
        calls.append(params)
        return {"id": "re_ok"}

    fake_resend = types.SimpleNamespace(
        api_key=None, Emails=types.SimpleNamespace(send=_send)
    )

    with patch("core.email.transport._import_resend", return_value=fake_resend):
        transport = ResendTransport(
            ResendConfig(api_key="re_secret", from_email="no-reply@visichek.app")
        )
        transport.send_message(_msg(), idempotency_key="visichek-email/xyz")

    assert len(calls) == 1


def test_resend_transport_raises_clear_error_when_sdk_missing() -> None:
    # The SDK is imported at construction, so a missing package fails fast at
    # startup (when the transport is built) rather than on the first send.
    with patch(
        "core.email.transport._import_resend",
        side_effect=RuntimeError(
            "EMAIL_PROVIDER=resend requires the 'resend' package."
        ),
    ):
        with pytest.raises(RuntimeError, match="resend"):
            ResendTransport(
                ResendConfig(api_key="re_secret", from_email="no-reply@visichek.app")
            )


# ── SMTPTransport tolerates the shared kwarg ─────────────────────────
def test_smtp_transport_accepts_and_ignores_idempotency_key() -> None:
    from core.email.transport import SmtpConfig

    transport = SMTPTransport(
        SmtpConfig(
            host="smtp.example.com",
            port=587,
            username="user",
            password="pass",
            from_email="from@example.com",
        )
    )
    fake_server = MagicMock()
    with patch("smtplib.SMTP", return_value=fake_server):
        # Passing idempotency_key must not raise; SMTP simply ignores it.
        transport.send_message(_msg(), idempotency_key="visichek-email/ignored")
    fake_server.sendmail.assert_called_once()
    fake_server.quit.assert_called_once()


# ── EmailManager idempotency-key lifecycle ───────────────────────────
class _RecordingTransport:
    """Fails ``fail_times`` then succeeds; records every idempotency key seen."""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.keys: list[str | None] = []

    def send_message(
        self, message: EmailMessage, *, idempotency_key: str | None = None
    ) -> None:
        self.keys.append(idempotency_key)
        if len(self.keys) <= self.fail_times:
            raise RuntimeError("transient")


async def test_manager_reuses_one_idempotency_key_across_retries() -> None:
    transport = _RecordingTransport(fail_times=1)
    mgr = EmailManager(
        transport=transport,
        sender_display_name="VisiChek",
        retry_attempts=3,
        retry_backoff_seconds=0.0,
        queue_enabled=False,
    )

    result = await mgr.send_message(_msg())

    assert result.status == "sent"
    assert result.attempts == 2
    assert len(transport.keys) == 2
    # Same key on every retry of the same logical send, and it's namespaced.
    assert transport.keys[0] == transport.keys[1]
    assert transport.keys[0] is not None
    assert transport.keys[0].startswith("visichek-email/")


async def test_manager_without_transport_raises() -> None:
    mgr = EmailManager(
        transport=None,
        sender_display_name="VisiChek",
        retry_attempts=1,
        retry_backoff_seconds=0.0,
        queue_enabled=False,
    )
    with pytest.raises(RuntimeError, match="transport is not configured"):
        await mgr.send_message(_msg())


async def test_manager_resend_misconfig_error_names_resend_vars() -> None:
    # EMAIL_PROVIDER=resend but no key/from -> transport is None. The error
    # must point at RESEND_* vars, not the SMTP vars (the original bug).
    mgr = EmailManager(
        transport=None,
        sender_display_name="VisiChek",
        retry_attempts=1,
        retry_backoff_seconds=0.0,
        queue_enabled=False,
        provider="resend",
    )
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        await mgr.send_message(_msg())
