"""Fixed-allowlist ``[placeholder]`` templating for tenant agreements.

The application admin authors the master documents in BlockNote and inserts
placeholders as plain bracketed text, e.g. ``[company_name]``. At per-tenant
build time those tokens are substituted from the tenant's own details. The
allowlist is fixed (``PLACEHOLDER_TOKENS``): a bracket whose inner text is not
a known token (directly or via a legacy alias) is left untouched, so arbitrary
square brackets in the legal copy survive verbatim.

Resolution rules:

* Known token with a value  → the value.
* Known token, value unset  → ``[To be provided]`` (a visible, reviewable gap).
* Unknown bracket           → left literal.

This generalises the original per-tenant DPA substitution in
``services/dpa_defaults.py`` (which hard-coded three verbose Word placeholders).
Those verbose placeholders are kept working via ``_LEGACY_ALIASES`` so the
already-committed DPA template resolves without being re-authored.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List

Block = Dict[str, Any]

# Shown for an allowlisted token whose tenant value is not set yet.
UNSET_PLACEHOLDER = "[To be provided]"

# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------

#: Canonical placeholder tokens (the text inside the brackets, normalised). The
#: frontend editor can offer exactly these for insertion; values are resolved
#: by the service layer from the tenant record + main super_admin email.
PLACEHOLDER_TOKENS: tuple[str, ...] = (
    "company_name",
    "organization_address",
    "contact_email",
    "dpo_contact_email",
    "retention_period",
    "country_of_hosting",
    "effective_date",
)

_TOKEN_SET = frozenset(PLACEHOLDER_TOKENS)

# Legacy verbose placeholders from the original Word-authored DPA, mapped onto
# the canonical tokens. Apostrophes are normalised permissively (straight '
# curly ’ backtick `) before lookup.
_LEGACY_ALIASES: Dict[str, str] = {
    "insert organization's representative legal name": "company_name",
    "insert organization's representative address": "organization_address",
    "insert organization's representative email": "contact_email",
}

_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")


def _normalise_apostrophes(text: str) -> str:
    return text.replace("’", "'").replace("`", "'")


def _canonical_token(inner: str) -> str | None:
    """Map the text inside a ``[...]`` to a canonical token, or ``None``.

    Accepts ``[company_name]``, ``[Company Name]``, ``[COMPANY NAME]`` (spaces
    and case folded to the underscore form) as well as the legacy verbose
    aliases.
    """
    raw = inner.strip()
    # Canonical form: lower-case, internal whitespace -> single underscore.
    folded = re.sub(r"\s+", "_", raw.lower()).strip("_")
    if folded in _TOKEN_SET:
        return folded
    alias_key = re.sub(r"\s+", " ", _normalise_apostrophes(raw).lower()).strip()
    return _LEGACY_ALIASES.get(alias_key)


def substitute_text(text: str, values: Dict[str, str]) -> str:
    """Replace every allowlisted ``[token]`` in ``text`` using ``values``.

    ``values`` maps canonical token -> resolved string (may be empty/None for
    unset). Unknown brackets are left untouched.
    """
    if not text or "[" not in text:
        return text

    def _repl(match: "re.Match[str]") -> str:
        token = _canonical_token(match.group(1))
        if token is None:
            return match.group(0)  # leave unknown brackets literal
        value = values.get(token)
        return value if value else UNSET_PLACEHOLDER

    return _BRACKET_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# Block-level resolution
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return "b_" + uuid.uuid4().hex[:12]


def _resolve_block(block: Block, values: Dict[str, str]) -> Block:
    """Deep-copy a block with a fresh id and substituted text nodes."""
    out: Block = {
        "id": _new_id(),
        "type": block.get("type", "paragraph"),
        "props": dict(block.get("props") or {}),
        "content": [],
        "children": [_resolve_block(c, values) for c in block.get("children") or []],
    }
    for node in block.get("content") or []:
        if isinstance(node, dict) and node.get("type") == "text":
            new_node = dict(node)
            new_node["text"] = substitute_text(node.get("text", ""), values)
            out["content"].append(new_node)
        else:
            # Non-text inline nodes (links, etc.) pass through unchanged.
            out["content"].append(node)
    return out


def resolve_blocks(blocks: List[Block], values: Dict[str, str]) -> List[Block]:
    """Return a substituted deep copy of ``blocks`` with fresh block ids."""
    return [_resolve_block(b, values) for b in blocks or []]


def flatten_blocks(blocks: List[Block]) -> str:
    """Project a block list onto plain text for the ``full_text`` field."""
    lines: List[str] = []
    for block in blocks or []:
        text = "".join(
            n.get("text", "")
            for n in block.get("content", []) or []
            if isinstance(n, dict)
        )
        if not text:
            continue
        prefix = (
            "- " if block.get("type") in ("bulletListItem", "numberedListItem") else ""
        )
        lines.append(prefix + text)
    return "\n".join(lines)
