"""Notifies the tenant contact that a support agent has replied to their case.

Sent whenever a VisiChek support agent posts a new reply on a case raised by a
tenant user. Includes a preview of the reply and a direct link to the thread.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.admin_replied.tenant"
SUBJECT = "New reply on your case: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "actor_name": str(context.get("actor_name", "Our support team")),
        "preview": str(context.get("message_preview", "")),
        "link": str(context.get("case_url", "#")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)

    content = (
        ui.eyebrow("Support")
        + ui.heading("A new reply on your case")
        + ui.paragraph(
            f"{c['actor_name']} replied to your case "
            f"<strong>“{c['case_subject']}”</strong>."
        )
        + (ui.quote(c["preview"]) if c["preview"] else "")
        + ui.button("Open the full thread", c["link"])
    )

    return ui.page(content, preheader="New reply on your case")


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        f'{c["actor_name"]} replied to your case "{c["case_subject"]}".',
        "",
    ]
    if c["preview"]:
        lines += [f'Preview: {c["preview"]}', ""]
    lines.append(f"Open the thread: {c['link']}")
    lines += ui.text_signoff()
    return "\n".join(lines)
