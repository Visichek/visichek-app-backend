"""Unit tests for Task 12: the branch add-on on the marketing pricing page.

Covers the ``addons`` section of ``PricingMarketingOut`` — live price
resolution via ``resolve_addon_unit_price``, overlay blurb/visibility
overrides, ``requires_plan`` derivation for derived-pricing addons, and
the overlay PATCH/DELETE merge-by-slug contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from schemas.addon_schema import AddonKind, AddonOut, AddonStatus
from schemas.pricing_marketing_schema import (
    PricingAddonCopy,
    PricingMarketingOverlayOut,
    PricingMarketingOverlayPatch,
)

pytestmark = pytest.mark.asyncio


def _branch_addon(**overrides) -> AddonOut:
    base = dict(
        _id="addon1",
        slug="additional-branch",
        name="Additional Branch",
        description="Ship-with-code catalog description",
        kind=AddonKind.BRANCH_QUOTA,
        status=AddonStatus.ACTIVE,
        unit_price=100_000.0,
        currency="NGN",
        pricing_mode="derived",
        derived_from={"plan": "premium", "field": "base_price_monthly", "multiplier": 0.8},
        recurring=True,
        billing_cycle="monthly",
        benefit_per_unit={"branches": 1},
    )
    base.update(overrides)
    return AddonOut(**base)


# ─── _build_addon_cards ───────────────────────────────────────────────


async def test_addon_card_uses_live_resolved_price() -> None:
    from services.pricing_marketing_service import _build_addon_cards

    addon = _branch_addon(unit_price=999.0)  # stale cached value
    with (
        patch("services.pricing_marketing_service.list_addons", new=AsyncMock(return_value=[addon])),
        patch(
            "services.pricing_marketing_service.resolve_addon_unit_price",
            new=AsyncMock(return_value=160_000.0),
        ),
    ):
        cards = await _build_addon_cards(None)

    assert len(cards) == 1
    card = cards[0]
    assert card.slug == "additional-branch"
    assert card.price_monthly == 160_000.0
    assert card.currency == "NGN"


async def test_addon_card_default_blurb_and_requires_plan() -> None:
    from services.pricing_marketing_service import _build_addon_cards

    addon = _branch_addon()
    with (
        patch("services.pricing_marketing_service.list_addons", new=AsyncMock(return_value=[addon])),
        patch(
            "services.pricing_marketing_service.resolve_addon_unit_price",
            new=AsyncMock(return_value=120_000.0),
        ),
    ):
        cards = await _build_addon_cards(None)

    card = cards[0]
    assert "20% off Premium" in (card.blurb or "")
    assert "tenant" not in (card.blurb or "").lower()
    assert card.requires_plan == "premium"
    assert card.visible is True


async def test_addon_card_overlay_overrides_blurb_and_visibility() -> None:
    from services.pricing_marketing_service import _build_addon_cards

    addon = _branch_addon()
    overlay = PricingMarketingOverlayOut(
        addons=[
            PricingAddonCopy(
                slug="additional-branch",
                blurb="Custom marketing copy for the branch add-on",
                visible=False,
            )
        ],
    )
    with (
        patch("services.pricing_marketing_service.list_addons", new=AsyncMock(return_value=[addon])),
        patch(
            "services.pricing_marketing_service.resolve_addon_unit_price",
            new=AsyncMock(return_value=120_000.0),
        ),
    ):
        cards = await _build_addon_cards(overlay)

    card = cards[0]
    assert card.blurb == "Custom marketing copy for the branch add-on"
    assert card.visible is False


async def test_fixed_pricing_addon_has_no_requires_plan() -> None:
    from services.pricing_marketing_service import _build_addon_cards

    addon = _branch_addon(
        slug="storage-extension",
        name="Storage",
        pricing_mode="fixed",
        derived_from=None,
        recurring=False,
    )
    with (
        patch("services.pricing_marketing_service.list_addons", new=AsyncMock(return_value=[addon])),
        patch(
            "services.pricing_marketing_service.resolve_addon_unit_price",
            new=AsyncMock(return_value=2_000.0),
        ),
    ):
        cards = await _build_addon_cards(None)

    assert cards[0].requires_plan is None


# ─── render_pricing_marketing wiring ──────────────────────────────────


async def test_render_pricing_marketing_includes_addons_section() -> None:
    from services.pricing_marketing_service import render_pricing_marketing
    from schemas.plan_schema import PlanOut, PlanStatus, PlanTier

    premium = PlanOut(
        _id="p1",
        name="premium",
        display_name="Premium",
        tier=PlanTier.PREMIUM,
        status=PlanStatus.ACTIVE,
        is_public=True,
        base_price_monthly=150_000.0,
        base_price_yearly=1_500_000.0,
        currency="NGN",
    )
    addon = _branch_addon()

    with (
        patch("services.pricing_marketing_service.get_overlay", new=AsyncMock(return_value=None)),
        patch("services.pricing_marketing_service.retrieve_plans", new=AsyncMock(return_value=[premium])),
        patch("services.pricing_marketing_service.get_feature_catalog", return_value=[]),
        patch("services.pricing_marketing_service.list_addons", new=AsyncMock(return_value=[addon])),
        patch(
            "services.pricing_marketing_service.resolve_addon_unit_price",
            new=AsyncMock(return_value=120_000.0),
        ),
    ):
        out = await render_pricing_marketing()

    assert len(out.addons) == 1
    assert out.addons[0].slug == "additional-branch"
    assert out.addons[0].price_monthly == 120_000.0


# ─── Overlay PATCH/DELETE merge-by-slug ───────────────────────────────


async def test_apply_overlay_patch_merges_addons_by_slug() -> None:
    from services.pricing_marketing_service import apply_overlay_patch

    existing = PricingMarketingOverlayOut(
        addons=[PricingAddonCopy(slug="additional-branch", blurb="Old copy")],
    )
    patch_payload = PricingMarketingOverlayPatch(
        addons=[PricingAddonCopy(slug="additional-branch", blurb="New copy")]
    )

    async def _fake_replace(payload: dict):
        return PricingMarketingOverlayOut(**payload)

    with (
        patch("services.pricing_marketing_service.get_overlay", new=AsyncMock(return_value=existing)),
        patch("services.pricing_marketing_service.replace_overlay", new=AsyncMock(side_effect=_fake_replace)),
    ):
        refreshed = await apply_overlay_patch(patch_payload)

    assert len(refreshed.addons) == 1
    assert refreshed.addons[0].blurb == "New copy"


async def test_delete_overlay_row_removes_addon_by_slug() -> None:
    from services.pricing_marketing_service import delete_overlay_row

    existing = PricingMarketingOverlayOut(
        addons=[
            PricingAddonCopy(slug="additional-branch", blurb="Keep me? no"),
            PricingAddonCopy(slug="other-addon", blurb="Stays"),
        ],
    )

    async def _fake_replace(payload: dict):
        return PricingMarketingOverlayOut(**payload)

    with (
        patch("services.pricing_marketing_service.get_overlay", new=AsyncMock(return_value=existing)),
        patch("services.pricing_marketing_service.replace_overlay", new=AsyncMock(side_effect=_fake_replace)),
    ):
        refreshed = await delete_overlay_row("addon", "additional-branch")

    slugs = [a["slug"] if isinstance(a, dict) else a.slug for a in refreshed.addons]
    assert slugs == ["other-addon"]
