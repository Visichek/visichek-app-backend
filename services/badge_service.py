from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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

    # Check if expired
    now = int(time.time())
    if badge.expires_at <= now:
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
