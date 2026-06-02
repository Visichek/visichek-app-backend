"""Notifies the tenant that a support case has been marked as resolved.

Sent when a support agent (or the system) closes a case on behalf of the
tenant organisation. The recipient can confirm the fix or reopen within 7 days.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.resolved.tenant"
SUBJECT = "Your case has been resolved: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "actor_name": str(context.get("actor_name", "Our support team")),
        "link": str(context.get("case_url", "#")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)

    content = (
        ui.eyebrow("Support")
        + ui.heading("Your case has been resolved")
        + ui.paragraph(
            f"{c['actor_name']} has marked your case "
            f"<strong>&ldquo;{c['case_subject']}&rdquo;</strong> as resolved."
        )
        + ui.paragraph(
            "If the fix worked, you don’t have to do anything — we’ll "
            "auto-close the case in 7 days. If something still isn’t right, open "
            "the case and click <em>Reopen</em>."
        )
        + ui.button("Confirm or reopen", c["link"])
        + ui.fallback_link(c["link"] if c["link"] != "#" else "")
    )

    return ui.page(
        content,
        preheader="Your case has been resolved",
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        f'{c["actor_name"]} has marked your case "{c["case_subject"]}" as resolved.',
        "",
        "If the fix worked, no action needed — it’ll auto-close in 7 days. "
        f"Otherwise, reopen it from: {c['link']}",
    ]
    lines += ui.text_signoff()
    return "\n".join(lines)
