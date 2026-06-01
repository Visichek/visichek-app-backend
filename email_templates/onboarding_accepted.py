"""Tenant onboarding acceptance email.

Sent by ``services/onboarding_submission_service._queue_status_email``
after an application admin accepts a self-onboarding submission. Carries
the sign-in email + temporary password the system generated (or the
password the admin chose during approval) plus the tenant login URL.
Presentation is delegated entirely to ``_shell`` — the brand layer shared
across all VisiChek transactional emails.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "onboarding_accepted"
SUBJECT = "Your {platform_name} workspace is ready"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    full_name = _safe(context, "full_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    email = _safe(context, "admin_email", "")
    temp_password = _safe(context, "temp_password", "")
    login_url = _safe(context, "login_url", "")
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

    creds_block = (
        ui.cred_card(
            [
                ("Sign-in email", email, False),
                ("Temporary password", temp_password, True),
            ]
        )
        if temp_password
        else ""
    )

    content = (
        ui.eyebrow("Workspace ready")
        + ui.heading(f"Welcome to {platform_name}, {full_name}.")
        + ui.paragraph(
            f"Your application to set up <strong>{organization_name}</strong> on "
            f"{platform_name} has been approved. "
            "You can sign in below as the super admin for your workspace."
        )
        + notes_block
        + creds_block
        + ui.paragraph("For your first sign-in:")
        + ui.ordered_steps(
            [
                "Use the temporary password above.",
                "Change your password from <em>Settings &rarr; Account</em> right after you land.",
                "Invite your team and configure your branches, departments, and check-in flow.",
            ]
        )
        + ui.button(f"Sign in to {platform_name}", login_url)
        + ui.fallback_link(login_url)
        + ui.muted(
            "You are currently on the Free plan — every workspace starts there. "
            "You can upgrade to a paid plan from <em>Settings &rarr; Billing</em> at any time."
        )
    )

    return ui.page(
        content,
        preheader="Your VisiChek workspace is ready",
    )


def render_text(context: dict[str, Any]) -> str:
    full_name = _safe(context, "full_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    email = _safe(context, "admin_email", "")
    temp_password = _safe(context, "temp_password", "")
    login_url = _safe(context, "login_url", "")
    review_notes = _safe(context, "review_notes", "")

    lines = [
        f"Welcome to {platform_name}, {full_name}.",
        "",
        f"Your application to set up {organization_name} on {platform_name} "
        "has been approved. You can sign in as the super admin for your "
        "workspace.",
    ]
    if review_notes:
        lines.extend(["", f"Note from our team: {review_notes}"])
    if temp_password:
        lines.extend(
            [
                "",
                f"Sign-in email: {email}",
                f"Temporary password: {temp_password}",
            ]
        )
    lines.extend(
        [
            "",
            "First sign-in:",
            "  1. Use the temporary password above.",
            "  2. Change your password from Settings -> Account right after you land.",
            "  3. Invite your team and configure your branches, departments, and check-in flow.",
        ]
    )
    if login_url:
        lines.extend(["", f"Sign in: {login_url}"])
    lines.extend(
        [
            "",
            "You are currently on the Free plan -- every workspace starts there. "
            "You can upgrade to a paid plan from Settings -> Billing at any time.",
        ]
    )
    lines += ui.text_signoff()
    return "\n".join(lines)
