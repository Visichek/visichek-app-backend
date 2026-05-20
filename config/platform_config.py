"""Static platform configuration — single source of truth for settings
that are set ONCE at deploy time and almost never change at runtime.

Historically these lived in the ``platform_settings`` MongoDB singleton and
were editable through ``GET/PATCH /v1/platform-settings``. That exposed
security-critical knobs (password policy, lockout thresholds, session
timeout, 2FA enforcement) and operational plumbing (SMTP, rate limits,
tenant defaults, feature flags) to a runtime admin UI even though they
should be reviewed in code and shipped through a deploy.

They now live here, version-controlled, alongside ``role_permissions.py``
and ``plan_tiers.py``. To change one, edit this file and redeploy. The ONLY
platform setting still editable at runtime is ``maintenance_mode`` (+ its
message), which is gated behind an OTP challenge — see
``services/platform_settings_service.py``.

Read access:
* Security policy fields are consumed via ``core.security_policy`` (login,
  password validation, JWT issuance, 2FA enforcement).
* ``self_onboarding_enabled`` gates the public onboarding submission endpoint.
* The remaining fields are informational defaults for the platform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Dict, Optional


class SmtpEncryption(str, Enum):
    TLS = "tls"
    SSL = "ssl"
    NONE = "none"


@dataclass(frozen=True)
class PlatformConfig:
    """Frozen, deploy-time platform configuration.

    Edit the defaults below and redeploy to change any value. Nothing here
    is editable through the API.
    """

    # ── General ──────────────────────────────────────────────────────────
    platform_name: str = "VisiChek"
    support_email: Optional[str] = None
    support_phone: Optional[str] = None
    platform_url: Optional[str] = None

    # ── Password Policy (platform-wide) ──────────────────────────────────
    password_min_length: int = 8
    password_max_length: int = 128
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_number: bool = True
    password_require_special_char: bool = True
    password_expiry_days: Optional[int] = None
    password_history_count: int = 5

    # ── Account Lockout (platform-wide) ──────────────────────────────────
    max_failed_login_attempts: int = 5
    lockout_duration_minutes: int = 15

    # ── Session (platform-wide) ──────────────────────────────────────────
    session_timeout_minutes: int = 60

    # ── Two-Factor Authentication (platform-wide) ────────────────────────
    enforce_totp_for_admins: bool = True
    enforce_totp_for_tenant_users: bool = False

    # ── Admin Account Limits ─────────────────────────────────────────────
    max_admin_accounts: Optional[int] = None

    # ── Tenant Defaults ──────────────────────────────────────────────────
    default_trial_days: int = 14
    default_plan_id: Optional[str] = None
    max_tenants_per_plan: Dict[str, Optional[int]] = field(default_factory=dict)

    # ── Email & SMTP ─────────────────────────────────────────────────────
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_from_name: Optional[str] = None
    smtp_encryption: SmtpEncryption = SmtpEncryption.TLS

    # ── Feature Flags (maintenance_mode is NOT here — it stays runtime) ───
    signups_enabled: bool = True
    public_api_enabled: bool = True
    beta_features_enabled: bool = False
    # Self-onboarding (public marketing-site form). When False the public
    # POST /v1/onboarding/submissions endpoint returns 403 self_onboarding_disabled.
    self_onboarding_enabled: bool = True

    # ── Rate Limiting Defaults ───────────────────────────────────────────
    global_rate_limit_per_minute: int = 80
    global_rate_limit_burst: int = 20

    def __post_init__(self) -> None:
        # Fail fast at import time if a bad value is shipped in code — these
        # are the same invariants the old runtime validator enforced.
        if self.password_min_length < 8:
            raise ValueError("password_min_length must be at least 8")
        if self.password_min_length > self.password_max_length:
            raise ValueError("password_min_length cannot exceed password_max_length")
        if self.password_max_length > 256:
            raise ValueError("password_max_length must be at most 256")
        if not (0 <= self.password_history_count <= 24):
            raise ValueError("password_history_count must be between 0 and 24")
        if self.max_failed_login_attempts < 1:
            raise ValueError("max_failed_login_attempts must be at least 1")
        if self.lockout_duration_minutes < 1:
            raise ValueError("lockout_duration_minutes must be at least 1")
        if self.session_timeout_minutes < 5:
            raise ValueError("session_timeout_minutes must be at least 5")
        if self.password_expiry_days is not None and self.password_expiry_days < 1:
            raise ValueError("password_expiry_days must be at least 1 if set")


# The active platform configuration. Edit fields above and redeploy.
PLATFORM_CONFIG = PlatformConfig()


@lru_cache(maxsize=1)
def get_platform_config() -> PlatformConfig:
    """Return the frozen platform configuration singleton."""
    return PLATFORM_CONFIG
