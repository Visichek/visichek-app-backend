"""Tenant notification: a support case has been closed.

Sent to the tenant contact when their support case is marked closed in
VisiChek. Contains the case subject and a direct link to the case history.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.closed.tenant"
SUBJECT = "Case closed: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "link": str(context.get("case_url", "#")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)

    content = (
        ui.eyebrow("Support")
        + ui.heading("Your support case is now closed")
        + ui.paragraph(
            f'The case <strong>&#8220;{c["case_subject"]}&#8221;</strong> has been '
            f"closed. If something similar comes up, you can always open a new case."
        )
        + ui.button("View case history", c["link"])
        + ui.fallback_link(c["link"] if c["link"] != "#" else "")
    )

    return ui.page(
        content,
        preheader="Your support case is closed",
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        f'The case "{c["case_subject"]}" has been closed.',
        "Open a new case if you need anything else.",
        "",
        f"View case history: {c['link']}",
    ]
    lines += ui.text_signoff()
    return "\n".join(lines)
