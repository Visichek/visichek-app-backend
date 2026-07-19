"""Billing dunning email — payment retries and the final Free downgrade.

One template serves every dunning stage; the stage-specific copy
(``subject_line``, ``title``, ``body``) is computed in
``services/dunning_service._queue_dunning_email``. This replaces the old
path that enqueued a ``services.email_service:send_dunning_email`` task
which never existed, so no dunning email was ever delivered.

Presentation is delegated entirely to ``_shell`` — the brand layer shared
across all VisiChek transactional emails.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "billing_dunning"
# The manager formats SUBJECT with the send context, so the stage copy
# supplies the real subject line.
SUBJECT = "{subject_line}"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    title = _safe(context, "title", "Payment update for your subscription")
    body = _safe(context, "body", "")
    billing_url = _safe(context, "billing_url", "")

    content = (
        ui.eyebrow("Billing")
        + ui.heading(f"Hi {recipient_name},")
        + ui.paragraph(
            f"This is a billing update for <strong>{organization_name}</strong> "
            f"on {platform_name}."
        )
        + ui.panel(body, variant="warning", title=title)
        + ui.paragraph(
            "To keep your subscription active, review your payment method "
            "and settle the outstanding balance from your billing page."
        )
        + (ui.button("Review billing", billing_url) if billing_url else "")
        + (ui.fallback_link(billing_url) if billing_url else "")
        + ui.muted(
            "If you've already updated your payment details, you can ignore "
            "this message — the next automatic retry will pick it up."
        )
    )

    return ui.page(content, preheader=title)


def render_text(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    title = _safe(context, "title", "Payment update for your subscription")
    body = _safe(context, "body", "")
    billing_url = _safe(context, "billing_url", "")

    lines = [
        f"Hi {recipient_name},",
        "",
        f"This is a billing update for {organization_name} on {platform_name}.",
        "",
        title,
        body,
        "",
        "To keep your subscription active, review your payment method and "
        "settle the outstanding balance from your billing page.",
    ]
    if billing_url:
        lines.extend(["", f"Review billing: {billing_url}"])
    lines.extend(
        [
            "",
            "If you've already updated your payment details, you can ignore "
            "this message -- the next automatic retry will pick it up.",
        ]
    )
    lines += ui.text_signoff()
    return "\n".join(lines)
