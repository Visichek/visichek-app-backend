"""Default FAQ bootstrap.

Runs once at app startup. Seeds the public FAQ page with the
five canonical questions the marketing team agreed on at launch.

The bootstrap is **conservative** — it only inserts items whose
``item_key`` is not yet present in the overlay. Admin edits to a
default item (eg rewriting the answer) are preserved across
restarts because the merge happens per ``item_key``: if the key
already exists, we leave the stored row alone.

Duplicate-question protection: a default is also skipped if its
normalised question text already appears anywhere in the overlay
under a DIFFERENT ``item_key``. This handles the edge case where
an admin already added the same question manually before the
default was introduced.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from schemas.faq_schema import (
    FaqItem,
    FaqOverlayCreate,
    FaqOverlayPatch,
    normalize_question_key,
)
from repositories.faq_repo import create_overlay, get_overlay, replace_overlay

logger = logging.getLogger(__name__)


# ── The five seed FAQs ──────────────────────────────────────────────


_DEFAULT_FAQ_ITEMS: List[Dict[str, Any]] = [
    {
        "item_key": "do-we-need-special-hardware",
        "question": "Do we need special hardware to use VisiChek?",
        "answer": (
            "No. VisiChek works with standard reception laptops, "
            "tablets, webcams, and badge printers. If your building "
            "already uses QR scanners, access control doors, or "
            "turnstiles, VisiChek can integrate with them as part of "
            "an upgraded setup."
        ),
        "category_key": "general",
        "sort_order": 10,
    },
    {
        "item_key": "can-verify-nigerian-ids",
        "question": "Can VisiChek verify Nigerian government-issued IDs?",
        "answer": (
            "Yes. VisiChek is designed to support government-issued "
            "identification commonly used in Nigeria and extracts "
            "visitor information automatically during check-in."
        ),
        "category_key": "security",
        "sort_order": 20,
    },
    {
        "item_key": "where-is-data-stored-ndpa",
        "question": "Where is our visitor data stored, and is it NDPA compliant?",
        "answer": (
            "VisiChek supports encrypted visitor records, role-based "
            "access control, and configurable data retention policies. "
            "For organizations with compliance requirements, deployment "
            "options can support local hosting or approved "
            "infrastructure aligned with NDPA expectations."
        ),
        "category_key": "security",
        "sort_order": 30,
    },
    {
        "item_key": "multi-department-multi-branch",
        "question": "Can multiple departments or branches use the same system?",
        "answer": (
            "Yes. Each department manages its own visitors "
            "independently, while administrators maintain "
            "company-wide visibility. VisiChek also supports "
            "multi-branch setups from a single centralized dashboard."
        ),
        "category_key": "general",
        "sort_order": 40,
    },
    {
        "item_key": "how-is-visichek-priced",
        "question": "How is VisiChek priced?",
        "answer": (
            "VisiChek uses a subscription model based on your "
            "organization's setup, including number of departments, "
            "locations, and check-in workflow requirements such as QR "
            "or hardware integrations. Most organizations start with "
            "a reception-level deployment and expand as needed."
        ),
        "category_key": "billing",
        "sort_order": 50,
    },
]


async def ensure_default_faqs() -> Dict[str, Any]:
    """Seed missing default FAQ items. Idempotent.

    Returns a small summary dict for the startup log so operators can
    see whether anything was actually inserted on this boot.
    """
    overlay = await get_overlay()

    # First-run fast path — no overlay document at all. Create the
    # whole seed in one shot. Schema validators strip whitespace and
    # the patch-level validator catches in-payload duplicates, so we
    # don't need separate guards here.
    if overlay is None:
        seed_items = [FaqItem(**item) for item in _DEFAULT_FAQ_ITEMS]
        await create_overlay(
            FaqOverlayCreate(
                headline=None,
                subheadline=None,
                footer_html=None,
                items=seed_items,
                categories=[],
            )
        )
        return {
            "created_overlay": True,
            "inserted_items": [item["item_key"] for item in _DEFAULT_FAQ_ITEMS],
            "skipped_existing_keys": [],
            "skipped_duplicate_questions": [],
        }

    # Conservative merge — only add items that aren't already there
    # by item_key OR by normalised question text.
    existing_item_keys = {item.item_key for item in overlay.items}
    existing_question_keys = {
        normalize_question_key(item.question) for item in overlay.items
    }

    to_insert: List[Dict[str, Any]] = []
    skipped_existing: List[str] = []
    skipped_dup_questions: List[str] = []

    for default in _DEFAULT_FAQ_ITEMS:
        if default["item_key"] in existing_item_keys:
            skipped_existing.append(default["item_key"])
            continue
        qkey = normalize_question_key(default["question"])
        if qkey in existing_question_keys:
            skipped_dup_questions.append(default["item_key"])
            continue
        to_insert.append(default)

    if not to_insert:
        return {
            "created_overlay": False,
            "inserted_items": [],
            "skipped_existing_keys": skipped_existing,
            "skipped_duplicate_questions": skipped_dup_questions,
        }

    # Build a PATCH-style merge — apply via the repo directly so we
    # bypass the regular service-layer ``apply_overlay_patch`` (which
    # would invoke audit logging and FastAPI HTTPException paths we
    # don't want firing during silent startup work).
    merged_items = [
        item.model_dump(exclude_none=True) for item in overlay.items
    ] + to_insert

    overlay_dict = overlay.model_dump(by_alias=False, exclude={"id"})
    overlay_dict["items"] = merged_items
    import time

    overlay_dict["last_updated"] = int(time.time())
    overlay_dict.pop("_id", None)
    overlay_dict.pop("id", None)

    await replace_overlay(overlay_dict)

    # Drop the precompute so the next GET picks up the new items.
    # delete_precompute is scope-aware; faqs.template is GLOBAL so no
    # tenant / user kwargs are required.
    try:
        from core.queue.precompute import delete_precompute

        delete_precompute("faqs.template")
    except Exception:
        logger.debug("faqs.template precompute drop failed (non-fatal)", exc_info=True)

    return {
        "created_overlay": False,
        "inserted_items": [item["item_key"] for item in to_insert],
        "skipped_existing_keys": skipped_existing,
        "skipped_duplicate_questions": skipped_dup_questions,
    }


# Re-export for use by tests / one-off scripts.
__all__ = ["ensure_default_faqs", "_DEFAULT_FAQ_ITEMS", "FaqOverlayPatch"]
