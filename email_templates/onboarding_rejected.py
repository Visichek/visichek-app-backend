"""Tenant onboarding rejection email.

Sent by ``services/onboarding_submission_service._queue_status_email``
after an application admin rejects a self-onboarding submission. Carries
the reviewer's notes so the applicant knows why and what to fix before
re-applying. Presentation is delegated entirely to ``_shell`` — the brand
layer shared across all VisiChek transactional emails.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "onboarding_rejected"
SUBJECT = "Update on your {platform_name} application"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    full_name = _safe(context, "full_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    review_notes = _safe(context, "review_notes", "")

    notes_block = (
        ui.panel(
            review_notes,
            variant="warning",
            title="Note from our team",
        )
        if review_notes
        else ""
    )

    content = (
        ui.eyebrow("Application update")
        + ui.heading(f"Hi {full_name},")
        + ui.paragraph(
            f"Thank you for applying to set up <strong>{organization_name}</strong> "
            f"on {platform_name}. After reviewing your submission, we are unable "
            "to approve it at this time."
        )
        + notes_block
        + ui.paragraph(
            "If you believe this was a mistake, or once you have addressed the "
            "points above, you are welcome to submit a new application."
        )
        + ui.muted(
            "Need help? Reply to this email or reach out to our support team "
            "and we'll walk you through it."
        )
    )

    return ui.page(
        content,
        preheader=f"An update on your {platform_name} application",
    )


def render_text(context: dict[str, Any]) -> str:
    full_name = _safe(context, "full_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    review_notes = _safe(context, "review_notes", "")

    lines = [
        f"Hi {full_name},",
        "",
        f"Thank you for applying to set up {organization_name} on "
        f"{platform_name}. After reviewing your submission, we are unable to "
        "approve it at this time.",
    ]
    if review_notes:
        lines.extend(["", f"Note from our team: {review_notes}"])
    lines.extend(
        [
            "",
            "If you believe this was a mistake, or once you have addressed the "
            "points above, you are welcome to submit a new application.",
            "",
            "Need help? Reply to this email or reach out to our support team.",
        ]
    )
    lines += ui.text_signoff()
    return "\n".join(lines)
