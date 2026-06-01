"""Starter email template — a minimal example showing the VisiChek shell.

Renders a simple greeting with a primary call-to-action button.
Demonstrates the minimal render_html / render_text contract every template
must satisfy.
"""

from __future__ import annotations

from typing import Any

from email_templates import _shell as ui

TEMPLATE_KEY = "starter"
SUBJECT = "Starter Email Template"


def render_html(context: dict[str, Any]) -> str:
    recipient_name = str(context.get("recipient_name", "there"))
    action_link = str(context.get("action_link", "#"))

    content = (
        ui.eyebrow("VisiChek")
        + ui.heading("Starter template")
        + ui.paragraph(f"Hello {recipient_name}, this is your starter email template.")
        + ui.button("Continue", action_link)
    )

    return ui.page(content, preheader="VisiChek")


def render_text(context: dict[str, Any]) -> str:
    recipient_name = str(context.get("recipient_name", "there"))
    action_link = str(context.get("action_link", "#"))

    lines = [
        "VISICHEK",
        "",
        "Starter template",
        "",
        f"Hello {recipient_name}, this is your starter email template.",
        "",
        f"Continue: {action_link}",
    ]
    lines += ui.text_signoff()
    return "\n".join(lines)
