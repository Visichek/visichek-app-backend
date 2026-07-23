"""Unit tests for Task 7: branch add-on catalog + derived pricing.

Covers ``resolve_addon_unit_price`` / ``resolve_derived_addon_price``,
the Premium-write resync hook, purchase-time tier gating, purchase-time
snapshot immutability (price + validity/cycle), and add-on catalog
seed idempotency.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from schemas.addon_schema import (
    AddonCreate,
    AddonKind,
    AddonOut,
    AddonStatus,
    AddonUpdate,
    TenantAddonOut,
    TenantAddonStatus,
)

pytestmark = pytest.mark.asyncio


def _premium_plan(base_price_monthly: float = 150_000.0) -> Any:
    from schemas.plan_schema import PlanOut, PlanTier

    return PlanOut(
        _id="507f1f77bcf86cd799439099",
        name="premium",
        display_name="Premium",
        tier=PlanTier.PREMIUM,
        base_price_monthly=base_price_monthly,
        base_price_yearly=base_price_monthly * 12,
    )


def _derived_addon(
    *,
    unit_price: float = 0.0,
    addon_id: str = "addon1",
    slug: str | None = "additional-branch",
) -> AddonOut:
    return AddonOut(
        _id=addon_id,
        slug=slug,
        name="Additional Branch",
        kind=AddonKind.BRANCH_QUOTA,
        status=AddonStatus.ACTIVE,
        unit_price=unit_price,
        currency="NGN",
        pricing_mode="derived",
        derived_from={
            "plan": "premium",
            "field": "base_price_monthly",
            "multiplier": 0.8,
        },
        recurring=True,
        billing_cycle="monthly",
        validity_days=None,
        benefit_per_unit={"branches": 1},
    )


def _fixed_addon(*, unit_price: float = 2000.0) -> AddonOut:
    return AddonOut(
        _id="addon2",
        name="Storage",
        kind=AddonKind.STORAGE_EXTENSION,
        status=AddonStatus.ACTIVE,
        unit_price=unit_price,
        currency="NGN",
        benefit_per_unit={"storage_mb": 1024},
    )


# ─── resolve_addon_unit_price ───────────────────────────────────────


async def test_fixed_addon_uses_stored_unit_price() -> None:
    from services.addon_service import resolve_addon_unit_price

    addon = _fixed_addon(unit_price=2000.0)
    assert await resolve_addon_unit_price(addon) == 2000.0


async def test_derived_addon_resolves_live_from_referenced_plan() -> None:
    from services.addon_service import resolve_addon_unit_price

    addon = _derived_addon(unit_price=999.0)  # stale cached value
    with patch(
        "repositories.plan_repo.get_plan",
        new=AsyncMock(return_value=_premium_plan(150_000.0)),
    ):
        price = await resolve_addon_unit_price(addon)

    assert price == 150_000.0 * 0.8  # 120,000


async def test_derived_addon_follows_live_premium_price_changes() -> None:
    """The core acceptance check: bump Premium's price, the derived
    addon price follows within one call — no caching in between."""
    from services.addon_service import resolve_addon_unit_price

    addon = _derived_addon()
    with patch(
        "repositories.plan_repo.get_plan",
        new=AsyncMock(return_value=_premium_plan(150_000.0)),
    ):
        price_before = await resolve_addon_unit_price(addon)
    with patch(
        "repositories.plan_repo.get_plan",
        new=AsyncMock(return_value=_premium_plan(200_000.0)),
    ):
        price_after = await resolve_addon_unit_price(addon)

    assert price_before == 120_000.0
    assert price_after == 160_000.0
    assert price_after != price_before


async def test_derived_addon_falls_back_to_cached_price_when_plan_missing() -> None:
    from services.addon_service import resolve_addon_unit_price

    addon = _derived_addon(unit_price=111_000.0)
    with patch("repositories.plan_repo.get_plan", new=AsyncMock(return_value=None)):
        price = await resolve_addon_unit_price(addon)

    assert price == 111_000.0


# ─── resync_derived_addon_prices (Premium-write hook) ───────────────


async def test_resync_updates_derived_addons_referencing_the_plan() -> None:
    from services.addon_service import resync_derived_addon_prices

    row = _derived_addon(unit_price=100_000.0, addon_id="addon1")
    with (
        patch("services.addon_service.list_addons", new=AsyncMock(return_value=[row])),
        patch(
            "repositories.plan_repo.get_plan",
            new=AsyncMock(return_value=_premium_plan(200_000.0)),
        ),
        patch(
            "services.addon_service.update_addon", new=AsyncMock(return_value=None)
        ) as mock_update,
    ):
        updated_count = await resync_derived_addon_prices("premium")

    assert updated_count == 1
    mock_update.assert_awaited_once()
    call_args = mock_update.await_args
    assert call_args is not None
    assert call_args.args[0] == "addon1"
    payload: AddonUpdate = call_args.args[1]
    assert payload.unit_price == 160_000.0


async def test_resync_skips_rows_whose_price_is_unchanged() -> None:
    from services.addon_service import resync_derived_addon_prices

    row = _derived_addon(unit_price=120_000.0)
    with (
        patch("services.addon_service.list_addons", new=AsyncMock(return_value=[row])),
        patch(
            "repositories.plan_repo.get_plan",
            new=AsyncMock(return_value=_premium_plan(150_000.0)),
        ),
        patch("services.addon_service.update_addon", new=AsyncMock()) as mock_update,
    ):
        updated_count = await resync_derived_addon_prices("premium")

    assert updated_count == 0
    mock_update.assert_not_awaited()


# ─── Purchase gating by tier ─────────────────────────────────────────


async def test_purchase_blocked_for_non_premium_tenant() -> None:
    from core.errors import AppException
    from services.addon_service import initiate_addon_purchase

    addon = _derived_addon(unit_price=120_000.0)
    with (
        patch(
            "services.addon_service.get_public_addon", new=AsyncMock(return_value=addon)
        ),
        patch(
            "services.plan_cache_service.resolve_tenant_plan",
            new=AsyncMock(return_value={"tier": "starter"}),
        ),
    ):
        with pytest.raises(AppException) as exc_info:
            await initiate_addon_purchase(
                tenant_id="507f1f77bcf86cd799439011",
                addon_id="addon1",
                quantity=1,
                actor_id="user1",
            )

    assert exc_info.value.status_code == 403


async def test_purchase_allowed_for_premium_tenant_cold_cache_miss_enum_tier() -> None:
    """On a cache miss, ``resolve_tenant_plan`` re-derives the plan from the
    DB and returns the tier as a ``PlanTier`` enum member rather than the
    plain string the warm Redis path serves. ``_require_premium_tier`` must
    still recognise Premium in that shape (Task 7 review risk: cold-miss
    tier-gate regression) instead of 403ing a legitimate Premium tenant."""
    from schemas.plan_schema import PlanTier
    from services.addon_service import initiate_addon_purchase
    from core.payments.types import (
        PaymentIntentResponse,
        PaymentProviderName,
        PaymentStatus,
    )

    addon = _derived_addon(unit_price=120_000.0)
    saved_row = TenantAddonOut(
        _id="ta1",
        tenant_id="507f1f77bcf86cd799439011",
        addon_id="addon1",
        addon_kind=AddonKind.BRANCH_QUOTA,
        quantity=1,
        unit_price_snapshot=120_000.0,
        currency_snapshot="NGN",
        status=TenantAddonStatus.PENDING,
    )
    intent = PaymentIntentResponse(
        provider=PaymentProviderName.APP,
        reference="ref1",
        status=PaymentStatus.PENDING,
        checkout_url="https://pay/x",
        provider_payload={},
    )
    with (
        patch(
            "services.addon_service.get_public_addon", new=AsyncMock(return_value=addon)
        ),
        patch(
            # Simulate the cold-miss DB-resolution path: tier comes back as
            # the enum member (PlanTier.PREMIUM), not the string "premium".
            "services.plan_cache_service.resolve_tenant_plan",
            new=AsyncMock(return_value={"tier": PlanTier.PREMIUM}),
        ),
        patch("services.addon_service._select_provider", return_value="app"),
        patch(
            "services.addon_service._create_intent_with_fallback",
            return_value=("app", intent),
        ),
        patch(
            "services.addon_service.create_tenant_addon",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
        patch("services.addon_service.record_audit_event", new=AsyncMock()),
    ):
        result = await initiate_addon_purchase(
            tenant_id="507f1f77bcf86cd799439011",
            addon_id="addon1",
            quantity=1,
            actor_id="user1",
        )

    assert result is saved_row
    mock_create.assert_awaited_once()


async def test_purchase_allowed_for_premium_tenant_and_snapshots_price_and_cycle() -> (
    None
):
    from services.addon_service import initiate_addon_purchase
    from core.payments.types import PaymentIntentResponse

    addon = _derived_addon(unit_price=120_000.0)
    saved_row = TenantAddonOut(
        _id="ta1",
        tenant_id="507f1f77bcf86cd799439011",
        addon_id="addon1",
        addon_kind=AddonKind.BRANCH_QUOTA,
        quantity=1,
        unit_price_snapshot=120_000.0,
        currency_snapshot="NGN",
        status=TenantAddonStatus.PENDING,
    )

    from core.payments.types import PaymentProviderName, PaymentStatus

    intent = PaymentIntentResponse(
        provider=PaymentProviderName.APP,
        reference="ref1",
        status=PaymentStatus.PENDING,
        checkout_url="https://pay/x",
        provider_payload={},
    )
    with (
        patch(
            "services.addon_service.get_public_addon", new=AsyncMock(return_value=addon)
        ),
        patch(
            "services.plan_cache_service.resolve_tenant_plan",
            new=AsyncMock(return_value={"tier": "premium"}),
        ),
        patch("services.addon_service._select_provider", return_value="app"),
        patch(
            "services.addon_service._create_intent_with_fallback",
            return_value=("app", intent),
        ),
        patch(
            "services.addon_service.create_tenant_addon",
            new=AsyncMock(return_value=saved_row),
        ) as mock_create,
        patch("services.addon_service.record_audit_event", new=AsyncMock()),
    ):
        result = await initiate_addon_purchase(
            tenant_id="507f1f77bcf86cd799439011",
            addon_id="addon1",
            quantity=1,
            actor_id="user1",
        )

    assert result is saved_row
    mock_create.assert_awaited_once()
    assert mock_create.await_args is not None
    payload = mock_create.await_args.args[0]
    assert payload.unit_price_snapshot == 120_000.0
    assert payload.recurring_snapshot is True
    assert payload.validity_days_snapshot == 30  # monthly cycle
    assert payload.billing_cycle_snapshot == "monthly"


# ─── Activation snapshot immutability ────────────────────────────────


async def test_recurring_activation_uses_purchase_time_snapshot_not_current_catalog() -> (
    None
):
    """A catalog edit between purchase and webhook must NOT change the
    granted expiry for a recurring addon — this is the quirk WS2 must
    NOT repeat for price/validity."""
    from services.addon_service import activate_tenant_addon_by_reference

    pending_row = TenantAddonOut(
        _id="ta1",
        tenant_id="t1",
        addon_id="addon1",
        addon_kind=AddonKind.BRANCH_QUOTA,
        quantity=1,
        unit_price_snapshot=120_000.0,
        currency_snapshot="NGN",
        status=TenantAddonStatus.PENDING,
        recurring_snapshot=True,
        validity_days_snapshot=30,
        billing_cycle_snapshot="monthly",
        payment_reference="addon_ref1",
    )

    # Catalog row now claims a DIFFERENT (wrong, if consulted) validity —
    # activation must ignore it entirely for a recurring row.
    mutated_catalog_addon = _derived_addon(unit_price=999_999.0)
    mutated_catalog_addon.validity_days = 9999

    updated_row = pending_row.model_copy(update={"status": TenantAddonStatus.ACTIVE})

    with (
        patch(
            "services.addon_service.get_tenant_addon_by_reference",
            new=AsyncMock(return_value=pending_row),
        ),
        patch(
            "services.addon_service.get_addon_by_id",
            new=AsyncMock(return_value=mutated_catalog_addon),
        ) as mock_get_addon,
        patch(
            "services.addon_service.update_tenant_addon",
            new=AsyncMock(return_value=updated_row),
        ) as mock_update,
        patch("services.addon_service.record_audit_event", new=AsyncMock()),
        patch(
            "services.addon_service._invalidate_tenant_addon_caches", new=AsyncMock()
        ),
    ):
        await activate_tenant_addon_by_reference(
            payment_reference="addon_ref1", completed_at=1_000_000
        )

    # The catalog row (with its mutated validity_days) is never consulted
    # for a recurring add-on.
    mock_get_addon.assert_not_awaited()
    assert mock_update.await_args is not None
    update_payload = mock_update.await_args.args[1]
    assert update_payload.expires_at == 1_000_000 + 30 * 86_400


# ─── Catalog seed idempotency ────────────────────────────────────────


async def test_seed_creates_singleton_addon_when_absent() -> None:
    from services.addon_bootstrap import ensure_addon_catalog

    created = AddonOut(
        _id="seeded1",
        slug="additional-branch",
        name="Additional Branch",
        kind=AddonKind.BRANCH_QUOTA,
        status=AddonStatus.ACTIVE,
        unit_price=120_000.0,
        pricing_mode="derived",
        recurring=True,
    )
    with (
        patch("services.addon_bootstrap.get_addon", new=AsyncMock(return_value=None)),
        patch(
            "services.addon_bootstrap.resolve_derived_addon_price",
            new=AsyncMock(return_value=120_000.0),
        ),
        patch(
            "services.addon_bootstrap.create_addon", new=AsyncMock(return_value=created)
        ) as mock_create,
    ):
        result = await ensure_addon_catalog()

    assert result == {"additional-branch": "seeded1"}
    mock_create.assert_awaited_once()
    assert mock_create.await_args is not None
    payload: AddonCreate = mock_create.await_args.args[0]
    assert payload.slug == "additional-branch"
    assert payload.unit_price == 120_000.0


async def test_seed_is_idempotent_and_preserves_admin_description() -> None:
    """Re-running the seed against an existing row must not touch
    ``description`` (admin-editable) but must refresh mechanical
    fields like the live-resolved unit_price."""
    from services.addon_bootstrap import ensure_addon_catalog

    existing = AddonOut(
        _id="seeded1",
        slug="additional-branch",
        name="Additional Branch",
        description="Admin's custom copy — must survive",
        kind=AddonKind.BRANCH_QUOTA,
        status=AddonStatus.ACTIVE,
        unit_price=100_000.0,
        currency="NGN",
        pricing_mode="derived",
        derived_from={
            "plan": "premium",
            "field": "base_price_monthly",
            "multiplier": 0.8,
        },
        recurring=True,
        billing_cycle="monthly",
    )
    with (
        patch(
            "services.addon_bootstrap.get_addon", new=AsyncMock(return_value=existing)
        ),
        patch(
            "services.addon_bootstrap.resolve_derived_addon_price",
            new=AsyncMock(return_value=160_000.0),
        ),
        patch(
            "services.addon_bootstrap.update_addon", new=AsyncMock(return_value=None)
        ) as mock_update,
    ):
        result = await ensure_addon_catalog()

    assert result == {"additional-branch": "seeded1"}
    mock_update.assert_awaited_once()
    call_args = mock_update.await_args
    assert call_args is not None
    assert call_args.args[0] == "seeded1"
    payload: AddonUpdate = call_args.args[1]
    assert payload.unit_price == 160_000.0
    # description was never part of the update payload
    assert "description" not in payload.model_dump(exclude_unset=True)
