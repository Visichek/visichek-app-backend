from __future__ import annotations

from typing import Any

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
    return (
        f"<h2>Tenant replied on support case</h2>"
        f"<p><strong>{c['company_name']}</strong> posted on case "
        f"<em>{c['case_subject']}</em>.</p>"
        f"<blockquote>{c['preview']}</blockquote>"
        f"<p><a href='{c['link']}'>Open case</a></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"{c['company_name']} posted on case \"{c['case_subject']}\".\n\n"
        f"Preview: {c['preview']}\n\nOpen: {c['link']}"
    )
