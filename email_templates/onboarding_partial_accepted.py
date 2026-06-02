"""Tenant onboarding partial-acceptance email.

Sent when the application admin approves the submission but flags some
payload fields as missing or unsatisfactory. The newly-provisioned
super_admin must fill those in via the self-completion endpoint before
the tenant is considered fully onboarded.

Mirrors ``onboarding_accepted.py`` and adds the pending-fields panel.
Presentation is delegated to ``_shell`` — the brand layer that owns all
colours, typography, and layout so this module stays pure content logic.
"""

from __future__ import annotations

from typing import Any, Iterable

from email_templates import _shell as ui

TEMPLATE_KEY = "onboarding_partial_accepted"
SUBJECT = "Your {platform_name} workspace is ready — a few details to finish"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def _pending_labels(context: dict[str, Any]) -> list[str]:
    labels = context.get("pending_field_labels")
    if isinstance(labels, dict) and labels:
        return [str(v) for v in labels.values() if v]
    keys = context.get("pending_field_keys")
    if isinstance(keys, Iterable):
        return [str(k) for k in keys if k]
    return []


def render_html(context: dict[str, Any]) -> str:
    full_name = _safe(context, "full_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    email = _safe(context, "admin_email", "")
    temp_password = _safe(context, "temp_password", "")
    login_url = _safe(context, "login_url", "")
    review_notes = _safe(context, "review_notes", "")
    pending = _pending_labels(context)

    notes_block = (
        ui.panel(
            f"<strong>Note from our team:</strong><br>{review_notes}",
            variant="warning",
        )
        if review_notes
        else ""
    )

    creds_rows = [("Sign-in email", email, False)]
    if temp_password:
        creds_rows.append(("Temporary password", temp_password, True))
    creds_block = ui.cred_card(creds_rows) if temp_password else ""

    pending_block = (
        ui.panel(
            ui.bullet_list(pending),
            title="Details we still need from you",
        )
        if pending
        else ""
    )

    content = (
        ui.eyebrow("Workspace ready")
        + ui.heading(f"Welcome to {platform_name}, {full_name}.")
        + ui.paragraph(
            f"Your application to set up <strong>{organization_name}</strong> on "
            f"{platform_name} has been approved, with a short list of details to "
            "finish before your workspace is complete."
        )
        + notes_block
        + creds_block
        + pending_block
        + ui.paragraph("For your first sign-in:")
        + ui.ordered_steps(
            [
                "Use the temporary password above.",
                "Change your password from <em>Settings &rarr; Account</em>.",
                "Open the onboarding card on your dashboard to complete the remaining fields.",
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
        preheader="Your VisiChek workspace is ready — a few details to finish",
    )


def render_text(context: dict[str, Any]) -> str:
    full_name = _safe(context, "full_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    organization_name = _safe(context, "organization_name", "your organization")
    email = _safe(context, "admin_email", "")
    temp_password = _safe(context, "temp_password", "")
    login_url = _safe(context, "login_url", "")
    review_notes = _safe(context, "review_notes", "")
    pending = _pending_labels(context)

    lines = [
        f"Welcome to {platform_name}, {full_name}.",
        "",
        f"Your application to set up {organization_name} on {platform_name} "
        "has been approved, with a short list of details to finish before "
        "your workspace is complete.",
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
    if pending:
        lines.extend(
            ["", "Details we still need from you:"]
            + [f"  - {label}" for label in pending]
        )
    lines.extend(
        [
            "",
            "First sign-in:",
            "  1. Use the temporary password above.",
            "  2. Change your password from Settings -> Account.",
            "  3. Open the onboarding card on your dashboard to complete the remaining fields.",
        ]
    )
    if login_url:
        lines.extend(["", f"Sign in: {login_url}"])
    lines.extend(
        [
            "",
            "You are currently on the Free plan — every workspace starts there. "
            "You can upgrade to a paid plan from Settings -> Billing at any time.",
        ]
    )
    lines.extend(ui.text_signoff())
    return "\n".join(lines)
