"""Unit tests for plan archive behavior refinement."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from schemas.plan_schema import PlanOut, PlanStatus, PlanTier, TenantCapLimit


# ---------------------------------------------------------------------------
# Plan archive: retrieve_plans with public_only excludes archived
# ---------------------------------------------------------------------------


def _make_plan_out(**overrides) -> PlanOut:
    defaults = {
        "_id": "507f1f77bcf86cd799439121",
        "name": "test-plan",
        "display_name": "Test Plan",
        "tier": PlanTier.PROFESSIONAL.value,
        "status": PlanStatus.ACTIVE.value,
        "base_price_monthly": 99.0,
        "base_price_yearly": 999.0,
        "is_public": True,
    }
    defaults.update(overrides)
    return PlanOut(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_retrieve_plans_public_excludes_archived():
    """When public_only=True, the filter must exclude archived plans."""
    with patch(
        "services.plan_service.get_plans",
        new_callable=AsyncMock,
        return_value=[_make_plan_out()],
    ) as mock_get:
        from services.plan_service import retrieve_plans

        result = await retrieve_plans(public_only=True)
        assert len(result) == 1

        # Inspect the filter dict passed to get_plans
        call_args = mock_get.call_args
        filter_dict = call_args[0][0]
        assert filter_dict["is_public"] is True
        assert filter_dict["status"] == {"$ne": "archived"}


@pytest.mark.asyncio
async def test_retrieve_plans_without_public_no_status_filter():
    """When public_only=False, no automatic status exclusion is applied."""
    with patch(
        "services.plan_service.get_plans",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_get:
        from services.plan_service import retrieve_plans

        await retrieve_plans(public_only=False)
        call_args = mock_get.call_args
        filter_dict = call_args[0][0]
        assert "status" not in filter_dict


# ---------------------------------------------------------------------------
# Archive sets is_public=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_plan_sets_is_public_false():
    """archive_plan() should set both status=archived and is_public=False."""
    with (
        patch(
            "services.plan_service.retrieve_plan_by_id",
            new_callable=AsyncMock,
            return_value=_make_plan_out(),
        ),
        patch(
            "services.plan_service.update_plan",
            new_callable=AsyncMock,
            return_value=_make_plan_out(status="archived", is_public=False),
        ) as mock_update,
    ):
        from services.plan_service import archive_plan

        result = await archive_plan("507f1f77bcf86cd799439121")
        assert result.status == PlanStatus.ARCHIVED

        # Verify the update data included is_public=False
        call_args = mock_update.call_args
        update_schema = call_args[0][1]
        assert update_schema.is_public is False
        assert update_schema.status == PlanStatus.ARCHIVED


# ---------------------------------------------------------------------------
# subscribe_tenant rejects archived plans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_to_archived_plan_fails():
    """subscribe_tenant() should reject plans that are not ACTIVE."""
    archived_plan = _make_plan_out(status=PlanStatus.ARCHIVED.value)

    with (
        patch(
            "services.subscription_service.get_plan",
            new_callable=AsyncMock,
            return_value=archived_plan,
        ),
    ):
        from services.subscription_service import subscribe_tenant
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await subscribe_tenant(tenant_id="t1", plan_id="507f1f77bcf86cd799439121")
        assert exc_info.value.status_code == 400
        assert "not active" in exc_info.value.detail


@pytest.mark.asyncio
async def test_subscribe_to_draft_plan_fails():
    """subscribe_tenant() should also reject DRAFT plans."""
    draft_plan = _make_plan_out(status=PlanStatus.DRAFT.value)

    with (
        patch(
            "services.subscription_service.get_plan",
            new_callable=AsyncMock,
            return_value=draft_plan,
        ),
    ):
        from services.subscription_service import subscribe_tenant
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await subscribe_tenant(tenant_id="t1", plan_id="507f1f77bcf86cd799439121")
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# TenantCapLimit now has max_branches
# ---------------------------------------------------------------------------


class TestTenantCapLimitMaxBranches:
    def test_max_branches_default_none(self):
        cap = TenantCapLimit()
        assert cap.max_branches is None

    def test_max_branches_set(self):
        cap = TenantCapLimit(max_branches=5)
        assert cap.max_branches == 5

    def test_full_cap_limit(self):
        cap = TenantCapLimit(
            max_system_users=10,
            max_departments=5,
            max_branches=3,
            max_visitors_per_month=1000,
            max_appointments_per_month=500,
        )
        assert cap.max_branches == 3
        assert cap.max_system_users == 10


# ---------------------------------------------------------------------------
# Delete only DRAFT plans (existing behavior verification)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_active_plan_fails():
    """remove_plan() should reject deletion of non-DRAFT plans."""
    active_plan = _make_plan_out(status=PlanStatus.ACTIVE.value)

    with patch(
        "services.plan_service.get_plan",
        new_callable=AsyncMock,
        return_value=active_plan,
    ):
        from services.plan_service import remove_plan
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await remove_plan("507f1f77bcf86cd799439121")
        assert exc_info.value.status_code == 400
        assert "draft" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_remove_archived_plan_fails():
    """remove_plan() should reject deletion of archived plans."""
    archived_plan = _make_plan_out(status=PlanStatus.ARCHIVED.value)

    with patch(
        "services.plan_service.get_plan",
        new_callable=AsyncMock,
        return_value=archived_plan,
    ):
        from services.plan_service import remove_plan
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await remove_plan("507f1f77bcf86cd799439121")
        assert exc_info.value.status_code == 400
