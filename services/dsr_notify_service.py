"""DSR requester notifications — keep the data subject informed as their
request moves through its workflow.

A DSR's recipients are the **requester email** captured when the request was
raised PLUS the **linked visitor profile's email**, de-duplicated. The same
recipient set is used by the access-export delivery (see
``dsr_access_export_service``) so a subject always hears back on every address
we have for them.

``notify_dsr_status`` fires on the in-progress and completed shifts. It is
best-effort: a missing transport, missing email, or a send failure is logged
and swallowed — a status flip must never roll back because an email bounced.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from core.email.types import EmailDispatchRequest
from schemas.data_subject_request_schema import DSROut

logger = logging.getLogger(__name__)


def _valid_email(value: Optional[str]) -> bool:
    return bool(value) and "@" in (value or "")


async def resolve_dsr_recipients(
    dsr: DSROut,
) -> Tuple[List[str], Optional[str], Optional[str]]:
    """Return ``(recipient_emails, visitor_name, visitor_email)`` for a DSR.

    Recipients = the requester email (first) + the linked visitor profile's
    email, both validated and de-duplicated. Requester-first so the person who
    actually raised the request leads the ``To`` line.
    """
    from services.summary_resolver import resolve_visitor_profile_summary

    summary = await resolve_visitor_profile_summary(dsr.visitor_profile_id)
    visitor_email = summary.email_address if summary else None
    visitor_name = summary.full_name if summary else None

    recipients: List[str] = []
    seen: set[str] = set()
    for email in (dsr.requester_email, visitor_email):
        if _valid_email(email) and email not in seen:
            seen.add(email)  # type: ignore[arg-type]
            recipients.append(email)  # type: ignore[arg-type]
    return recipients, visitor_name, visitor_email


async def _resolve_tenant_name(tenant_id: str) -> str:
    try:
        from services.tenant_service import retrieve_tenant_by_id

        tenant = await retrieve_tenant_by_id(tenant_id)
        return getattr(tenant, "company_name", None) or "VisiChek"
    except Exception:
        return "VisiChek"


def _request_type_label(dsr: DSROut) -> str:
    rt = dsr.request_type
    raw = rt.value if hasattr(rt, "value") else str(rt)
    return raw.replace("_", " ")


async def notify_dsr_status(dsr: DSROut, kind: str) -> None:
    """Email the requester + linked visitor that the DSR moved to ``kind``.

    ``kind`` is ``"in_progress"`` or ``"completed"``. Best-effort: never raises.
    """
    if kind not in ("in_progress", "completed"):
        return
    try:
        recipients, visitor_name, _ = await resolve_dsr_recipients(dsr)
        if not recipients:
            return
        tenant_name = await _resolve_tenant_name(dsr.tenant_id)
        name = visitor_name or dsr.requester_name or "there"
        template = "dsr_in_progress" if kind == "in_progress" else "dsr_completed"
        context = {
            "recipient_name": name,
            "tenant_name": tenant_name,
            "request_type": _request_type_label(dsr),
            "resolution": dsr.resolution or "",
        }

        from core.email.manager import EmailManager

        manager = EmailManager.get_instance()
        for email in recipients:
            try:
                await manager.send_template(
                    EmailDispatchRequest(
                        to_email=email,
                        template_key=template,
                        context=context,
                        dispatch="auto",
                    )
                )
            except Exception:
                logger.warning(
                    "dsr notify: send failed to=%s kind=%s", email, kind, exc_info=True
                )
    except Exception:
        logger.warning(
            "dsr notify: failed dsr=%s kind=%s",
            getattr(dsr, "id", None),
            kind,
            exc_info=True,
        )
