"""Default Visitor Privacy Policy master template (BlockNote blocks).

Used by the startup bootstrap to seed the ``visitor-privacy-policy`` master
legal document as a draft when it does not already exist, so the application
admin has an editable starting point and the kiosk has content to render before
the first publish. Placeholders use the canonical fixed-allowlist tokens (see
``services.tenant_agreements.templating.PLACEHOLDER_TOKENS``); they are
substituted per tenant at build time.

This is the platform master that REPLACES tenant self-authoring of the kiosk
visitor notice (the old ``services/privacy_notice_defaults.py`` per-tenant seed,
which used ``{{COMPANY_NAME}}``-style tokens).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

Block = Dict[str, Any]

# (kind, text): h = level-2 bold heading, p = paragraph, li = bullet item.
_TEMPLATE: List[tuple[str, str]] = [
    (
        "p",
        "[company_name] collects visitor information at reception to support "
        "facility access control, appointment validation, and visitor-session "
        "recordkeeping.",
    ),
    ("h", "What information we collect"),
    ("p", "We may collect:"),
    ("li", "Your name"),
    ("li", "Your phone number"),
    ("li", "Your email"),
    ("li", "Organisation/company name"),
    ("li", "Host name"),
    ("li", "Purpose of visit"),
    ("li", "Check-in and check-out time"),
    ("li", "Badge identifier"),
    ("p", "Where necessary for identity verification:"),
    ("li", "Selected identity details from your ID document"),
    ("li", "A visitor photograph if no ID is available"),
    ("h", "Why we collect this information"),
    ("p", "Your information is collected to:"),
    ("li", "Verify visitor identity"),
    ("li", "Manage access to this facility"),
    ("li", "Maintain visitor logs for security and compliance purposes"),
    (
        "p",
        "This processing supports our legitimate interest in maintaining a safe "
        "and controlled facility environment.",
    ),
    ("h", "How long your information is kept"),
    (
        "p",
        "Visitor records are retained for [retention_period]. After this period, "
        "records are securely deleted unless required for legal or security "
        "purposes.",
    ),
    ("h", "Who processes your information"),
    (
        "p",
        "Visitor information is collected by [company_name] using the VisiChek "
        "visitor management platform. VisiChek processes visitor information on "
        "our behalf as a service provider.",
    ),
    ("h", "Your rights"),
    (
        "p",
        "You may request access to or correction of your visitor record by "
        "contacting [contact_email]. Requests are handled in accordance with "
        "applicable data-protection laws.",
    ),
    ("h", "Contact for privacy questions"),
    (
        "p",
        "If you have questions about how your information is handled, please contact:",
    ),
    ("p", "[dpo_contact_email]"),
]


def _new_id() -> str:
    return "b_" + uuid.uuid4().hex[:12]


def _make_block(kind: str, text: str) -> Block:
    if kind == "h":
        return {
            "id": _new_id(),
            "type": "heading",
            "props": {"level": 2},
            "content": [{"type": "text", "text": text, "styles": {"bold": True}}],
            "children": [],
        }
    block_type = "bulletListItem" if kind == "li" else "paragraph"
    return {
        "id": _new_id(),
        "type": block_type,
        "props": {},
        "content": [{"type": "text", "text": text, "styles": {}}],
        "children": [],
    }


def default_blocks() -> List[Block]:
    """Return the default Visitor Privacy Policy master body (with fresh ids)."""
    return [_make_block(kind, text) for kind, text in _TEMPLATE]
