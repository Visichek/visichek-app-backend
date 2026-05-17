"""FAQ overlay service for the public marketing site.

Singleton overlay document (``faqs`` collection) holding question /
answer pairs, optional section groupings, and hero copy. GET renders
the overlay on top of ship-with-code defaults so the page is non-empty
on day one; PATCH merges incoming changes onto the persisted overlay
by natural key (``item_key`` / ``category_key``).

Mirror of ``services.pricing_marketing_service`` in shape — same
overlay-on-top-of-defaults pattern, same precompute caching, same
merge semantics. Kept as a separate module so the FAQ schema can
evolve independently of pricing-page concerns.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from schemas.faq_schema import (
    FaqOut,
    FaqOverlayCreate,
    FaqOverlayOut,
    FaqOverlayPatch,
    FaqRenderedItem,
    FaqRenderedSection,
    normalize_question_key,
)
from repositories.faq_repo import create_overlay, get_overlay, replace_overlay

logger = logging.getLogger(__name__)


# ── Defaults ────────────────────────────────────────────────────────


DEFAULT_HEADLINE = "Frequently Asked Questions"
DEFAULT_SUBHEADLINE: Optional[str] = None
DEFAULT_FOOTER_HTML = (
    "Have an infrequently asked question? Email us anytime at "
    "<a href='mailto:support@visichek.com'>support@visichek.com</a>."
)


# Default sections. Admins can rename / re-order via PATCH categories;
# admins can also create entirely new categories by sending a new
# ``category_key`` on a PATCH categories entry.
DEFAULT_CATEGORIES: List[Dict[str, Any]] = [
    {"category_key": "general", "label": "General", "sort_order": 10},
    {"category_key": "billing", "label": "Billing & pricing", "sort_order": 20},
    {"category_key": "security", "label": "Security & compliance", "sort_order": 30},
    {"category_key": "support", "label": "Support", "sort_order": 40},
]


# ── Render ───────────────────────────────────────────────────────────


def _resolve_categories(
    overlay: Optional[FaqOverlayOut],
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {
        c["category_key"]: dict(c) for c in DEFAULT_CATEGORIES
    }
    if overlay:
        for cat in overlay.categories:
            out[cat.category_key] = {
                "category_key": cat.category_key,
                "label": cat.label,
                "sort_order": cat.sort_order,
            }
    return out


def _build_sections(overlay: Optional[FaqOverlayOut]) -> List[FaqRenderedSection]:
    categories = _resolve_categories(overlay)
    by_cat: Dict[str, List[FaqRenderedItem]] = {}

    items = overlay.items if overlay else []
    for item in items:
        cat_key = item.category_key or "general"
        if cat_key not in categories:
            # Items pointing at an unknown category get parked under a
            # synthetic "general" section rather than dropping out.
            cat_key = "general"
        by_cat.setdefault(cat_key, []).append(
            FaqRenderedItem(
                item_key=item.item_key,
                question=item.question,
                answer=item.answer,
                sort_order=item.sort_order,
            )
        )

    sections: List[FaqRenderedSection] = []
    for cat_key, rendered_items in by_cat.items():
        meta = categories.get(
            cat_key,
            {"category_key": cat_key, "label": cat_key, "sort_order": 999},
        )
        rendered_items.sort(key=lambda i: (i.sort_order, i.question))
        sections.append(
            FaqRenderedSection(
                category_key=cat_key,
                label=meta["label"],
                sort_order=meta["sort_order"],
                items=rendered_items,
            )
        )

    sections.sort(key=lambda s: (s.sort_order, s.label))
    return sections


async def render_faqs() -> FaqOut:
    overlay = await get_overlay()
    sections = _build_sections(overlay)

    headline = (overlay.headline if overlay and overlay.headline else None) or DEFAULT_HEADLINE
    subheadline = (
        overlay.subheadline if overlay and overlay.subheadline else None
    ) or DEFAULT_SUBHEADLINE
    footer_html = (
        overlay.footer_html if overlay and overlay.footer_html else None
    ) or DEFAULT_FOOTER_HTML

    return FaqOut(
        headline=headline,
        subheadline=subheadline,
        footer_html=footer_html,
        sections=sections,
        last_updated=int(overlay.last_updated)
        if overlay and overlay.last_updated
        else int(time.time()),
    )


# ── PATCH merge ─────────────────────────────────────────────────────


def _merge_list(
    existing: List[Any],
    incoming: Optional[List[Any]],
    key_attr: str,
) -> List[Any]:
    if incoming is None:
        return existing
    if not incoming:
        return []
    by_key: Dict[str, Any] = {}
    for item in existing:
        k = item.get(key_attr) if isinstance(item, dict) else getattr(item, key_attr, None)
        if k:
            by_key[k] = item
    for item in incoming:
        as_dict = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)
        key = as_dict.get(key_attr)
        if not key:
            continue
        by_key[key] = as_dict
    existing_keys: List[str] = []
    for item in existing:
        k = item.get(key_attr) if isinstance(item, dict) else getattr(item, key_attr, None)
        if k and k in by_key and k not in existing_keys:
            existing_keys.append(k)
    out: List[Any] = [by_key[k] for k in existing_keys]
    for k, v in by_key.items():
        if k not in existing_keys:
            out.append(v)
    return out


def _assert_no_duplicate_questions(items: List[dict]) -> None:
    """Reject any pair of items whose ``question`` normalises to the same key.

    Different ``item_key`` values are allowed; the constraint is on the
    human-facing ``question`` text. Raises 409 so the admin UI can
    surface the collision rather than a generic 500.
    """
    by_qkey: Dict[str, str] = {}
    for item in items:
        if isinstance(item, dict):
            question = str(item.get("question") or "")
            item_key = str(item.get("item_key") or "")
        else:
            question = str(getattr(item, "question", "") or "")
            item_key = str(getattr(item, "item_key", "") or "")
        if not question:
            continue
        qkey = normalize_question_key(question)
        if qkey in by_qkey and by_qkey[qkey] != item_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Duplicate FAQ question: '{question}' already exists "
                    f"under itemKey '{by_qkey[qkey]}'. Edit that row "
                    f"instead of adding a second copy."
                ),
            )
        by_qkey[qkey] = item_key


async def apply_overlay_patch(patch: FaqOverlayPatch) -> FaqOverlayOut:
    current = await get_overlay()
    if current is None:
        # Even on first write, enforce uniqueness within the seed.
        seed_items = patch.items or []
        _assert_no_duplicate_questions([i.model_dump() for i in seed_items])
        seed = FaqOverlayCreate(
            headline=patch.headline,
            subheadline=patch.subheadline,
            footer_html=patch.footer_html,
            items=seed_items,
            categories=patch.categories or [],
        )
        return await create_overlay(seed)

    current_dict = current.model_dump(by_alias=False, exclude={"id"})
    if patch.headline is not None:
        current_dict["headline"] = patch.headline
    if patch.subheadline is not None:
        current_dict["subheadline"] = patch.subheadline
    if patch.footer_html is not None:
        current_dict["footer_html"] = patch.footer_html

    current_dict["items"] = _merge_list(
        current_dict.get("items", []), patch.items, "item_key"
    )
    current_dict["categories"] = _merge_list(
        current_dict.get("categories", []), patch.categories, "category_key"
    )

    # Cross-payload duplicate check — after merge, no two items should
    # share the same normalised question. This is the catch for "admin
    # PATCH'd a single new item whose question collides with an existing
    # row that wasn't in the payload".
    _assert_no_duplicate_questions(current_dict["items"])

    current_dict["last_updated"] = int(time.time())
    current_dict.pop("_id", None)
    current_dict.pop("id", None)
    if current.date_created is not None:
        current_dict["date_created"] = current.date_created

    refreshed = await replace_overlay(current_dict)
    if refreshed is None:
        raise RuntimeError("faq: replace_overlay returned None")
    return refreshed


async def delete_overlay_row(kind: str, key: str) -> FaqOverlayOut:
    current = await get_overlay()
    if current is None:
        return FaqOverlayOut(
            headline=None,
            subheadline=None,
            footer_html=None,
            items=[],
            categories=[],
            date_created=int(time.time()),
            last_updated=int(time.time()),
        )

    current_dict = current.model_dump(by_alias=False, exclude={"id"})
    if kind == "item":
        current_dict["items"] = [
            i for i in current_dict.get("items", []) if i.get("item_key") != key
        ]
    elif kind == "category":
        current_dict["categories"] = [
            c
            for c in current_dict.get("categories", [])
            if c.get("category_key") != key
        ]
    else:
        raise ValueError(f"Unknown faq row kind: {kind}")

    current_dict["last_updated"] = int(time.time())
    current_dict.pop("_id", None)
    current_dict.pop("id", None)
    if current.date_created is not None:
        current_dict["date_created"] = current.date_created

    refreshed = await replace_overlay(current_dict)
    if refreshed is None:
        raise RuntimeError("faq: replace_overlay returned None")
    return refreshed
