"""Unit tests for the real-time notification delivery layer.

Covers the two things the SSE / summary spec hinges on:

  * ``compute_notification_state`` — the single source of truth shared by
    ``GET /v1/notifications/summary`` and every SSE event. ``total`` is
    counted directly and may exceed ``sum(counts)`` (null-bucket
    notifications count toward ``total`` but to no bucket).
  * the pub/sub publish helpers + SSE frame formatting + bucket
    classification ordering (first match wins, specific before generic).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from schemas.notification_schema import NotificationOut, NotificationType
from services.notification_service import (
    _classify_link_to_bucket,
    compute_notification_state,
)
from services.notification_stream_service import (
    _channel,
    _format_sse,
    publish_notification_changed,
    publish_notification_created,
)

pytestmark = pytest.mark.unit


# --- compute_notification_state -------------------------------------------


@pytest.mark.asyncio
async def test_total_is_counted_directly_not_summed() -> None:
    """`total` must come from the direct unread count, not sum(counts).

    Three unread notifications exist; only one maps to a bucket. `total`
    must be 3 (== /unread-count) even though the buckets sum to 1.
    """
    with (
        patch(
            "services.notification_service.get_unread_count",
            new=AsyncMock(return_value=3),
        ),
        patch(
            "services.notification_service.get_notification_bucket_summary",
            new=AsyncMock(return_value={"visitors": 1}),
        ),
    ):
        state = await compute_notification_state(user_id="u1", user_type="system_user")

    assert state["total"] == 3
    assert state["counts"] == {"visitors": 1}
    assert state["total"] > sum(state["counts"].values())


@pytest.mark.asyncio
async def test_state_with_no_unread() -> None:
    with (
        patch(
            "services.notification_service.get_unread_count",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "services.notification_service.get_notification_bucket_summary",
            new=AsyncMock(return_value={}),
        ),
    ):
        state = await compute_notification_state(user_id="u1", user_type="admin")

    assert state == {"total": 0, "counts": {}}


# --- bucket classification (order matters) --------------------------------


def test_bucket_specific_patterns_win_over_generic() -> None:
    # /support-cases must beat the generic ordering.
    assert _classify_link_to_bucket("/app/support-cases/123") == "support_cases"
    # /tenants/onboarding is tested BEFORE /visitors etc.
    assert _classify_link_to_bucket("/admin/tenants/onboarding/9") == "onboarding_queue"
    assert _classify_link_to_bucket("/app/checkins/5") == "visitors"
    assert _classify_link_to_bucket("/app/visitors/5") == "visitors"
    assert _classify_link_to_bucket("/app/appointments/5") == "appointments"
    assert _classify_link_to_bucket("/app/billing") == "billing"
    assert _classify_link_to_bucket("/app/subscriptions") == "billing"


def test_bucket_null_for_unmapped_and_missing_link() -> None:
    assert _classify_link_to_bucket("/app/profile") is None
    assert _classify_link_to_bucket(None) is None


# --- SSE frame formatting -------------------------------------------------


def test_format_sse_frame_shape() -> None:
    frame = _format_sse(7, "notification.changed", {"total": 2, "counts": {}})
    assert frame.startswith("id: 7\nevent: notification.changed\ndata: ")
    assert frame.endswith("\n\n")
    # The data line must be valid JSON carrying the absolute state.
    data_line = frame.split("data: ", 1)[1].rstrip("\n")
    assert json.loads(data_line) == {"total": 2, "counts": {}}


def test_channel_includes_user_type_and_id() -> None:
    assert _channel("system_user", "abc") == "notif:events:system_user:abc"
    assert _channel("admin", "abc") != _channel("system_user", "abc")


# --- publish helpers ------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_created_carries_identity_and_absolute_state() -> None:
    notif = NotificationOut(
        _id="n1",
        user_id="u1",
        user_type="system_user",
        title="Pending Check-In Approval",
        body="x",
        type=NotificationType.INFO,
        link="/app/checkins/42",
    )

    with (
        patch(
            "services.notification_service.compute_notification_state",
            new=AsyncMock(return_value={"total": 5, "counts": {"visitors": 2}}),
        ),
        patch(
            "services.notification_stream_service._publish",
            new=AsyncMock(),
        ) as pub,
    ):
        await publish_notification_created(notif)

    pub.assert_awaited_once()
    assert pub.await_args is not None
    args = pub.await_args.args
    # (_publish positional: user_type, user_id, event_name, data)
    assert args[0] == "system_user"
    assert args[1] == "u1"
    assert args[2] == "notification.created"
    data = args[3]
    assert data["id"] == "n1"
    assert data["type"] == "info"
    assert data["link"] == "/app/checkins/42"
    assert data["bucket"] == "visitors"
    assert data["total"] == 5
    assert data["counts"] == {"visitors": 2}


@pytest.mark.asyncio
async def test_publish_changed_carries_only_absolute_state() -> None:
    with (
        patch(
            "services.notification_service.compute_notification_state",
            new=AsyncMock(return_value={"total": 1, "counts": {"jobs": 1}}),
        ),
        patch(
            "services.notification_stream_service._publish",
            new=AsyncMock(),
        ) as pub,
    ):
        await publish_notification_changed(user_id="u1", user_type="admin")

    pub.assert_awaited_once()
    assert pub.await_args is not None
    args = pub.await_args.args
    assert args[0] == "admin"
    assert args[1] == "u1"
    assert args[2] == "notification.changed"
    assert args[3] == {"total": 1, "counts": {"jobs": 1}}


@pytest.mark.asyncio
async def test_publish_created_noop_without_user_identity() -> None:
    notif = NotificationOut(
        _id="n1",
        user_id=None,
        user_type=None,
        title="x",
        body="y",
        type=NotificationType.INFO,
    )
    with patch("services.notification_stream_service._publish", new=AsyncMock()) as pub:
        await publish_notification_created(notif)
    pub.assert_not_awaited()
