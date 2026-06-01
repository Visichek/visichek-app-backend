"""Authority-driven password reset notification.

Sent by ``services/password_change_service.reset_system_user_password_by_authority``
when an application admin (or tenant super_admin) triggers a password
reset on someone else's account. The actor does NOT choose the new
password — the service generates a temporary value and this email is
the only channel that ever carries the cleartext.

The recipient is forced to change the password on next sign-in
(``must_change_password=True`` on their row), so the body explains
the flow without giving them a false sense that the temp value is a
working long-term password. Uses the shared VisiChek brand shell for
all visual presentation.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "password_reset_temp"
SUBJECT = "Your {platform_name} password was reset"


def _safe(context: dict[str, Any], key: str, fallback: str) -> str:
    value = context.get(key)
    if value is None:
        return fallback
    return str(value)


def _actor_label(actor_role: str) -> str:
    role = (actor_role or "").lower()
    if role == "admin":
        return "your platform administrator"
    if role == "super_admin":
        return "your organization's super admin"
    return "an administrator"


def render_html(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    email = _safe(context, "email", "")
    temp_password = _safe(context, "temp_password", "")
    login_url = _safe(context, "login_url", "")
    actor_label = _actor_label(_safe(context, "actor_role", ""))

    cred_rows = [("Sign-in email", email, False)]
    if temp_password:
        cred_rows.append(("Temporary password", temp_password, True))

    content = (
        ui.eyebrow("Password reset")
        + ui.heading(f"Hello {recipient_name},")
        + ui.paragraph(
            f"{actor_label} reset your <strong>{platform_name}</strong> password. "
            "Use the temporary value below to sign in — you will be required "
            "to choose a new password before you can do anything else."
        )
        + ui.cred_card(cred_rows)
        + ui.paragraph("What to do next:")
        + ui.ordered_steps([
            "Sign in with the temporary password above.",
            "You will land directly on the <em>Change password</em> screen "
            "— set a new password you choose.",
            "The temporary value above stops working as soon as your "
            "new password is saved.",
        ])
        + ui.button(f"Sign in to {platform_name}", login_url)
        + ui.fallback_link(login_url)
        + ui.muted(
            "If you didn’t expect this reset, contact your administrator. "
            "All of your active sessions were signed out as part of the reset."
        )
    )

    return ui.page(
        content,
        preheader="Your VisiChek password was reset",
    )


def render_text(context: dict[str, Any]) -> str:
    recipient_name = _safe(context, "recipient_name", "there")
    platform_name = _safe(context, "platform_name", "VisiChek")
    email = _safe(context, "email", "")
    temp_password = _safe(context, "temp_password", "")
    login_url = _safe(context, "login_url", "")
    actor_label = _actor_label(_safe(context, "actor_role", ""))

    lines = [
        f"Hello {recipient_name},",
        "",
        f"{actor_label} reset your {platform_name} password. Use the "
        "temporary value below to sign in — you will be required to "
        "choose a new password before you can do anything else.",
        "",
        f"Sign-in email: {email}",
        f"Temporary password: {temp_password}",
        "",
        "What to do next:",
        "  1. Sign in with the temporary password above.",
        "  2. You will land directly on the Change password screen — set a new password you choose.",
        "  3. The temporary value stops working as soon as your new password is saved.",
    ]
    if login_url:
        lines.extend(["", f"Sign in: {login_url}"])
    lines.extend(
        [
            "",
            "If you didn’t expect this reset, contact your administrator. "
            "All of your active sessions were signed out as part of the reset.",
        ]
    )
    lines.extend(ui.text_signoff())
    return "\n".join(lines)
