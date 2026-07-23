"""Unit tests for Task 13 — enterprise composer backend.

Covers:
1. Feature registry compiles the expected feature_rules from a toggle map.
2. Enterprise plan edits are now enforced against ENTERPRISE_TEMPLATE's
   (expanded) allowlist instead of being wide open.
3. ``add_plan`` forces ``is_public=False`` on enterprise plans unless the
   caller explicitly set it.
4. The preview-limitations composition returns deniedFeatures for a draft
   with branding turned off.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from schemas.plan_schema import PlanCreate, PlanOut, PlanStatus, PlanTier, PlanUpdate

pytestmark = pytest.mark.asyncio


def _make_plan_out(**overrides) -> PlanOut:
    defaults = {
        "_id": "5f0a1b2c3d4e5f6a7b8c9d0e",
        "name": "enterprise-acme",
        "display_name": "Acme Enterprise",
        "tier": PlanTier.ENTERPRISE.value,
        "status": PlanStatus.ACTIVE.value,
        "base_price_monthly": 0.0,
        "base_price_yearly": 0.0,
        "currency": "NGN",
        "feature_rules": [],
        "crud_limits": [],
        "retrieval_quotas": [],
        "storage_limits": {},
        "tenant_caps": {},
        "trial_days": 0,
        "is_public": False,
        "date_created": int(time.time()),
        "last_updated": int(time.time()),
    }
    defaults.update(overrides)
    return PlanOut(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Feature registry compiler
# ---------------------------------------------------------------------------


class TestFeatureRegistryCompiler:
    def test_registry_has_expected_keys(self):
        from services.plan_feature_service import TOGGLEABLE_FEATURES

        assert set(TOGGLEABLE_FEATURES.keys()) == {
            "branding",
            "branching",
            "appointments",
            "badges",
            "csv_export",
            "host_email_notifications",
            "kyc",
            "watchlist",
            "sso",
            "api_access",
        }

    def test_disabling_branding_emits_the_same_patterns_as_free_tier(self):
        from config.plan_tiers import FREE_DENIED_FEATURES
        from services.plan_feature_service import compile_feature_rules

        rules = compile_feature_rules({"branding": False})
        patterns = {r.endpoint_pattern for r in rules}
        free_branding_patterns = {
            r.endpoint_pattern
            for r in FREE_DENIED_FEATURES
            if not r.enabled and "branding" in r.endpoint_pattern
        }
        assert patterns == free_branding_patterns

    def test_enabling_a_feature_emits_no_rule(self):
        from services.plan_feature_service import compile_feature_rules

        rules = compile_feature_rules({"branding": True, "sso": True})
        assert rules == []

    def test_unspecified_feature_falls_back_to_default_enabled(self):
        from services.plan_feature_service import (
            TOGGLEABLE_FEATURES,
            compile_feature_rules,
        )

        # kyc defaults to enabled=True -> no rule when omitted from the map
        rules = compile_feature_rules({})
        patterns = {r.endpoint_pattern for r in rules}
        assert not (patterns & {"/v1/kyc*", "/v1/kyc/*"})
        assert TOGGLEABLE_FEATURES["kyc"].default_enabled is True

    def test_api_access_has_no_deny_rules(self):
        from services.plan_feature_service import TOGGLEABLE_FEATURES

        assert TOGGLEABLE_FEATURES["api_access"].deny_rules == ()

    def test_branching_covers_multi_location_branch_writes(self):
        from services.plan_feature_service import compile_feature_rules

        rules = compile_feature_rules({"branching": False})
        write_rules = [r for r in rules if r.endpoint_pattern.startswith("/v1/branches")]
        assert write_rules
        for r in write_rules:
            assert "POST" in r.methods


# ---------------------------------------------------------------------------
# 2. Enterprise edit enforcement (previously dead code)
# ---------------------------------------------------------------------------


class TestEnterpriseEditEnforcement:
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_disallowed_field_is_now_blocked(self, mock_get):
        mock_get.return_value = _make_plan_out()
        from services.plan_service import update_plan_by_id

        with pytest.raises(HTTPException) as exc:
            await update_plan_by_id(
                "5f0a1b2c3d4e5f6a7b8c9d0e",
                PlanUpdate(currency="USD"),
            )
        assert exc.value.status_code == 400
        assert "tier-locked" in str(exc.value.detail).lower()

    @patch("services.plan_service.update_plan", new_callable=AsyncMock)
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_composition_fields_are_allowed(self, mock_get, mock_update):
        mock_get.return_value = _make_plan_out()
        mock_update.return_value = _make_plan_out(display_name="Acme (new)")
        from services.plan_service import update_plan_by_id

        result = await update_plan_by_id(
            "5f0a1b2c3d4e5f6a7b8c9d0e",
            PlanUpdate(
                display_name="Acme (new)",
                description="Updated",
                is_public=False,
                sort_order=99,
                feature_rules=[],
            ),
        )
        assert result is not None
        mock_update.assert_awaited_once()

    @patch("services.plan_service.update_plan", new_callable=AsyncMock)
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_adjustable_cap_field_still_allowed(self, mock_get, mock_update):
        mock_get.return_value = _make_plan_out()
        mock_update.return_value = _make_plan_out()
        from services.plan_service import update_plan_by_id

        result = await update_plan_by_id(
            "5f0a1b2c3d4e5f6a7b8c9d0e",
            PlanUpdate(tenant_caps={"max_branches": 5}),
        )
        assert result is not None
        mock_update.assert_awaited_once()

    @patch("services.plan_service.update_plan", new_callable=AsyncMock)
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_storage_limits_field_is_allowed(self, mock_get, mock_update):
        """Regression: Task 14's FE limits step includes storage inputs
        for enterprise plans — storage_limits must be in
        ENTERPRISE_TEMPLATE.adjustable_plan_fields or an enterprise
        storage edit 400s as tier-locked."""
        mock_get.return_value = _make_plan_out()
        mock_update.return_value = _make_plan_out()
        from services.plan_service import update_plan_by_id

        result = await update_plan_by_id(
            "5f0a1b2c3d4e5f6a7b8c9d0e",
            PlanUpdate(storage_limits={"max_documents": 5000, "max_storage_mb": 10_000}),
        )
        assert result is not None
        mock_update.assert_awaited_once()

    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_singleton_plans_unaffected_by_enterprise_fallback(self, mock_get):
        mock_get.return_value = _make_plan_out(
            name="premium", tier=PlanTier.PREMIUM.value
        )
        from services.plan_service import update_plan_by_id

        with pytest.raises(HTTPException) as exc:
            await update_plan_by_id(
                "5f0a1b2c3d4e5f6a7b8c9d0e",
                PlanUpdate(feature_rules=[]),
            )
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# 3. is_public forced False on enterprise creation
# ---------------------------------------------------------------------------


class TestEnterpriseIsPublicDefault:
    @patch("services.plan_service.create_plan", new_callable=AsyncMock)
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_is_public_forced_false_when_unset(self, mock_get, mock_create):
        mock_get.return_value = None
        mock_create.return_value = _make_plan_out(is_public=False)
        from services.plan_service import add_plan

        await add_plan(
            PlanCreate(
                name="enterprise-acme",
                display_name="Acme",
                tier=PlanTier.ENTERPRISE,
            )
        )
        sent_payload = mock_create.await_args.args[0]
        assert sent_payload.is_public is False

    @patch("services.plan_service.create_plan", new_callable=AsyncMock)
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_explicit_is_public_true_respected(self, mock_get, mock_create):
        mock_get.return_value = None
        mock_create.return_value = _make_plan_out(is_public=True)
        from services.plan_service import add_plan

        await add_plan(
            PlanCreate(
                name="enterprise-acme",
                display_name="Acme",
                tier=PlanTier.ENTERPRISE,
                is_public=True,
            )
        )
        sent_payload = mock_create.await_args.args[0]
        assert sent_payload.is_public is True

    @patch("services.plan_service.create_plan", new_callable=AsyncMock)
    @patch("services.plan_service.get_plan", new_callable=AsyncMock)
    async def test_singleton_plans_keep_default_is_public_true(
        self, mock_get, mock_create
    ):
        mock_get.return_value = None
        mock_create.return_value = _make_plan_out(
            name="premium", tier=PlanTier.PREMIUM.value, is_public=True
        )
        from services.plan_service import add_plan

        await add_plan(
            PlanCreate(name="premium", display_name="Premium", tier=PlanTier.PREMIUM)
        )
        sent_payload = mock_create.await_args.args[0]
        assert sent_payload.is_public is True


# ---------------------------------------------------------------------------
# 4. Preview-limitations endpoint composition
# ---------------------------------------------------------------------------


class TestPreviewLimitations:
    @patch("services.me_limitations_service.get_plan", new_callable=AsyncMock)
    async def test_branding_off_draft_yields_denied_feature(self, mock_get):
        from services.me_limitations_service import build_admin_plan_preview_limitations
        from services.plan_feature_service import compile_feature_rules

        mock_get.return_value = _make_plan_out(tenant_caps={})

        draft = PlanUpdate(feature_rules=compile_feature_rules({"branding": False}))
        result = await build_admin_plan_preview_limitations(
            "5f0a1b2c3d4e5f6a7b8c9d0e", draft
        )

        assert "branding" in result["deniedFeatures"]
        assert any(
            e["pattern"].startswith("/v1/branding") for e in result["deniedEndpoints"]
        )
        assert result["enterprise"]["isEnterprise"] is True
        assert result["tenantId"] is None

    @patch("services.me_limitations_service.get_plan", new_callable=AsyncMock)
    async def test_unknown_plan_id_raises_not_found(self, mock_get):
        mock_get.return_value = None
        from services.me_limitations_service import build_admin_plan_preview_limitations

        with pytest.raises(Exception):
            await build_admin_plan_preview_limitations(
                "5f0a1b2c3d4e5f6a7b8c9d0e", PlanUpdate()
            )

    @patch("services.me_limitations_service.get_plan", new_callable=AsyncMock)
    async def test_no_overrides_denies_nothing_by_default(self, mock_get):
        mock_get.return_value = _make_plan_out(feature_rules=[], tenant_caps={})
        from services.me_limitations_service import build_admin_plan_preview_limitations

        result = await build_admin_plan_preview_limitations(
            "5f0a1b2c3d4e5f6a7b8c9d0e", PlanUpdate()
        )
        assert result["deniedFeatures"] == []
        assert result["deniedEndpoints"] == []
