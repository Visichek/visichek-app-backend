"""Notifies a tenant (case submitter) that their support case is awaiting
their reply — the support agent has requested additional information and the
ball is back in the tenant's court. Rendered via the VisiChek brand shell.
"""

from __future__ import annotations

import html
from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.awaiting_tenant"
SUBJECT = "We need your input on case: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "preview": str(context.get("message_preview", "")),
        "link": str(context.get("case_url", "#")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)

    content = (
        ui.eyebrow("Action needed")
        + ui.heading("We need your input")
        + ui.paragraph(
            f'Your support case <strong>"{html.escape(c["case_subject"])}"</strong> is '
            "awaiting your reply. Please add the information we requested so "
            "we can keep making progress."
        )
        + ui.button("Reply to the case", c["link"])
        + ui.fallback_link(c["link"])
    )

    return ui.page(
        content,
        preheader="We need your input on your case",
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        f'Your support case "{c["case_subject"]}" is awaiting your reply.',
        "Please add the information we requested.",
        "",
        f"Reply: {c['link']}",
    ]
    lines += ui.text_signoff()
    return "\n".join(lines)
