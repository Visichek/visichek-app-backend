"""Shared imports + legal-document domain types.

The BlockNote content models (``BaseBlock``, ``ParagraphBlock``,
``HeadingBlock``, ``parse_document``, ...) are re-exported from
``blog.schemas.imports`` so the legal editor speaks exactly the same
block-JSON dialect as the blog editor — one frontend rich-text component
serves both. Only the legal-specific enums + helpers live here.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

# Re-export the BlockNote block models so callers can ``from
# legal.schemas.imports import parse_document`` without reaching into the
# blog package directly.
from blog.schemas.imports import (  # noqa: F401
    APIInlineContent,
    BaseBlock,
    HeadingBlock,
    LinkInline,
    ParagraphBlock,
    StyledText,
    parse_block_dict,
    parse_document,
)


class LegalDocStatus(str, Enum):
    """Lifecycle state of a legal document head record."""

    draft = "draft"
    published = "published"
    archived = "archived"


class LegalDocType(str, Enum):
    """Well-known legal document categories.

    ``other`` is the escape hatch for admin-defined documents — the
    feature lets you *name* an arbitrary legal doc, so the slug/title is
    the real identity and ``docType`` is just a grouping tag for the UI.
    """

    privacy_policy = "privacy_policy"
    terms_of_service = "terms_of_service"
    service_agreement = "service_agreement"
    cookie_policy = "cookie_policy"
    acceptable_use_policy = "acceptable_use_policy"
    data_processing_agreement = "data_processing_agreement"
    refund_policy = "refund_policy"
    sla = "sla"
    disclaimer = "disclaimer"
    other = "other"


# Slug validation — lowercase kebab/underscore, must start with a letter.
SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{0,80}$")


def generate_slug(title: str) -> str:
    """Build a URL-safe slug from a human title.

    Mirrors ``blog.schemas.blog_schema._generate_slug`` so behaviour is
    predictable across the two content features.
    """
    if not title:
        return "untitled-document"
    cleaned = title.lower()
    cleaned = re.sub(r"[^a-z0-9\s-]", "", cleaned)
    cleaned = re.sub(r"[\s-]+", "-", cleaned)
    cleaned = cleaned.strip("-")
    return cleaned or "untitled-document"


def excerpt_from_blocks(blocks: Optional[list], max_length: int = 240) -> str:
    """Flatten the leading text of a BlockNote body into a plain-text excerpt."""
    if not blocks:
        return ""
    texts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        content = block.get("content")
        if isinstance(content, list):
            for node in content:
                if isinstance(node, dict) and node.get("type") == "text":
                    texts.append(node.get("text", ""))
                elif isinstance(node, dict) and node.get("type") == "link":
                    for inner in node.get("content", []) or []:
                        if isinstance(inner, dict) and inner.get("type") == "text":
                            texts.append(inner.get("text", ""))
    full_text = " ".join(t for t in texts if t).strip()
    if len(full_text) > max_length:
        return full_text[:max_length].rstrip() + "..."
    return full_text
