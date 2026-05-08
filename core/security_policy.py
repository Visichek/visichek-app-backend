"""Platform-wide security policy resolver with in-memory cache.

Security policies (password rules, account lockout, session timeout, 2FA
enforcement) are owned by the platform admin and stored in the
``platform_settings`` singleton. Tenants do not configure these. This
module is the single read path used by login, password validation,
JWT issuance, and 2FA enforcement so that one source of truth applies
to every account on the platform.

The dataclass mirrors the security fields on ``PlatformSettingsOut`` so
callers can use it without importing the schema; the cache exists
because schemas cannot ``await`` and login is on a hot path.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60


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


_DEFAULT_POLICY = SecurityPolicy()
_cached_policy: Optional[SecurityPolicy] = None
_cached_at: float = 0.0


def _build_from_settings(settings: object) -> SecurityPolicy:
    """Map a PlatformSettingsOut to a SecurityPolicy snapshot."""
    return SecurityPolicy(
        password_min_length=getattr(settings, "password_min_length", 8),
        password_max_length=getattr(settings, "password_max_length", 128),
        password_require_uppercase=getattr(
            settings, "password_require_uppercase", True
        ),
        password_require_lowercase=getattr(
            settings, "password_require_lowercase", True
        ),
        password_require_number=getattr(settings, "password_require_number", True),
        password_require_special_char=getattr(
            settings, "password_require_special_char", True
        ),
        password_expiry_days=getattr(settings, "password_expiry_days", None),
        password_history_count=getattr(settings, "password_history_count", 5),
        max_failed_login_attempts=getattr(settings, "max_failed_login_attempts", 5),
        lockout_duration_minutes=getattr(settings, "lockout_duration_minutes", 15),
        session_timeout_minutes=getattr(settings, "session_timeout_minutes", 60),
        enforce_totp_for_admins=getattr(settings, "enforce_totp_for_admins", True),
        enforce_totp_for_tenant_users=getattr(
            settings, "enforce_totp_for_tenant_users", False
        ),
    )


async def get_security_policy(force_refresh: bool = False) -> SecurityPolicy:
    """Return the active platform security policy.

    Cached for ``_CACHE_TTL_SECONDS``. On any DB error we fall back to
    the in-memory cached value or the dataclass defaults — never raise,
    because every login and every password change path depends on this.
    """
    global _cached_policy, _cached_at

    now = time.time()
    if (
        not force_refresh
        and _cached_policy is not None
        and (now - _cached_at) < _CACHE_TTL_SECONDS
    ):
        return _cached_policy

    try:
        from repositories.platform_settings_repo import get_platform_settings

        settings = await get_platform_settings()
        if settings is not None:
            _cached_policy = _build_from_settings(settings)
        elif _cached_policy is None:
            _cached_policy = _DEFAULT_POLICY
    except Exception:
        logger.warning(
            "security_policy: failed to load platform settings, using cached/default",
            exc_info=True,
        )
        if _cached_policy is None:
            _cached_policy = _DEFAULT_POLICY

    _cached_at = now
    return _cached_policy


def get_security_policy_sync() -> SecurityPolicy:
    """Return the cached policy without touching the DB.

    Used by Pydantic validators, which cannot ``await``. If the cache
    has not been primed yet (cold process, no login traffic) callers
    get the conservative defaults from ``SecurityPolicy()``.
    """
    return _cached_policy or _DEFAULT_POLICY


def invalidate_security_policy_cache() -> None:
    """Drop the cached snapshot — next read repopulates from MongoDB."""
    global _cached_policy, _cached_at
    _cached_policy = None
    _cached_at = 0.0
