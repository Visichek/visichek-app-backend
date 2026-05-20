"""Unit tests for the notification read-receipt auto-mark.

When a user reads a resource that triggered a notification, the matching
unread notification(s) flip to read on their own — no manual mark-read
call. Notifications carry ``resource_type`` + ``resource_id`` so the match
is precise.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from schemas.notification_schema import NotificationCreate
from services.notification_service import (
    extract_resource_ids,
    mark_notifications_read_for_resources,
    schedule_resource_read_receipt,
)

pytestmark = pytest.mark.unit


# --- Schema carries the resource reference ---------------------------------


def test_notification_create_carries_resource_fields() -> None:
    notif = NotificationCreate(
        user_id="u1",
        user_type="system_user",
        title="t",
        body="b",
        resource_type="incident",
        resource_id="inc-1",
    )
    assert notif.resource_type == "incident"
    assert notif.resource_id == "inc-1"


def test_notification_create_resource_fields_default_none() -> None:
    notif = NotificationCreate(
        user_id="u1", user_type="system_user", title="t", body="b"
    )
    assert notif.resource_type is None
    assert notif.resource_id is None


# --- extract_resource_ids handles every list shape -------------------------


def test_extract_ids_from_items_dict() -> None:
    result = {"items": [{"_id": "a"}, {"id": "b"}, {"foo": "bar"}], "meta": {}}
    assert extract_resource_ids(result) == ["a", "b"]


def test_extract_ids_from_tuple() -> None:
    class Row:
        def __init__(self, rid: str) -> None:
            self.id = rid

    result = ([Row("x"), Row("y")], {"total": 2})
    assert extract_resource_ids(result) == ["x", "y"]


def test_extract_ids_from_bare_list() -> None:
    assert extract_resource_ids([{"id": "1"}, {"_id": "2"}]) == ["1", "2"]


def test_extract_ids_empty() -> None:
    assert extract_resource_ids(None) == []
    assert extract_resource_ids({"items": []}) == []
    assert extract_resource_ids({}) == []


# --- mark_notifications_read_for_resources ---------------------------------


@pytest.mark.asyncio
async def test_mark_read_delegates_to_repo() -> None:
    with patch(
        "services.notification_service.mark_read_by_resource_ids",
        new=AsyncMock(return_value=2),
    ) as repo:
        count = await mark_notifications_read_for_resources(
            user_id="u1",
            user_type="system_user",
            resource_type="incident",
            resource_ids=["inc-1", "inc-2"],
        )
    assert count == 2
    repo.assert_awaited_once_with(
        user_id="u1",
        user_type="system_user",
        resource_type="incident",
        resource_ids=["inc-1", "inc-2"],
    )


@pytest.mark.asyncio
async def test_mark_read_noop_on_empty_ids() -> None:
    with patch(
        "services.notification_service.mark_read_by_resource_ids",
        new=AsyncMock(return_value=0),
    ) as repo:
        count = await mark_notifications_read_for_resources(
            user_id="u1",
            user_type="system_user",
            resource_type="incident",
            resource_ids=[],
        )
    assert count == 0
    repo.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_read_swallows_repo_error() -> None:
    with patch(
        "services.notification_service.mark_read_by_resource_ids",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        # Must not raise — read receipts are best-effort.
        count = await mark_notifications_read_for_resources(
            user_id="u1",
            user_type="system_user",
            resource_type="incident",
            resource_ids=["inc-1"],
        )
    assert count == 0


# --- schedule_resource_read_receipt (non-blocking) -------------------------


@pytest.mark.asyncio
async def test_schedule_maps_role_and_marks() -> None:
    with patch(
        "services.notification_service.mark_notifications_read_for_resources",
        new=AsyncMock(return_value=1),
    ) as marker:
        schedule_resource_read_receipt(
            user_id="u1",
            user_role="super_admin",  # tenant role → "system_user"
            resource_type="checkin",
            resource_ids=["c1"],
        )
        # The scheduler is fire-and-forget; let the spawned task run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    marker.assert_awaited_once_with(
        user_id="u1",
        user_type="system_user",
        resource_type="checkin",
        resource_ids=["c1"],
    )


@pytest.mark.asyncio
async def test_schedule_admin_role_maps_to_admin() -> None:
    with patch(
        "services.notification_service.mark_notifications_read_for_resources",
        new=AsyncMock(return_value=1),
    ) as marker:
        schedule_resource_read_receipt(
            user_id="a1",
            user_role="admin",
            resource_type="support_case",
            resource_ids=["case-1"],
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert marker.await_args.kwargs["user_type"] == "admin"


@pytest.mark.asyncio
async def test_schedule_noop_when_no_ids() -> None:
    with patch(
        "services.notification_service.mark_notifications_read_for_resources",
        new=AsyncMock(),
    ) as marker:
        schedule_resource_read_receipt(
            user_id="u1",
            user_role="super_admin",
            resource_type="incident",
            resource_ids=[],
        )
        await asyncio.sleep(0)
    marker.assert_not_awaited()
