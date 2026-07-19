"""Tests for the non-production test-email bypass (core/test_mode.py).

The bypass must:
* match only the configured test domains (default ``visichek.test``),
* be a hard no-op when ``ENV=production``,
* hand test accounts the fixed ``test_temp_password`` (which must itself
  satisfy the password policy) while everyone else still gets a random
  policy-compliant value,
* make ``EmailManager`` skip sends to test addresses.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from core.settings import get_settings
from core.test_mode import is_test_email, issue_temp_password


def _settings(env: str = "testing", domains: tuple[str, ...] = ("visichek.test",)):
    return replace(get_settings(), env=env, test_email_domains=domains)


class TestIsTestEmail:
    def test_matches_test_domain_outside_production(self):
        with patch("core.test_mode.get_settings", return_value=_settings()):
            assert is_test_email("owner@visichek.test") is True

    def test_matches_subdomain_of_test_domain(self):
        with patch("core.test_mode.get_settings", return_value=_settings()):
            assert is_test_email("invitee@tenant-a.visichek.test") is True

    def test_is_case_insensitive(self):
        with patch("core.test_mode.get_settings", return_value=_settings()):
            assert is_test_email("Owner@VisiChek.TEST") is True

    def test_rejects_normal_domains(self):
        with patch("core.test_mode.get_settings", return_value=_settings()):
            assert is_test_email("someone@gmail.com") is False

    def test_rejects_lookalike_suffix_without_dot_boundary(self):
        with patch("core.test_mode.get_settings", return_value=_settings()):
            assert is_test_email("x@evilvisichek.test.com") is False
            assert is_test_email("x@notvisichek.test") is False

    def test_rejects_empty_and_malformed(self):
        with patch("core.test_mode.get_settings", return_value=_settings()):
            assert is_test_email(None) is False
            assert is_test_email("") is False
            assert is_test_email("no-at-sign") is False

    def test_hard_false_in_production(self):
        with patch(
            "core.test_mode.get_settings",
            return_value=_settings(env="production"),
        ):
            assert is_test_email("owner@visichek.test") is False

    def test_custom_domains_from_settings(self):
        with patch(
            "core.test_mode.get_settings",
            return_value=_settings(domains=("qa.example",)),
        ):
            assert is_test_email("a@qa.example") is True
            assert is_test_email("a@visichek.test") is False


class TestIssueTempPassword:
    def test_test_account_gets_fixed_password(self):
        settings = _settings()
        with patch("core.test_mode.get_settings", return_value=settings):
            assert issue_temp_password("owner@visichek.test") == (
                settings.test_temp_password
            )

    def test_normal_account_gets_random_policy_password(self):
        with patch("core.test_mode.get_settings", return_value=_settings()):
            generated = issue_temp_password("someone@gmail.com")
        assert generated != get_settings().test_temp_password
        # Two calls must differ — the value is random, not fixed.
        with patch("core.test_mode.get_settings", return_value=_settings()):
            assert issue_temp_password("someone@gmail.com") != generated

    def test_production_never_gets_fixed_password(self):
        settings = _settings(env="production")
        with patch("core.test_mode.get_settings", return_value=settings):
            assert issue_temp_password("owner@visichek.test") != (
                settings.test_temp_password
            )

    def test_default_fixed_password_satisfies_policy(self):
        from security.password_policy import validate_password_strength

        # Raises on violation — the fixed value must always be loginable.
        validate_password_strength(get_settings().test_temp_password)


class TestEmailSuppression:
    @pytest.mark.asyncio
    async def test_send_template_skips_test_recipient(self):
        from core.email.manager import EmailManager
        from core.email.types import EmailDispatchRequest

        manager = EmailManager(
            transport=None,
            sender_display_name="VisiChek",
            retry_attempts=1,
            retry_backoff_seconds=0.0,
            queue_enabled=True,
        )
        with patch("core.email.manager.is_test_email", return_value=True):
            result = await manager.send_template(
                EmailDispatchRequest(
                    to_email="owner@visichek.test",
                    template_key="onboarding_accepted",
                )
            )
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_send_message_skips_test_recipient_before_transport_check(self):
        from core.email.manager import EmailManager
        from core.email.types import EmailMessage

        # transport=None would raise RuntimeError if the gate didn't fire
        # first, so a "skipped" result proves the suppression path ran.
        manager = EmailManager(
            transport=None,
            sender_display_name="VisiChek",
            retry_attempts=1,
            retry_backoff_seconds=0.0,
            queue_enabled=False,
        )
        with patch("core.email.manager.is_test_email", return_value=True):
            result = await manager.send_message(
                EmailMessage(
                    to_email="owner@visichek.test",
                    subject="s",
                    html_body="<p>h</p>",
                    text_body="t",
                    sender_display_name="VisiChek",
                )
            )
        assert result.status == "skipped"
