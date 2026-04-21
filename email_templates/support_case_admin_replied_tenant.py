from __future__ import annotations

from typing import Any

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
    return (
        f"<h2>A new reply on your support case</h2>"
        f"<p>{c['actor_name']} replied to your case "
        f"<strong>\"{c['case_subject']}\"</strong>.</p>"
        f"<blockquote>{c['preview']}</blockquote>"
        f"<p><a href='{c['link']}'>Open the full thread</a></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"{c['actor_name']} replied to your case \"{c['case_subject']}\".\n\n"
        f"Preview: {c['preview']}\n\nOpen the thread: {c['link']}"
    )
