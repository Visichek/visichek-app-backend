"""Tenant onboarding completion email.

Sent by ``services/onboarding_submission_service._queue_status_email``
after a partially-accepted tenant supplies the last pending fields and
their submission transitions to COMPLETED. Confirms the workspace is
fully set up and points the super admin back at the sign-in page.
Presentation is delegated entirely to ``_shell`` — the brand layer shared
across all VisiChek transactional emails.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "onboarding_completed"
SUBJECT = "Your {platform_name} workspace setup is complete"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    full_name = _safe(context, "full_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    login_url = _safe(context, "login_url", "")

    content = (
        ui.eyebrow("Setup complete")
        + ui.heading(f"You're all set, {full_name}.")
        + ui.paragraph(
            f"Thanks for completing the remaining details for "
            f"<strong>{organization_name}</strong>. Your {platform_name} "
            "workspace is now fully set up — nothing else is pending on "
            "your application."
        )
        + ui.paragraph(
            "You can keep configuring your workspace at any time: invite your "
            "team, set up branches and departments, and tailor the visitor "
            "check-in flow."
        )
        + (ui.button(f"Sign in to {platform_name}", login_url) if login_url else "")
        + (ui.fallback_link(login_url) if login_url else "")
        + ui.muted(
            "You are currently on the Free plan — you can upgrade from "
            "<em>Settings &rarr; Billing</em> whenever you're ready."
        )
    )

    return ui.page(
        content,
        preheader=f"Your {platform_name} workspace setup is complete",
    )


def render_text(context: dict[str, Any]) -> str:
    full_name = _safe(context, "full_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    login_url = _safe(context, "login_url", "")

    lines = [
        f"You're all set, {full_name}.",
        "",
        f"Thanks for completing the remaining details for {organization_name}. "
        f"Your {platform_name} workspace is now fully set up -- nothing else "
        "is pending on your application.",
        "",
        "You can keep configuring your workspace at any time: invite your "
        "team, set up branches and departments, and tailor the visitor "
        "check-in flow.",
    ]
    if login_url:
        lines.extend(["", f"Sign in: {login_url}"])
    lines.extend(
        [
            "",
            "You are currently on the Free plan -- you can upgrade from "
            "Settings -> Billing whenever you're ready.",
        ]
    )
    lines += ui.text_signoff()
    return "\n".join(lines)
