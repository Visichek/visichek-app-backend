"""Pricing-marketing template service.

Renders the public pricing page from three live sources:

1. The active+public subscription plans (excluding bespoke
   ``enterprise-<customer>`` slugs, which are tenant-private).
2. The togglable-feature catalog from
   :mod:`services.plan_feature_service` — adds one row per feature.
3. The togglable / numeric fields on each plan doc itself: tenant caps,
   storage limits, CRUD/retrieval quotas, support tier, SLA, trial
   days, ``custom_branding`` / ``api_access`` / ``priority_support``.

A persisted overlay document (``pricing_marketing`` collection) lets
application admins layer marketing copy — taglines, CTAs, bullet
lists, row labels/descriptions, category names — without touching plan
internals. The overlay never stores values; it owns text only. Values
always come from the live plan, which is what guarantees the page
stays in sync with the actual catalogue.

Removed entities (a plan archived, a feature dropped from every plan)
drop out of the rendered response automatically. Their overlay copy
stays in the document so that if the entity comes back, the copy comes
back with it.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from schemas.pricing_marketing_schema import (
    PricingComparisonCell,
    PricingComparisonRow,
    PricingComparisonSection,
    PricingMarketingOut,
    PricingMarketingOverlayCreate,
    PricingMarketingOverlayOut,
    PricingMarketingOverlayPatch,
    PricingPlanCard,
)
from schemas.plan_schema import PlanOut, PlanStatus, PlanTier
from repositories.pricing_marketing_repo import (
    create_overlay,
    get_overlay,
    replace_overlay,
)
from services.plan_feature_service import get_feature_catalog
from services.plan_service import retrieve_plans

logger = logging.getLogger(__name__)


# ── Defaults for the hero copy ───────────────────────────────────────
# Returned when no overlay row sets these. Overlay PATCH overrides them.

DEFAULT_HEADLINE = "Pricing for security-first teams"
DEFAULT_SUBHEADLINE = (
    "Scales with locations, departments, rollout needs, and the "
    "security workflow you need on day one."
)


# ── Default highlight bullets per canonical plan slug ─────────────────
# Surfaced under each pricing card when the overlay doesn't override
# them. Sourced from the canonical plan defaults in
# ``config/plan_tiers.py`` (caps + features) so the bullets stay
# truthful even if no admin ever edits the marketing overlay.
#
# Custom enterprise slugs (``enterprise-<customer>``) get the generic
# enterprise bullets unless an overlay row gives them custom copy.

DEFAULT_PLAN_BULLETS: Dict[str, List[str]] = {
    "free": [
        "Manual visitor check-in / check-out",
        "Up to 50 visitors per month",
        "1 location, 1 department, 1 seat",
        "Searchable visitor logs",
        "Encrypted database",
        "Email support",
    ],
    "starter": [
        "Everything in Free",
        "QR self check-in",
        "Badge printing",
        "Up to 150 visitors per month",
        "Up to 5 team seats, 3 departments",
        "Standard support",
    ],
    "premium": [
        "Everything in Starter",
        "Multi-location — unlimited branches",
        "Appointments + host email notifications",
        "Custom branding (logos, badge styling)",
        "ID verification (Dojah KYC)",
        "CSV export of visitor logs & compliance reports",
        "API access",
        "Up to 50 seats, 15 departments per location",
        "24-hour SLA on support",
    ],
    "enterprise": [
        "Everything in Premium",
        "Unlimited seats, departments, visitors",
        "Watchlist / flagged-visitor controls",
        "SSO (Azure AD / Google Workspace)",
        "Private cloud or on-prem deployment",
        "Dedicated technical account manager",
        "4-hour SLA on priority support",
        "Custom integrations & workflows",
    ],
}


# ── Canonical category catalog ───────────────────────────────────────
# Default category labels + ordering. Admins can override any of these
# via PATCH (a category with the same ``category_key`` in the overlay
# replaces the default).

DEFAULT_CATEGORIES: List[Dict[str, Any]] = [
    {"category_key": "capacity", "label": "Capacity & limits", "sort_order": 10},
    {"category_key": "features", "label": "Features", "sort_order": 20},
    {"category_key": "storage", "label": "Storage", "sort_order": 30},
    {"category_key": "throughput", "label": "Throughput", "sort_order": 40},
    {"category_key": "support_sla", "label": "Support & SLA", "sort_order": 50},
    {"category_key": "billing", "label": "Billing", "sort_order": 60},
]


# Maps each well-known row source to its default category.
_TENANT_CAP_CATEGORY = "capacity"
_STORAGE_CATEGORY = "storage"
_THROUGHPUT_CATEGORY = "throughput"
_FEATURE_CATEGORY = "features"
_FLAG_CATEGORY = "features"
_SUPPORT_CATEGORY = "support_sla"
_BILLING_CATEGORY = "billing"


# Human labels for the known tenant_caps numeric fields.
_TENANT_CAP_LABELS: Dict[str, str] = {
    "max_system_users": "Team seats",
    "max_departments": "Departments",
    "max_branches": "Branches / locations",
    "max_visitors_per_month": "Visitors per month",
    "max_appointments_per_month": "Appointments per month",
}

# Human labels for storage fields.
_STORAGE_LABELS: Dict[str, str] = {
    "max_documents": "Total documents",
    "max_storage_mb": "Total storage",
    "max_file_size_mb": "Max file size",
}

# Plan-level boolean / scalar flags surfaced as comparison rows.
_FLAG_ROWS: List[Dict[str, Any]] = [
    {
        "row_key": "flag.priority_support",
        "attr": "priority_support",
        "label": "Priority support",
        "category_key": _SUPPORT_CATEGORY,
        "kind": "bool",
    },
    {
        "row_key": "flag.custom_branding",
        "attr": "custom_branding",
        "label": "Custom branding",
        "category_key": _FLAG_CATEGORY,
        "kind": "bool",
    },
    {
        "row_key": "flag.api_access",
        "attr": "api_access",
        "label": "API access",
        "category_key": _FLAG_CATEGORY,
        "kind": "bool",
    },
    {
        "row_key": "flag.sla",
        "attr": "sla_response_hours",
        "label": "SLA response time",
        "category_key": _SUPPORT_CATEGORY,
        "kind": "hours",
    },
    {
        "row_key": "flag.trial",
        "attr": "trial_days",
        "label": "Free trial",
        "category_key": _BILLING_CATEGORY,
        "kind": "days",
    },
    {
        "row_key": "flag.support_tier",
        "attr": "support_tier",
        "label": "Support tier",
        "category_key": _SUPPORT_CATEGORY,
        "kind": "enum",
    },
]


# ── Cell formatters ──────────────────────────────────────────────────


def _format_bool(value: Any) -> str:
    return "✓" if bool(value) else "—"


def _format_int_or_unlimited(value: Any, *, enterprise: bool = False) -> str:
    if value is None:
        return "Custom" if enterprise else "Unlimited"
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value)
    if n <= 0 and enterprise:
        return "Custom"
    return f"{n:,}"


def _format_storage_mb(value: Any, *, enterprise: bool = False) -> str:
    if value is None:
        return "Custom" if enterprise else "Unlimited"
    try:
        mb = int(value)
    except (TypeError, ValueError):
        return str(value)
    if mb >= 1024 and mb % 1024 == 0:
        return f"{mb // 1024} GB"
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb} MB"


def _format_file_size_mb(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value)} MB"
    except (TypeError, ValueError):
        return str(value)


def _format_hours(value: Any) -> str:
    if value is None or value == 0:
        return "—"
    try:
        h = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{h}h"


def _format_days(value: Any) -> str:
    if value is None or value == 0:
        return "—"
    try:
        d = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{d}-day"


def _format_enum(value: Any) -> str:
    if value is None:
        return "—"
    raw = getattr(value, "value", None)
    s = raw if isinstance(raw, str) else str(value)
    if not s or s.lower() in {"none", ""}:
        return "—"
    return s.replace("_", " ").title()


# ── Plan-side helpers ────────────────────────────────────────────────


def _is_enterprise(plan: PlanOut) -> bool:
    tier = getattr(plan, "tier", None)
    tier_val = getattr(tier, "value", tier)
    return tier_val == PlanTier.ENTERPRISE.value


def _feature_enabled_on_plan(plan: PlanOut, endpoint_pattern: str) -> bool:
    """A togglable feature is 'enabled' when there is no DENY rule whose
    ``endpoint_pattern`` matches ours. Deny-list semantics mirror the
    plan-enforcement middleware.
    """
    for rule in getattr(plan, "feature_rules", []) or []:
        if rule.endpoint_pattern == endpoint_pattern:
            return bool(rule.enabled)
    # No explicit rule → allowed (middleware contract).
    return True


def _crud_limit_for(plan: PlanOut, collection: str, op: str) -> Optional[int]:
    for limit in getattr(plan, "crud_limits", []) or []:
        if limit.collection != collection:
            continue
        attr = f"max_{op}"
        val = getattr(limit, attr, None)
        if val is not None:
            return int(val)
    return None


def _retrieval_quota_for(plan: PlanOut, collection: str) -> Optional[int]:
    for q in getattr(plan, "retrieval_quotas", []) or []:
        if q.collection == collection:
            return q.max_reads
    return None


# ── Visible-plan collapse ────────────────────────────────────────────


def _filter_visible_plans(plans: List[PlanOut]) -> List[PlanOut]:
    """Return the plans that show on the public pricing page.

    Rules:
    * ACTIVE only (drafts and archived plans are hidden).
    * ``is_public=True`` only.
    * Exactly one enterprise card — the canonical template slug
      ``"enterprise"``. Bespoke ``enterprise-<customer>`` plans are
      always ``is_public=False`` by construction but we filter again
      defensively here.
    """
    visible: List[PlanOut] = []
    enterprise_seen = False
    for plan in plans:
        status = plan.status.value if hasattr(plan.status, "value") else plan.status
        if status != PlanStatus.ACTIVE.value:
            continue
        if not getattr(plan, "is_public", False):
            continue
        if _is_enterprise(plan):
            if plan.name != "enterprise":
                # Bespoke per-customer enterprise plan — never show.
                continue
            if enterprise_seen:
                continue
            enterprise_seen = True
        visible.append(plan)

    def _sort_key(p: PlanOut) -> tuple:
        tier_order = {"free": 0, "starter": 1, "premium": 2, "enterprise": 3}
        tier_val = p.tier.value if hasattr(p.tier, "value") else p.tier
        return (getattr(p, "sort_order", 0) or 0, tier_order.get(tier_val, 99))

    visible.sort(key=_sort_key)
    return visible


# ── Overlay lookup helpers ───────────────────────────────────────────


def _index_by(items: List[Any], attr: str) -> Dict[str, Any]:
    return {getattr(it, attr): it for it in items if getattr(it, attr, None)}


# ── Plan cards (summary table) ───────────────────────────────────────


def _default_bullets_for_plan(plan: PlanOut) -> List[str]:
    """Pick the ship-with-code bullets for a plan slug.

    Bespoke ``enterprise-<customer>`` slugs fall back to the generic
    enterprise bullets. Unknown slugs get an empty list so the card
    renders without bullets rather than wrong ones.
    """
    if plan.name in DEFAULT_PLAN_BULLETS:
        return list(DEFAULT_PLAN_BULLETS[plan.name])
    if _is_enterprise(plan):
        return list(DEFAULT_PLAN_BULLETS["enterprise"])
    return []


def _build_plan_card(
    plan: PlanOut,
    overlay: Optional[PricingMarketingOverlayOut],
) -> PricingPlanCard:
    plans_copy = _index_by(overlay.plans, "plan_name") if overlay else {}
    copy = plans_copy.get(plan.name)
    enterprise = _is_enterprise(plan)

    cta_label = (copy.cta_label if copy and copy.cta_label else None) or (
        "Contact sales" if enterprise else f"Start with {plan.display_name}"
    )

    # Bullets: overlay wins; otherwise fall back to the per-slug defaults
    # baked into DEFAULT_PLAN_BULLETS. A copy entry with an empty list is
    # treated as "no bullets" (admin intentionally cleared them).
    if copy is not None and copy.highlight_bullets is not None:
        bullets = list(copy.highlight_bullets)
    else:
        bullets = _default_bullets_for_plan(plan)

    return PricingPlanCard(
        plan_id=plan.id or "",
        plan_name=plan.name,
        display_name=plan.display_name,
        tier=plan.tier.value if hasattr(plan.tier, "value") else str(plan.tier),
        tagline=(copy.tagline if copy else None) or plan.description,
        price_monthly=None if enterprise else plan.base_price_monthly,
        price_yearly=None if enterprise else plan.base_price_yearly,
        currency=(overlay.currency_display if overlay and overlay.currency_display else plan.currency),
        cta_label=cta_label,
        cta_url=(copy.cta_url if copy else None),
        badge=(copy.badge if copy else None),
        highlight_bullets=bullets,
        sort_order=getattr(plan, "sort_order", 0) or 0,
    )


# ── Row collection (feature + cap + storage + throughput rows) ───────


def _row_inventory(plans: List[PlanOut]) -> List[Dict[str, Any]]:
    """Walk every visible plan and emit one descriptor per surfaced row.

    Each descriptor is a dict with at least ``row_key``, ``label``,
    ``category_key``, and ``compute(plan)`` — a callable that returns
    a ``(value, display)`` tuple for one plan. Duplicate row_keys are
    merged on first sight so the inventory is stable across plans.
    """
    seen: Dict[str, Dict[str, Any]] = {}

    # 1) Togglable features from the catalog (only if the row would
    #    show a value on at least one plan — which is always true,
    #    since the rule defaults to allow).
    for spec in get_feature_catalog():
        row_key = f"feature.{spec.key}"
        endpoint_pattern = spec.endpoint_pattern

        def _compute_feature(
            plan: PlanOut, _ep: str = endpoint_pattern
        ) -> tuple[Any, str]:
            enabled = _feature_enabled_on_plan(plan, _ep)
            return (enabled, _format_bool(enabled))

        seen.setdefault(
            row_key,
            {
                "row_key": row_key,
                "label": spec.label,
                "description": spec.description,
                "category_key": _FEATURE_CATEGORY,
                "compute": _compute_feature,
            },
        )

    # 2) Plan-level flags / numerics (priority_support, custom_branding,
    #    api_access, sla_response_hours, trial_days, support_tier).
    for flag in _FLAG_ROWS:
        row_key = flag["row_key"]
        attr = flag["attr"]
        kind = flag["kind"]

        def _compute_flag(
            plan: PlanOut, _attr: str = attr, _kind: str = kind
        ) -> tuple[Any, str]:
            value = getattr(plan, _attr, None)
            if _kind == "bool":
                return (bool(value), _format_bool(value))
            if _kind == "hours":
                return (value, _format_hours(value))
            if _kind == "days":
                return (value, _format_days(value))
            if _kind == "enum":
                raw = getattr(value, "value", value)
                return (raw, _format_enum(value))
            return (value, str(value) if value is not None else "—")

        seen.setdefault(
            row_key,
            {
                "row_key": row_key,
                "label": flag["label"],
                "description": None,
                "category_key": flag["category_key"],
                "compute": _compute_flag,
            },
        )

    # 3) Tenant caps — one row per distinct cap field that has a value
    #    on at least one plan (None is meaningful: "Unlimited").
    for cap_field, cap_label in _TENANT_CAP_LABELS.items():
        # Include the row unconditionally — Free's max_branches=1 is
        # already a value, and Enterprise's None becomes "Custom".
        row_key = f"cap.{cap_field}"

        def _compute_cap(
            plan: PlanOut, _field: str = cap_field
        ) -> tuple[Any, str]:
            caps = getattr(plan, "tenant_caps", None)
            value = getattr(caps, _field, None) if caps else None
            display = _format_int_or_unlimited(value, enterprise=_is_enterprise(plan))
            return (value, display)

        seen.setdefault(
            row_key,
            {
                "row_key": row_key,
                "label": cap_label,
                "description": None,
                "category_key": _TENANT_CAP_CATEGORY,
                "compute": _compute_cap,
            },
        )

    # 4) Storage limits.
    for storage_field, storage_label in _STORAGE_LABELS.items():
        row_key = f"storage.{storage_field}"

        def _compute_storage(
            plan: PlanOut,
            _field: str = storage_field,
        ) -> tuple[Any, str]:
            storage = getattr(plan, "storage_limits", None)
            value = getattr(storage, _field, None) if storage else None
            if _field == "max_storage_mb":
                return (value, _format_storage_mb(value, enterprise=_is_enterprise(plan)))
            if _field == "max_file_size_mb":
                return (value, _format_file_size_mb(value))
            return (value, _format_int_or_unlimited(value, enterprise=_is_enterprise(plan)))

        seen.setdefault(
            row_key,
            {
                "row_key": row_key,
                "label": storage_label,
                "description": None,
                "category_key": _STORAGE_CATEGORY,
                "compute": _compute_storage,
            },
        )

    # 5) CRUD limits — union of (collection, op) across all plans. We
    #    skip a (collection, op) entirely when no plan declares a cap
    #    for it (otherwise every plan would get "Unlimited" everywhere
    #    and the row would be noise).
    crud_pairs: set[tuple[str, str]] = set()
    for plan in plans:
        for limit in getattr(plan, "crud_limits", []) or []:
            for op in ("create", "update", "delete"):
                if getattr(limit, f"max_{op}", None) is not None:
                    crud_pairs.add((limit.collection, op))

    for collection, op in sorted(crud_pairs):
        row_key = f"limit.crud.{collection}.{op}"
        label = f"{collection.replace('_', ' ').title()} — {op}s"

        def _compute_crud(
            plan: PlanOut, _c: str = collection, _o: str = op
        ) -> tuple[Any, str]:
            value = _crud_limit_for(plan, _c, _o)
            return (value, _format_int_or_unlimited(value, enterprise=_is_enterprise(plan)))

        seen.setdefault(
            row_key,
            {
                "row_key": row_key,
                "label": label,
                "description": None,
                "category_key": _THROUGHPUT_CATEGORY,
                "compute": _compute_crud,
            },
        )

    # 6) Retrieval quotas — same idea.
    read_collections: set[str] = set()
    for plan in plans:
        for q in getattr(plan, "retrieval_quotas", []) or []:
            if q.max_reads is not None:
                read_collections.add(q.collection)

    for collection in sorted(read_collections):
        row_key = f"limit.read.{collection}"
        label = f"{collection.replace('_', ' ').title()} — reads"

        def _compute_read(
            plan: PlanOut, _c: str = collection
        ) -> tuple[Any, str]:
            value = _retrieval_quota_for(plan, _c)
            return (value, _format_int_or_unlimited(value, enterprise=_is_enterprise(plan)))

        seen.setdefault(
            row_key,
            {
                "row_key": row_key,
                "label": label,
                "description": None,
                "category_key": _THROUGHPUT_CATEGORY,
                "compute": _compute_read,
            },
        )

    return list(seen.values())


# ── Section assembly ────────────────────────────────────────────────


def _resolve_categories(
    overlay: Optional[PricingMarketingOverlayOut],
) -> Dict[str, Dict[str, Any]]:
    """Return ``{category_key: {label, sort_order}}`` merging defaults
    with overlay overrides."""
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


def _build_sections(
    rows_meta: List[Dict[str, Any]],
    plans: List[PlanOut],
    overlay: Optional[PricingMarketingOverlayOut],
) -> List[PricingComparisonSection]:
    features_copy = _index_by(overlay.features, "row_key") if overlay else {}
    categories = _resolve_categories(overlay)

    # group rows by category
    by_cat: Dict[str, List[PricingComparisonRow]] = {}
    for meta in rows_meta:
        copy = features_copy.get(meta["row_key"])
        category_key = (copy.category_key if copy and copy.category_key else None) or meta[
            "category_key"
        ]
        label = (copy.label if copy and copy.label else None) or meta["label"]
        description = (
            (copy.description if copy and copy.description else None)
            or meta.get("description")
        )

        cells: List[PricingComparisonCell] = []
        for plan in plans:
            value, display = meta["compute"](plan)
            cells.append(
                PricingComparisonCell(
                    plan_name=plan.name,
                    value=value,
                    display=display,
                )
            )

        row = PricingComparisonRow(
            row_key=meta["row_key"],
            label=label,
            description=description,
            cells=cells,
        )
        by_cat.setdefault(category_key, []).append(row)

    sections: List[PricingComparisonSection] = []
    for category_key, rows in by_cat.items():
        cat_meta = categories.get(
            category_key,
            {"category_key": category_key, "label": category_key, "sort_order": 999},
        )
        sections.append(
            PricingComparisonSection(
                category_key=category_key,
                label=cat_meta["label"],
                sort_order=cat_meta["sort_order"],
                rows=rows,
            )
        )

    sections.sort(key=lambda s: (s.sort_order, s.label))
    return sections


# ── Public render entrypoint ─────────────────────────────────────────


async def render_pricing_marketing() -> PricingMarketingOut:
    """Build the full rendered pricing-marketing payload."""
    overlay = await get_overlay()
    plans = await retrieve_plans(start=0, stop=100)
    visible = _filter_visible_plans(plans)

    plan_cards = [_build_plan_card(p, overlay) for p in visible]
    rows_meta = _row_inventory(visible)
    sections = _build_sections(rows_meta, visible, overlay)

    currency = (
        overlay.currency_display
        if overlay and overlay.currency_display
        else (visible[0].currency if visible else "NGN")
    )

    headline = (overlay.headline if overlay and overlay.headline else None) or DEFAULT_HEADLINE
    subheadline = (
        overlay.subheadline if overlay and overlay.subheadline else None
    ) or DEFAULT_SUBHEADLINE

    return PricingMarketingOut(
        headline=headline,
        subheadline=subheadline,
        currency=currency,
        plans=plan_cards,
        sections=sections,
        last_updated=int(overlay.last_updated) if overlay and overlay.last_updated else int(time.time()),
    )


# ── Overlay PATCH merge logic ────────────────────────────────────────


def _merge_list(
    existing: List[Any],
    incoming: Optional[List[Any]],
    key_attr: str,
) -> List[Any]:
    """Upsert each item in ``incoming`` into ``existing`` by ``key_attr``.

    Passing ``incoming = None`` leaves the list untouched. Passing an
    empty list (``[]``) clears the list. Items with a matching key
    REPLACE the existing entry; new keys are appended.
    """
    if incoming is None:
        return existing
    if not incoming:
        return []
    by_key: Dict[str, Any] = {}
    for item in existing:
        k = item.get(key_attr) if isinstance(item, dict) else getattr(item, key_attr, None)
        if k:
            by_key[k] = item
    out: List[Any] = []
    for item in incoming:
        # ``incoming`` is a list of pydantic models from the patch — convert.
        as_dict = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)
        key = as_dict.get(key_attr)
        if not key:
            continue
        by_key[key] = as_dict
    # Preserve insertion order: existing keys first (in their order),
    # then any new keys appended at the end.
    existing_keys = []
    for item in existing:
        k = item.get(key_attr) if isinstance(item, dict) else getattr(item, key_attr, None)
        if k and k in by_key and k not in existing_keys:
            existing_keys.append(k)
    out.extend(by_key[k] for k in existing_keys)
    for k, v in by_key.items():
        if k not in existing_keys:
            out.append(v)
    return out


async def apply_overlay_patch(
    patch: PricingMarketingOverlayPatch,
) -> PricingMarketingOverlayOut:
    """Merge ``patch`` onto the persisted overlay (creating it if absent).

    Returns the post-merge overlay document.
    """
    current = await get_overlay()
    if current is None:
        # Fresh — bootstrap with whatever the patch carries.
        seed = PricingMarketingOverlayCreate(
            headline=patch.headline,
            subheadline=patch.subheadline,
            currency_display=patch.currency_display,
            plans=patch.plans or [],
            features=patch.features or [],
            categories=patch.categories or [],
        )
        return await create_overlay(seed)

    current_dict = current.model_dump(by_alias=False, exclude={"id"})
    if patch.headline is not None:
        current_dict["headline"] = patch.headline
    if patch.subheadline is not None:
        current_dict["subheadline"] = patch.subheadline
    if patch.currency_display is not None:
        current_dict["currency_display"] = patch.currency_display

    current_dict["plans"] = _merge_list(
        current_dict.get("plans", []), patch.plans, "plan_name"
    )
    current_dict["features"] = _merge_list(
        current_dict.get("features", []), patch.features, "row_key"
    )
    current_dict["categories"] = _merge_list(
        current_dict.get("categories", []), patch.categories, "category_key"
    )

    current_dict["last_updated"] = int(time.time())
    current_dict.pop("date_created", None)  # never overwrite the original
    current_dict.pop("_id", None)
    current_dict.pop("id", None)

    # Preserve date_created on replace.
    if current.date_created is not None:
        current_dict["date_created"] = current.date_created

    refreshed = await replace_overlay(current_dict)
    if refreshed is None:
        # Should not happen — upsert always lands a doc.
        raise RuntimeError("pricing_marketing: replace_overlay returned None")
    return refreshed


async def delete_overlay_row(kind: str, key: str) -> PricingMarketingOverlayOut:
    """Remove one overlay row (plan / feature / category) by its natural key.

    No-ops when the overlay doesn't exist or the row isn't present, so
    callers can use this as an "ensure removed" primitive.
    """
    current = await get_overlay()
    if current is None:
        # Nothing to delete — return a synthetic empty overlay so the
        # writer audit log gets a consistent shape.
        return PricingMarketingOverlayOut(
            headline=None,
            subheadline=None,
            currency_display=None,
            plans=[],
            features=[],
            categories=[],
            date_created=int(time.time()),
            last_updated=int(time.time()),
        )

    current_dict = current.model_dump(by_alias=False, exclude={"id"})
    if kind == "plan":
        current_dict["plans"] = [
            p for p in current_dict.get("plans", []) if p.get("plan_name") != key
        ]
    elif kind == "feature":
        current_dict["features"] = [
            f for f in current_dict.get("features", []) if f.get("row_key") != key
        ]
    elif kind == "category":
        current_dict["categories"] = [
            c for c in current_dict.get("categories", []) if c.get("category_key") != key
        ]
    else:
        raise ValueError(f"Unknown pricing_marketing row kind: {kind}")

    current_dict["last_updated"] = int(time.time())
    current_dict.pop("_id", None)
    current_dict.pop("id", None)
    if current.date_created is not None:
        current_dict["date_created"] = current.date_created

    refreshed = await replace_overlay(current_dict)
    if refreshed is None:
        raise RuntimeError("pricing_marketing: replace_overlay returned None")
    return refreshed
