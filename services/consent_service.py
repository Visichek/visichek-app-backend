"""Visitor consent: enforcement + recording for the kiosk / public submit paths.

The kiosk multipart submit paths write to the ``checkins`` collection and have
no ``visit_sessions`` row, so consent is persisted into the dedicated
``consent_records`` collection (see ``repositories/consent_record_repo.py``) and
surfaced alongside session consent in GET /v1/compliance/consent-log.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from core.errors import consent_required
from repositories.privacy_notice_repo import get_active_notice_for_tenant
from schemas.consent_record_schema import ConsentRecordCreate
from schemas.imports import NoticeDisplayMode

logger = logging.getLogger(__name__)


def build_consent_payload(
    request,
    *,
    consent_granted: Optional[bool] = None,
    consent_method: Optional[str] = None,
    privacy_notice_id: Optional[str] = None,
    privacy_notice_version_id: Optional[str] = None,
    consent_accepted_at: Optional[int] = None,
) -> dict:
    """Assemble the consent dict threaded into the submit service functions.

    ``client_ip`` / ``user_agent`` are read from the request (never the body).
    """
    client_ip = None
    user_agent = None
    if request is not None:
        fwd = request.headers.get("X-Forwarded-For", "")
        client_ip = (fwd.split(",")[0].strip() if fwd else "") or (
            request.client.host if request.client else None
        )
        user_agent = request.headers.get("User-Agent")
    return {
        "consent_granted": consent_granted,
        "consent_method": consent_method,
        "privacy_notice_id": privacy_notice_id,
        "privacy_notice_version_id": privacy_notice_version_id,
        "consent_accepted_at": consent_accepted_at,
        "client_ip": client_ip,
        "user_agent": user_agent,
    }


async def enforce_consent_if_required(
    tenant_id: str,
    *,
    consent_granted: Optional[bool],
) -> None:
    """Reject a submit that lacks consent when the tenant's active notice
    requires it (display_mode == active_consent). Defence-in-depth behind the
    frontend gate (contract A.5).

    No active notice => nothing to require (do NOT hard-fail). A lookup failure
    fails OPEN — better to let the check-in through than to block the kiosk on
    a transient Mongo/Redis blip.
    """
    try:
        notice = await get_active_notice_for_tenant(tenant_id)
    except Exception:
        logger.warning(
            "consent enforcement: active-notice lookup failed tenant=%s (failing open)",
            tenant_id,
            exc_info=True,
        )
        return

    if notice is None:
        return

    display_mode = getattr(notice, "display_mode", None)
    if display_mode == NoticeDisplayMode.ACTIVE_CONSENT and not consent_granted:
        raise consent_required()


async def record_visitor_consent(
    *,
    tenant_id: str,
    consent_granted: Optional[bool],
    consent_method: Optional[str] = None,
    privacy_notice_id: Optional[str] = None,
    privacy_notice_version_id: Optional[str] = None,
    consent_accepted_at: Optional[int] = None,
    checkin_id: Optional[str] = None,
    session_id: Optional[str] = None,
    visitor_id: Optional[str] = None,
    visitor_name_snapshot: Optional[str] = None,
    department_id: Optional[str] = None,
    lawful_basis_at_time: Optional[object] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Persist a consent record. Fire-and-forget — a failure here must never
    roll back an otherwise-successful check-in. No-op when no consent signal
    was supplied (consent_granted is None and no notice version was echoed)."""
    if consent_granted is None and not privacy_notice_version_id:
        return
    try:
        from repositories.consent_record_repo import create_consent_record

        await create_consent_record(
            ConsentRecordCreate(
                tenant_id=tenant_id,
                checkin_id=checkin_id,
                session_id=session_id,
                visitor_id=visitor_id,
                visitor_name_snapshot=visitor_name_snapshot,
                department_id=department_id,
                privacy_notice_id=privacy_notice_id,
                privacy_notice_version_id=privacy_notice_version_id,
                consent_granted=bool(consent_granted),
                consent_method=consent_method,
                consent_timestamp=consent_accepted_at or int(time.time()),
                lawful_basis_at_time=lawful_basis_at_time,  # type: ignore[arg-type]
                client_ip=client_ip,
                user_agent=user_agent,
            )
        )
    except Exception:
        logger.warning(
            "record_visitor_consent failed tenant=%s checkin=%s",
            tenant_id,
            checkin_id,
            exc_info=True,
        )
