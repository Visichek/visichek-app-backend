"""Authority-driven password reset notification.

Sent by ``services/password_change_service.reset_system_user_password_by_authority``
when an application admin (or tenant super_admin) triggers a password
reset on someone else's account. The actor does NOT choose the new
password — the service generates a temporary value and this email is
the only channel that ever carries the cleartext.

The recipient is forced to change the password on next sign-in
(``must_change_password=True`` on their row), so the body explains
the flow without giving them a false sense that the temp value is a
working long-term password.
"""

from __future__ import annotations

from typing import Any

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

    login_button = (
        f"<a href='{login_url}' style='display:inline-block;background:#0F172A;"
        "color:#FFFFFF;text-decoration:none;padding:12px 22px;border-radius:8px;"
        f"font-weight:600;font-size:14px'>Sign in to {platform_name}</a>"
        if login_url
        else ""
    )

    return (
        "<div style='font-family:system-ui,Helvetica,Arial,sans-serif;"
        "max-width:560px;color:#0F172A;line-height:1.55'>"
        f"<h2 style='margin:0 0 14px;font-size:20px'>Hello {recipient_name},</h2>"
        f"<p style='margin:0 0 14px'>{actor_label} reset your "
        f"<strong>{platform_name}</strong> password. Use the temporary "
        "value below to sign in — you will be required to choose a new "
        "password before you can do anything else.</p>"
        "<div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;"
        "padding:16px 20px;margin:18px 0'>"
        "<p style='margin:0 0 6px;font-size:13px;color:#475569'>Sign-in email</p>"
        f"<p style='margin:0 0 14px;font-weight:600'>{email}</p>"
        "<p style='margin:0 0 6px;font-size:13px;color:#475569'>Temporary password</p>"
        f"<p style='margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
        f"font-size:15px;font-weight:600'>{temp_password}</p>"
        "</div>"
        "<p style='margin:0 0 14px'>What to do next:</p>"
        "<ol style='padding-left:20px;margin:0 0 18px'>"
        "<li style='margin-bottom:6px'>Sign in with the temporary password above.</li>"
        "<li style='margin-bottom:6px'>You will land directly on the "
        "<em>Change password</em> screen — set a new password you choose.</li>"
        "<li>The temporary value above stops working as soon as your "
        "new password is saved.</li>"
        "</ol>"
        f"<p style='margin:0 0 22px'>{login_button}</p>"
        "<p style='margin:0;color:#64748B;font-size:12px'>"
        "If you didn't expect this reset, contact your administrator. "
        "All of your active sessions were signed out as part of the reset."
        "</p>"
        "</div>"
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
            "If you didn't expect this reset, contact your administrator. "
            "All of your active sessions were signed out as part of the reset.",
        ]
    )
    return "\n".join(lines)
