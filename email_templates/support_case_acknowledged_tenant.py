"""Tenant notification — support case acknowledged.

Sent to the tenant (case submitter) when a support agent acknowledges their
open case, confirming it is actively being reviewed.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.acknowledged.tenant"
SUBJECT = "We've acknowledged your case: {case_subject}"


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
        + ui.heading("Your case has been acknowledged")
        + ui.paragraph(
            f"{c['actor_name']} has acknowledged your case "
            f'<strong>"{c["case_subject"]}"</strong> and is reviewing it now.'
        )
        + ui.button("View case", c["link"])
        + ui.fallback_link(c["link"])
    )

    return ui.page(
        content,
        preheader="We acknowledged your case",
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        f'{c["actor_name"]} has acknowledged your case "{c["case_subject"]}" '
        f"and is reviewing it now.",
        "",
        f"View case: {c['link']}",
    ]
    lines += ui.text_signoff()
    return "\n".join(lines)
