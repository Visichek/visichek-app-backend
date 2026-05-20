"""Unit tests for the plan write-pipeline handlers."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.plan_schema import PlanOut, PlanStatus, PlanTier

pytestmark = pytest.mark.asyncio


def _plan_out(**overrides) -> PlanOut:
    defaults = {
        "_id": "507f1f77bcf86cd799439011",
        "name": "test-plan",
        "display_name": "Test Plan",
        "tier": PlanTier.FREE.value,
        "status": PlanStatus.ACTIVE.value,
        "base_price_monthly": 0,
        "base_price_yearly": 0,
        "currency": "NGN",
        "feature_rules": [],
        "crud_limits": [],
        "retrieval_quotas": [],
        "storage_limits": {},
        "tenant_caps": {},
        "priority_support": False,
        "custom_branding": False,
        "api_access": False,
        "is_public": True,
        "sort_order": 0,
        "date_created": int(time.time()),
        "last_updated": int(time.time()),
    }
    defaults.update(overrides)
    return PlanOut(**defaults)  # type: ignore[arg-type]


class TestPlanActivateWriter:
    @patch("services.plan_writer.QueueManager")
    @patch("services.plan_writer.activate_plan", new_callable=AsyncMock)
    async def test_writer_dispatches_to_service(self, mock_activate, mock_qm):
        from services.plan_writer import _plan_activate

        mock_activate.return_value = _plan_out(status=PlanStatus.ACTIVE.value)
        mock_qm.get_instance.return_value = MagicMock()

        result = await _plan_activate(resource_id="507f1f77bcf86cd799439011", data={})

        mock_activate.assert_awaited_once_with(plan_id="507f1f77bcf86cd799439011")
        assert result == {"id": "507f1f77bcf86cd799439011", "status": "active"}

    @patch("services.plan_writer.QueueManager")
    @patch("services.plan_writer.activate_plan", new_callable=AsyncMock)
    async def test_writer_refreshes_both_list_precomputes(self, mock_activate, mock_qm):
        """Regression guard: ``_enqueue_list_refresh`` must enqueue refreshes
        for BOTH ``plans.list`` (admin view) and ``plans.public_list``
        (public catalogue). Refreshing only one was the cause of public
        catalogue plans showing as draft after activation.
        """
        from services.plan_writer import _plan_activate

        mock_activate.return_value = _plan_out(status=PlanStatus.ACTIVE.value)
        instance = MagicMock()
        mock_qm.get_instance.return_value = instance

        await _plan_activate(resource_id="507f1f77bcf86cd799439011", data={})

        enqueued_resources = {
            call.kwargs.get("payload", {}).get("resource")
            for call in instance.enqueue.call_args_list
            if call.kwargs.get("task_key") == "precompute.tenant_resource"
        }
        assert "plans.list" in enqueued_resources
        assert "plans.public_list" in enqueued_resources

    @patch("services.plan_writer.QueueManager")
    @patch("services.plan_writer.activate_plan", new_callable=AsyncMock)
    async def test_writer_propagates_service_exceptions(self, mock_activate, mock_qm):
        """If activate_plan raises (e.g. silent persistence failure), the
        writer must re-raise so the dispatcher marks the job as failed and
        ``notify_job_failure`` notifies the actor admin.
        """
        from fastapi import HTTPException

        from services.plan_writer import _plan_activate

        mock_qm.get_instance.return_value = MagicMock()
        mock_activate.side_effect = HTTPException(
            status_code=500, detail="Plan activation did not persist."
        )

        with pytest.raises(HTTPException) as exc_info:
            await _plan_activate(resource_id="507f1f77bcf86cd799439011", data={})
        assert exc_info.value.status_code == 500
