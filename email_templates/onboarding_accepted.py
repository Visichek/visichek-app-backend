"""Tenant onboarding acceptance email.

Sent by ``services/onboarding_submission_service._queue_status_email``
after an application admin accepts a self-onboarding submission. Carries
the sign-in email + temporary password the system generated (or the
password the admin chose during approval) plus the tenant login URL.

Mirrors the style of ``admin_invite.py`` so both flows feel like the
same product. HTML stays self-contained — inline styles only, no
external CSS or remote images.
"""

from __future__ import annotations

from typing import Any

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

    login_button = (
        f"<a href='{login_url}' style='display:inline-block;background:#0F172A;"
        "color:#FFFFFF;text-decoration:none;padding:12px 22px;border-radius:8px;"
        f"font-weight:600;font-size:14px'>Sign in to {platform_name}</a>"
        if login_url
        else ""
    )

    notes_block = (
        "<div style='background:#FEF9C3;border:1px solid #FDE68A;border-radius:10px;"
        "padding:14px 18px;margin:0 0 18px;color:#713F12;font-size:13px;line-height:1.55'>"
        f"<strong>Note from our team:</strong><br>{review_notes}</div>"
        if review_notes
        else ""
    )

    creds_block = (
        "<div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;"
        "padding:16px 20px;margin:18px 0'>"
        "<p style='margin:0 0 6px;font-size:13px;color:#475569'>Sign-in email</p>"
        f"<p style='margin:0 0 14px;font-weight:600'>{email}</p>"
        "<p style='margin:0 0 6px;font-size:13px;color:#475569'>Temporary password</p>"
        f"<p style='margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
        f"font-size:15px;font-weight:600'>{temp_password}</p>"
        "</div>"
        if temp_password
        else ""
    )

    return (
        "<div style='font-family:system-ui,Helvetica,Arial,sans-serif;"
        "max-width:560px;color:#0F172A;line-height:1.55'>"
        f"<h2 style='margin:0 0 16px;font-size:20px'>Welcome to {platform_name}, {full_name}.</h2>"
        f"<p style='margin:0 0 14px'>Your application to set up "
        f"<strong>{organization_name}</strong> on {platform_name} has been approved. "
        "You can sign in below as the super admin for your workspace.</p>"
        f"{notes_block}"
        f"{creds_block}"
        "<p style='margin:0 0 14px'>For your first sign-in:</p>"
        "<ol style='padding-left:20px;margin:0 0 18px'>"
        "<li style='margin-bottom:6px'>Use the temporary password above.</li>"
        "<li style='margin-bottom:6px'>Change your password from "
        "<em>Settings → Account</em> right after you land.</li>"
        "<li>Invite your team and configure your branches, departments, "
        "and check-in flow.</li>"
        "</ol>"
        f"<p style='margin:0 0 22px'>{login_button}</p>"
        "<p style='margin:0;color:#64748B;font-size:12px'>"
        "You are currently on the Free plan — every workspace starts there. "
        "You can upgrade to a paid plan from <em>Settings → Billing</em> at any time."
        "</p>"
        "</div>"
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
            "  2. Change your password from Settings → Account right after you land.",
            "  3. Invite your team and configure your branches, departments, and check-in flow.",
        ]
    )
    if login_url:
        lines.extend(["", f"Sign in: {login_url}"])
    lines.extend(
        [
            "",
            "You are currently on the Free plan — every workspace starts there. "
            "You can upgrade to a paid plan from Settings → Billing at any time.",
        ]
    )
    return "\n".join(lines)
