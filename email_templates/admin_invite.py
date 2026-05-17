"""Application-admin invitation email.

Sent by ``services/admin_service.add_admin`` whenever an existing
application admin invites a new platform operator. Carries the
sign-in URL, the temporary password the inviter chose, and a clear
nudge that 2FA is mandatory on first login.

Keep the HTML self-contained (inline styles only) so it renders the
same in Gmail / Outlook / Apple Mail / mobile clients. No external
CSS, no remote images.
"""

from __future__ import annotations

from typing import Any

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
        f"<h2 style='margin:0 0 16px;font-size:20px'>Welcome to {platform_name}, {invitee_name}.</h2>"
        f"<p style='margin:0 0 14px'>{inviter_name} has invited you to help "
        f"administer the {platform_name} platform with the "
        f"<strong>{access_label}</strong> role.</p>"
        "<div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;"
        "padding:16px 20px;margin:18px 0'>"
        "<p style='margin:0 0 6px;font-size:13px;color:#475569'>Sign-in email</p>"
        f"<p style='margin:0 0 14px;font-weight:600'>{email}</p>"
        "<p style='margin:0 0 6px;font-size:13px;color:#475569'>Temporary password</p>"
        f"<p style='margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
        f"font-size:15px;font-weight:600'>{temp_password}</p>"
        "</div>"
        "<p style='margin:0 0 14px'>For your first sign-in:</p>"
        "<ol style='padding-left:20px;margin:0 0 18px'>"
        "<li style='margin-bottom:6px'>Use the temporary password above.</li>"
        "<li style='margin-bottom:6px'>You will be asked for a 6-digit verification code "
        "sent to this inbox — that is two-factor authentication, and it is required "
        "on every login.</li>"
        "<li>Change your password from <em>Settings → Account</em> right after you land.</li>"
        "</ol>"
        f"<p style='margin:0 0 22px'>{login_button}</p>"
        "<p style='margin:0;color:#64748B;font-size:12px'>"
        "If you weren't expecting this invitation, you can ignore the email — "
        "the account is unusable until someone signs in with the temporary password."
        "</p>"
        "</div>"
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
        "  3. Change your password from Settings → Account right after you land.",
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
    return "\n".join(lines)
