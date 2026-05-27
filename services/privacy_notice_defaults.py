"""VisiChek-style default visitor privacy policy.

Seeded on tenant provisioning (and lazily on first read of the active notice)
so the visitor-consent feature works on day one without the tenant authoring
anything. The tenant can fully edit the seeded notice afterwards in the
rich-text (BlockNote) editor.

The canonical copy is the BlockNote ``body`` (a list of content blocks shaped
``{id, type, props, content, children}`` exactly as the frontend editor
round-trips). ``summary`` and ``full_text`` are flattened plain-text
projections of the same template, kept for the kiosk consent gate and any
caller that still reads the legacy plain-text fields.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

DEFAULT_NOTICE_TITLE = "Visitor Privacy Policy"

Block = Dict[str, Any]

# Substitution tokens. The service layer fills these per-tenant on seed:
#   {{COMPANY_NAME}}     -> tenant.company_name
#   {{RETENTION_PERIOD}} -> e.g. "1095 days", from the tenant's retention policy
#   {{CONTACT_EMAIL}}    -> tenant main super_admin email (general contact)
#   {{PRIVACY_CONTACT}}  -> tenant DPO email, falling back to the contact email
_TOKEN_COMPANY = "{{COMPANY_NAME}}"
_TOKEN_RETENTION = "{{RETENTION_PERIOD}}"
_TOKEN_CONTACT = "{{CONTACT_EMAIL}}"
_TOKEN_PRIVACY = "{{PRIVACY_CONTACT}}"

# The template, expressed as (kind, text) tuples. ``h`` = level-2 bold heading,
# ``p`` = paragraph, ``li`` = bullet list item. Mirrors the editor-authored
# document the tenants approved; the placeholders are substituted at seed time.
_TEMPLATE: List[tuple[str, str]] = [
    (
        "p",
        f"{_TOKEN_COMPANY} collects visitor information at reception to support "
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
        f"Visitor records are retained for {_TOKEN_RETENTION}. After this period, "
        "records are securely deleted unless required for legal or security "
        "purposes.",
    ),
    ("h", "Who processes your information"),
    (
        "p",
        f"Visitor information is collected by {_TOKEN_COMPANY} using the VisiChek "
        "visitor management platform. VisiChek processes visitor information on "
        "our behalf as a service provider.",
    ),
    ("h", "Your rights"),
    (
        "p",
        "You may request access to or correction of your visitor record by "
        f"contacting {_TOKEN_CONTACT}. Requests are handled in accordance with "
        "applicable data-protection laws.",
    ),
    ("h", "Contact for privacy questions"),
    (
        "p",
        "If you have questions about how your information is handled, please contact:",
    ),
    ("p", _TOKEN_PRIVACY),
]


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _make_block(kind: str, text: str) -> Block:
    """Build a single BlockNote block matching the frontend editor's shape."""
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


def _format_retention(retention_days: Optional[int]) -> str:
    if retention_days and retention_days > 0:
        return f"{retention_days} days"
    return "the period set out in our data-retention policy"


def _substitute(
    text: str,
    *,
    company_name: str,
    retention: str,
    contact_email: str,
    privacy_contact: str,
) -> str:
    return (
        text.replace(_TOKEN_COMPANY, company_name)
        .replace(_TOKEN_RETENTION, retention)
        .replace(_TOKEN_CONTACT, contact_email)
        .replace(_TOKEN_PRIVACY, privacy_contact)
    )


def _flatten(blocks: List[Block]) -> str:
    """Project the block list onto plain text for the legacy ``full_text``."""
    lines: List[str] = []
    for block in blocks:
        text = "".join(node.get("text", "") for node in block.get("content", []))
        if not text:
            continue
        lines.append(f"- {text}" if block.get("type") == "bulletListItem" else text)
    return "\n".join(lines)


def build_default_notice_content(
    company_name: str,
    *,
    contact_email: Optional[str] = None,
    privacy_contact: Optional[str] = None,
    retention_days: Optional[int] = None,
) -> dict:
    """Return the seeded default notice's ``title`` / ``summary`` / ``full_text``
    / ``body`` with every per-tenant placeholder substituted.

    ``body`` is the canonical BlockNote content; ``summary`` and ``full_text``
    are flattened projections kept for the kiosk and legacy readers.
    """
    company = company_name or "Our organisation"
    retention = _format_retention(retention_days)
    # The general contact falls back to a neutral phrase only when a tenant has
    # neither a main super_admin email nor a DPO email on file.
    contact = contact_email or "the facility administrator"
    # Privacy contact prefers the DPO/privacy address, then the general contact.
    privacy = privacy_contact or contact

    blocks: List[Block] = []
    for kind, raw in _TEMPLATE:
        text = _substitute(
            raw,
            company_name=company,
            retention=retention,
            contact_email=contact,
            privacy_contact=privacy,
        )
        blocks.append(_make_block(kind, text))

    # The summary is the opening paragraph (already substituted).
    summary = "".join(node.get("text", "") for node in blocks[0]["content"])

    return {
        "title": DEFAULT_NOTICE_TITLE,
        "summary": summary,
        "full_text": _flatten(blocks),
        "body": blocks,
    }
