"""Admin notification: a tenant company has posted a reply on a support case.

Sent to VisiChek admins/ops staff whenever a tenant replies to an open support
case, so the team can respond promptly. Includes a preview of the reply and a
direct link to the case.
"""
from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "support_case.tenant_replied.admin"
SUBJECT = "[Support] {tenant_company_name} replied on: {case_subject}"


def _common(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_subject": str(context.get("case_subject", "(no subject)")),
        "company_name": str(context.get("tenant_company_name", "A tenant")),
        "preview": str(context.get("message_preview", "")),
        "link": str(context.get("case_url", "#")),
    }


def render_html(context: dict[str, Any]) -> str:
    c = _common(context)

    content = (
        ui.eyebrow("Tenant reply")
        + ui.heading("Tenant replied on support case")
        + ui.paragraph(
            f"<strong>{c['company_name']}</strong> posted on {c['case_subject']}."
        )
        + ui.quote(c["preview"])
        + ui.button("Open case", c["link"])
    )

    return ui.page(content, preheader="A tenant replied on a case")


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    lines = [
        f'{c["company_name"]} posted on case "{c["case_subject"]}".',
        "",
        f"Preview: {c['preview']}",
        "",
        f"Open: {c['link']}",
    ]
    lines += ui.text_signoff()
    return "\n".join(lines)
