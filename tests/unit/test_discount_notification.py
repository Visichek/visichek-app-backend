"""Unit tests for the queued discount-available fan-out."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import discount_notification_service as svc


def _sub(tenant_id: str, plan_id: str = "plan_x") -> SimpleNamespace:
    return SimpleNamespace(tenant_id=tenant_id, plan_id=plan_id)


def test_format_value_percentage_and_fixed() -> None:
    assert svc._format_value("percentage", 50.0) == "50% off"
    assert svc._format_value("fixed", 500.0) == "500 off"


def test_checkout_link_includes_plan_only_when_known() -> None:
    assert svc._build_checkout_link(discount_id="d1", code="C", plan_id="p1") == (
        "/app/billing/checkout?discountId=d1&discountCode=C&planId=p1"
    )
    assert svc._build_checkout_link(discount_id="d1", code="C", plan_id=None) == (
        "/app/billing/checkout?discountId=d1&discountCode=C"
    )


@pytest.mark.asyncio
async def test_coordinator_tenant_scope_emits_single_batch() -> None:
    emitted: list[dict] = []

    def _fake_enqueue(task_key: str, payload: dict) -> bool:
        emitted.append({"task_key": task_key, "payload": payload})
        return True

    with patch.object(svc, "_enqueue", side_effect=_fake_enqueue):
        out = await svc._announce_coordinator(
            discount_id="d1",
            code="C",
            name="Launch",
            scope="tenant",
            discount_type="fixed",
            value=500.0,
            target_tenant_id="tenant_1",
            target_plan_ids=["only_plan"],
        )

    assert out == {"scope": "tenant", "tenants": 1, "batches": 1}
    assert len(emitted) == 1
    assert emitted[0]["task_key"] == "discount.announce_batch"
    # Single target plan is pinned onto the pair so the FE checkout pre-fills it.
    assert emitted[0]["payload"]["pairs"] == [["tenant_1", "only_plan"]]


@pytest.mark.asyncio
async def test_coordinator_global_scope_paginates_and_chunks() -> None:
    # 120 active subscriptions across a single page → ceil(120/50) = 3 batches.
    page = [_sub(f"t{i}") for i in range(120)]

    async def _fake_get_subscriptions(filt, start=0, stop=100):
        # One full page then empty (simulates end of data).
        return page if start == 0 else []

    emitted: list[dict] = []

    def _capture(task_key: str, payload: dict) -> bool:
        emitted.append(payload)
        return True

    with (
        patch.object(svc, "_PAGE_SIZE", 500),
        patch.object(svc, "_BATCH_SIZE", 50),
        patch(
            "repositories.subscription_repo.get_subscriptions",
            new=AsyncMock(side_effect=_fake_get_subscriptions),
        ),
        patch.object(svc, "_enqueue", side_effect=_capture),
    ):
        out = await svc._announce_coordinator(
            discount_id="d1",
            code="C",
            name="Launch",
            scope="global",
            discount_type="percentage",
            value=10.0,
        )

    assert out["scope"] == "global"
    assert out["tenants"] == 120
    assert out["batches"] == 3
    # Global scope carries no plan_id (FE picks the plan at checkout).
    assert all(pair[1] is None for batch in emitted for pair in batch["pairs"])
    # Every tenant appears exactly once across all batches.
    all_tenants = [pair[0] for batch in emitted for pair in batch["pairs"]]
    assert sorted(all_tenants) == sorted(s.tenant_id for s in page)


@pytest.mark.asyncio
async def test_batch_handler_notifies_super_admins_per_tenant() -> None:
    send_mock = AsyncMock()

    with (
        patch.object(
            svc,
            "_active_super_admins",
            new=AsyncMock(return_value=[MagicMock(id="su_1"), MagicMock(id="su_2")]),
        ),
        patch("services.notification_service.send_notification", new=send_mock),
    ):
        out = await svc._announce_batch(
            pairs=[["tenant_1", "plan_a"]],
            meta={
                "discount_id": "d1",
                "code": "C",
                "name": "Launch",
                "discount_type": "fixed",
                "value": 500.0,
                "valid_until": None,
            },
        )

    assert out == {"tenants": 1, "notifications": 2}
    assert send_mock.await_count == 2
    assert send_mock.await_args is not None
    kwargs = send_mock.await_args.kwargs
    assert kwargs["resource_type"] == "discount"
    assert kwargs["email_template_key"] == "notif_discount_available"
    assert "planId=plan_a" in kwargs["link"]
