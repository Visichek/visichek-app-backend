"""Per-tenant Data Processing Agreement template.

The DPA body is shipped as a committed JSON asset
(``services/dpa_template_blocks.json``) produced by
``scripts/dump_dpa_template.py`` from the canonical legal document. This module
loads that asset and, for a given tenant, produces the tenant-scoped DPA:

* fresh BlockNote block ids (so two tenants never share ids),
* the "Controller / Organization" party block filled in with the tenant's
  legal name / address / contact email,
* a flattened ``full_text`` projection for any plain-text consumer.

The Processor / VisiChek party block is fixed and is never substituted.

If the asset is absent (the extraction script has not been run yet) the build
functions return ``None`` and callers degrade gracefully — seeding is skipped
and the DPA read endpoint returns 404 rather than crashing.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional

Block = Dict[str, Any]

DEFAULT_DPA_TITLE = "Data Processing Agreement"

_ASSET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dpa_template_blocks.json")

# The Organization placeholders embedded in the source document. Apostrophe is
# matched permissively (straight ' / curly ’ / backtick) because the source was
# authored in Word and round-tripped through BlockNote.
_RE_NAME = re.compile(r"\[Insert Organization[’'`]?s representative legal name\]")
_RE_ADDRESS = re.compile(r"\[Insert Organization[’'`]?s representative address\]")
_RE_EMAIL = re.compile(r"\[Insert Organization[’'`]?s representative email\]")

# Shown when a tenant has not supplied the value yet (address / email may be
# unset before the super_admin completes the onboarding confirmation screen).
_UNSET = "[To be provided]"

# Cache the parsed asset so we don't re-read the file on every seed.
_TEMPLATE_CACHE: Optional[Dict[str, Any]] = None


def _load_template() -> Optional[Dict[str, Any]]:
    """Load and cache the committed DPA template asset, or None if missing."""
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is not None:
        return _TEMPLATE_CACHE
    if not os.path.exists(_ASSET_PATH):
        return None
    with open(_ASSET_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # Tolerate either the bare asset shape or an API envelope ({data:{body}}).
    if isinstance(data, dict) and "body" not in data and isinstance(
        data.get("data"), dict
    ):
        data = data["data"]
    _TEMPLATE_CACHE = data
    return _TEMPLATE_CACHE


def set_runtime_template(
    *, title: Optional[str], summary: Optional[str], body: List[Block]
) -> None:
    """Install a template loaded at runtime (e.g. from the legal_documents
    collection at startup). Takes precedence over the committed asset file."""
    global _TEMPLATE_CACHE
    _TEMPLATE_CACHE = {
        "title": title or DEFAULT_DPA_TITLE,
        "summary": summary,
        "body": body,
    }


def template_is_available() -> bool:
    """True when a DPA template (runtime-loaded or committed asset) is present."""
    tpl = _load_template()
    return bool(tpl and tpl.get("body"))


def _new_id() -> str:
    return "b_" + uuid.uuid4().hex[:12]


def _substitute_text(
    text: str, *, org_name: str, org_address: str, org_email: str
) -> str:
    text = _RE_NAME.sub(org_name, text)
    text = _RE_ADDRESS.sub(org_address, text)
    text = _RE_EMAIL.sub(org_email, text)
    return text


def _resolve_block(block: Block, **subs: str) -> Block:
    """Deep-copy a template block with a fresh id and substituted text nodes."""
    out: Block = {
        "id": _new_id(),
        "type": block.get("type", "paragraph"),
        "props": dict(block.get("props") or {}),
        "content": [],
        "children": [_resolve_block(c, **subs) for c in block.get("children") or []],
    }
    for node in block.get("content") or []:
        if isinstance(node, dict) and node.get("type") == "text":
            new_node = dict(node)
            new_node["text"] = _substitute_text(node.get("text", ""), **subs)
            out["content"].append(new_node)
        else:
            # Non-text inline nodes (e.g. links) pass through unchanged.
            out["content"].append(node)
    return out


def _flatten(blocks: List[Block]) -> str:
    lines: List[str] = []
    for block in blocks:
        text = "".join(
            n.get("text", "")
            for n in block.get("content", [])
            if isinstance(n, dict)
        )
        if not text:
            continue
        prefix = "- " if block.get("type") in ("bulletListItem", "numberedListItem") else ""
        lines.append(prefix + text)
    return "\n".join(lines)


def build_tenant_dpa_content(
    *,
    company_name: str,
    organization_address: Optional[str] = None,
    contact_email: Optional[str] = None,
) -> Optional[dict]:
    """Return ``{title, summary, full_text, body}`` for a tenant's DPA, or None
    when the template asset is not present.

    ``body`` is the canonical BlockNote content with the Organization party
    block filled in; ``full_text`` is a flattened projection.
    """
    tpl = _load_template()
    if not tpl or not tpl.get("body"):
        return None

    subs = {
        "org_name": company_name or _UNSET,
        "org_address": organization_address or _UNSET,
        "org_email": contact_email or _UNSET,
    }
    body = [_resolve_block(b, **subs) for b in tpl["body"]]

    title = tpl.get("title") or DEFAULT_DPA_TITLE
    summary = tpl.get("summary")
    if summary:
        summary = _substitute_text(summary, **subs)

    return {
        "title": title,
        "summary": summary,
        "full_text": _flatten(body),
        "body": body,
    }
