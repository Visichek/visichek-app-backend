"""Platform-wide security policy resolver.

Security policies (password rules, account lockout, session timeout, 2FA
enforcement) are owned by the platform and defined in code at
``config/platform_config.py``. Tenants do not configure these, and they are
no longer editable at runtime — changing one is a deploy.

This module is the single read path used by login, password validation,
JWT issuance, and 2FA enforcement so that one source of truth applies to
every account on the platform. The dataclass mirrors the security fields on
``PlatformConfig`` so callers can use it without importing the config module
directly, and the sync accessor exists because Pydantic validators cannot
``await``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SecurityPolicy:
    """Snapshot of the platform-wide security policy."""

    password_min_length: int = 8
    password_max_length: int = 128
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_number: bool = True
    password_require_special_char: bool = True
    password_expiry_days: Optional[int] = None
    password_history_count: int = 5

    max_failed_login_attempts: int = 5
    lockout_duration_minutes: int = 15

    session_timeout_minutes: int = 60

    enforce_totp_for_admins: bool = True
    enforce_totp_for_tenant_users: bool = False


def _build_policy() -> SecurityPolicy:
    """Build the policy snapshot from the static platform config."""
    from config.platform_config import get_platform_config

    cfg = get_platform_config()
    return SecurityPolicy(
        password_min_length=cfg.password_min_length,
        password_max_length=cfg.password_max_length,
        password_require_uppercase=cfg.password_require_uppercase,
        password_require_lowercase=cfg.password_require_lowercase,
        password_require_number=cfg.password_require_number,
        password_require_special_char=cfg.password_require_special_char,
        password_expiry_days=cfg.password_expiry_days,
        password_history_count=cfg.password_history_count,
        max_failed_login_attempts=cfg.max_failed_login_attempts,
        lockout_duration_minutes=cfg.lockout_duration_minutes,
        session_timeout_minutes=cfg.session_timeout_minutes,
        enforce_totp_for_admins=cfg.enforce_totp_for_admins,
        enforce_totp_for_tenant_users=cfg.enforce_totp_for_tenant_users,
    )


async def get_security_policy(force_refresh: bool = False) -> SecurityPolicy:
    """Return the active platform security policy from code config.

    The ``force_refresh`` argument is retained for backwards compatibility
    with callers from the era when this read from MongoDB; the policy is now
    static so it is ignored.
    """
    return _build_policy()


def get_security_policy_sync() -> SecurityPolicy:
    """Return the policy without awaiting — for use in Pydantic validators."""
    return _build_policy()


def invalidate_security_policy_cache() -> None:
    """No-op retained for backwards compatibility.

    The policy is sourced from code config and has no runtime cache to drop.
    """
    return None
