"""Test-email bypass helpers (NON-PRODUCTION ONLY).

An email address whose domain matches ``settings.test_email_domains``
(default ``visichek.test`` — an RFC 2606 reserved TLD that can never
receive real mail) identifies a *test account* used by automated E2E
runs. For these accounts, and only when ``ENV`` is not ``production``:

* outbound email is suppressed (``EmailManager`` logs and skips),
* Turnstile verification on the public onboarding form is skipped,
* system-generated temporary passwords are the fixed
  ``settings.test_temp_password`` instead of a random value, so an
  automated test can log in without reading a mailbox.

The 2FA/OTP side needs no helper here: ``services.otp_service`` already
issues the static ``OTP_DEV_CODE`` for every non-production environment.

Every helper below is a hard no-op in production regardless of
configuration — ``is_test_email`` returns False when
``settings.is_production``, and everything else keys off it.
"""

from __future__ import annotations

from core.settings import get_settings


def is_test_email(email: str | None) -> bool:
    """True when ``email`` belongs to a configured test domain AND the
    current environment is not production."""
    if not email or "@" not in email:
        return False
    settings = get_settings()
    if settings.is_production:
        return False
    domain = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    if not domain:
        return False
    return any(
        domain == test_domain or domain.endswith("." + test_domain)
        for test_domain in settings.test_email_domains
    )


def issue_temp_password(email: str | None) -> str:
    """System-generated temp password for an account-creation / reset path.

    Test accounts (non-production only) get the fixed
    ``settings.test_temp_password`` so E2E scripts can log in without
    intercepting the welcome email; everyone else gets the usual random
    policy-compliant value from ``generate_secure_temp_password``.
    """
    from security.password_policy import generate_secure_temp_password

    if is_test_email(email):
        return get_settings().test_temp_password
    return generate_secure_temp_password()
