from __future__ import annotations

from typing import Any

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
    return (
        f"<h2>We need your input</h2>"
        f"<p>Your support case <strong>\"{c['case_subject']}\"</strong> is "
        f"awaiting your reply. Please add the information we requested so "
        f"we can keep making progress.</p>"
        f"<p><a href='{c['link']}'>Reply to the case</a></p>"
    )


def render_text(context: dict[str, Any]) -> str:
    c = _common(context)
    return (
        f"Your support case \"{c['case_subject']}\" is awaiting your reply.\n"
        f"Please add the information we requested.\n\nReply: {c['link']}"
    )
