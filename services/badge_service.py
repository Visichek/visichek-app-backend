from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def resolve_badge_expiry(
    tenant_id: str, *, now: Optional[int] = None
) -> Optional[int]:
    """Resolve the badge expiry timestamp from the tenant's settings.

    Honours ``tenant_settings.visitor_badge_expiry`` (previously inert —
    both issuance paths hardcoded their expiry):

    * ``end_of_day`` → next UTC midnight. Kept as **UTC** end-of-day for
      now — computing the tenant-local day from ``default_timezone`` is a
      known gap carried over from the original hardcoded behaviour.
    * ``hours``      → ``now + visitor_badge_expiry_hours * 3600``. Falls
      back to end-of-day when the hours value is missing/invalid (the
      schema validator normally prevents that combination).
    * ``manual``     → ``None`` — no auto-expiry; the badge stays valid
      until the visit is checked out or the badge is revoked.

    Defaults to end-of-day (the schema default) when settings are missing
    or unreadable. Never raises — badge issuance must not fail on a
    settings-read hiccup.
    """
    import time as _time

    from schemas.tenant_settings_schema import VisitorBadgeExpiry

    ts = int(now) if now is not None else int(_time.time())

    mode_value = VisitorBadgeExpiry.END_OF_DAY.value
    hours: Optional[int] = None
    try:
        from repositories.tenant_settings_repo import get_tenant_settings

        settings = await get_tenant_settings({"tenant_id": tenant_id})
        if settings is not None:
            raw_mode = getattr(settings, "visitor_badge_expiry", None)
            if raw_mode:
                mode_value = getattr(raw_mode, "value", raw_mode)
            hours = getattr(settings, "visitor_badge_expiry_hours", None)
    except Exception:
        logger.warning(
            "resolve_badge_expiry: failed to load tenant settings tenant_id=%s "
            "— defaulting to end_of_day",
            tenant_id,
            exc_info=True,
        )

    if mode_value == VisitorBadgeExpiry.MANUAL.value:
        return None
    if mode_value == VisitorBadgeExpiry.HOURS.value and hours and hours > 0:
        return ts + int(hours) * 3600
    # END_OF_DAY (and the hours-without-a-value fallback): next UTC midnight.
    return ((ts // 86400) + 1) * 86400


async def validate_badge(qr_code_value: str):
    """Validate a badge by QR code value.

    Returns:
        BadgeValidationResponse with valid=True and badge details, or
        valid=False with a reason code.
    """
    from repositories.badge_repo import get_badge_by_qr_value
    from schemas.badge_schema import BadgeValidationResponse, BadgeValidationReason
    import time

    badge = await get_badge_by_qr_value(qr_code_value)

    if not badge:
        return BadgeValidationResponse(
            valid=False,
            reason=BadgeValidationReason.NOT_FOUND,
        )

    # Check if revoked
    if badge.revoked_at is not None:
        return BadgeValidationResponse(
            valid=False,
            reason=BadgeValidationReason.REVOKED,
        )

    # Check if expired. ``expires_at`` is None when the org's badge-expiry
    # policy is MANUAL — such badges never auto-expire (they stay valid
    # until the visit checks out or the badge is revoked).
    now = int(time.time())
    if badge.expires_at is not None and badge.expires_at <= now:
        return BadgeValidationResponse(
            valid=False,
            reason=BadgeValidationReason.EXPIRED,
        )

    # Valid badge - fetch details
    from repositories.checkin_repo import get_checkin
    from repositories.visitor_repo import get_visitor

    checkin = await get_checkin({"_id": badge.checkin_id})
    visitor = await get_visitor({"_id": checkin.visitor_id}) if checkin else None

    return BadgeValidationResponse(
        valid=True,
        badge_id=badge.id,
        qr_code_value=badge.qr_code_value,
        visitor_name=visitor.full_name if visitor else None,
        verified=visitor.verified if visitor else False,
        portrait_url=visitor.portrait_url if visitor else None,
        host_employee_name=None,  # TODO: resolve if context available
        purpose=checkin.purpose.purpose if checkin else None,
        issued_at=badge.issued_at,
        expires_at=badge.expires_at,
    )
