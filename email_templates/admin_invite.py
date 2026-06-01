"""Application-admin invitation email.

Sent by ``services/admin_service.add_admin`` whenever an existing
application admin invites a new platform operator. Carries the
sign-in URL, the temporary password the inviter chose, and a clear
nudge that 2FA is mandatory on first login. Presentation is
delegated entirely to the shared ``_shell`` brand layer.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "admin_invite"
SUBJECT = "You're invited to manage {platform_name}"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def render_html(context: dict[str, Any]) -> str:
    invitee_name = _safe(context, "invitee_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    inviter_name = _safe(context, "inviter_name", "An administrator")
    email = _safe(context, "email", "")
    temp_password = _safe(context, "temp_password", "")
    access_label = _safe(context, "access_label", "platform admin")
    login_url = _safe(context, "login_url", "")

    content = (
        ui.eyebrow("Platform access")
        + ui.heading(f"Welcome to {platform_name}, {invitee_name}.")
        + ui.paragraph(
            f"{inviter_name} has invited you to help administer the "
            f"{platform_name} platform with the <strong>{access_label}</strong> role."
        )
        + ui.cred_card([
            ("Sign-in email", email, False),
            ("Temporary password", temp_password, True),
        ])
        + ui.paragraph("For your first sign-in:")
        + ui.ordered_steps([
            "Use the temporary password above.",
            "You will be asked for a 6-digit verification code sent to this inbox "
            "— that is two-factor authentication, and it is required on every login.",
            "Change your password from <em>Settings → Account</em> right after you land.",
        ])
        + ui.button(f"Sign in to {platform_name}", login_url)
        + ui.fallback_link(login_url)
        + ui.muted(
            "If you weren't expecting this invitation, you can ignore the email — "
            "the account is unusable until someone signs in with the temporary password."
        )
    )

    return ui.page(
        content,
        preheader="You've been invited to manage VisiChek",
    )


def render_text(context: dict[str, Any]) -> str:
    invitee_name = _safe(context, "invitee_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    inviter_name = _safe(context, "inviter_name", "An administrator")
    email = _safe(context, "email", "")
    temp_password = _safe(context, "temp_password", "")
    access_label = _safe(context, "access_label", "platform admin")
    login_url = _safe(context, "login_url", "")

    lines = [
        f"Welcome to {platform_name}, {invitee_name}.",
        "",
        f"{inviter_name} has invited you to help administer the {platform_name} "
        f"platform with the {access_label} role.",
        "",
        f"Sign-in email: {email}",
        f"Temporary password: {temp_password}",
        "",
        "First sign-in:",
        "  1. Use the temporary password above.",
        "  2. You'll be asked for a 6-digit code sent to this inbox — that is "
        "two-factor authentication and is required on every login.",
        "  3. Change your password from Settings -> Account right after you land.",
    ]
    if login_url:
        lines.extend(["", f"Sign in: {login_url}"])
    lines.extend(
        [
            "",
            "If you weren't expecting this invitation you can ignore the email — "
            "the account is unusable until someone signs in with the temporary "
            "password.",
        ]
    )
    lines.extend(
        ui.text_signoff()
    )
    return "\n".join(lines)
