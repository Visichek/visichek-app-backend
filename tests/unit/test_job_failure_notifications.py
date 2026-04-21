"""Unit tests for queued-job-failure notifications."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.errors import AppException, ErrorCode
from schemas.queue_job_log_schema import QueueJobLogOut, QueueJobStatus
from services.notification_service import (
    _extract_failure_message,
    _format_action_from_writer_key,
    _user_type_for_role,
    notify_job_failure,
)

pytestmark = pytest.mark.unit


def _job_log(
    *,
    task_id: str = "task-123",
    actor_id: str | None = "actor-1",
    actor_role: str | None = "admin",
    tenant_id: str | None = "tenant-1",
) -> QueueJobLogOut:
    return QueueJobLogOut(
        task_id=task_id,
        task_key="db.write:discount.delete",
        actor_id=actor_id,
        actor_role=actor_role,
        tenant_id=tenant_id,
        status=QueueJobStatus.FAILED,
    )


# --- Pure-helper tests -----------------------------------------------------


def test_format_action_known_verb() -> None:
    assert (
        _format_action_from_writer_key("discount.delete")
        == "Couldn't delete discount"
    )


def test_format_action_unknown_verb_falls_back() -> None:
    assert (
        _format_action_from_writer_key("widget.frobnicate")
        == "Couldn't frobnicate widget"
    )


def test_format_action_missing_verb() -> None:
    assert _format_action_from_writer_key("orphan") == "Action failed: orphan"


def test_extract_failure_message_from_app_exception() -> None:
    exc = AppException(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED,
        message="Cannot delete an active discount. Disable it first.",
        details={"discount_id": "x"},
    )
    body, code = _extract_failure_message(exc)
    assert body == "Cannot delete an active discount. Disable it first."
    assert code == ErrorCode.VALIDATION_FAILED.value


def test_extract_failure_message_generic_exception() -> None:
    body, code = _extract_failure_message(RuntimeError("kaboom"))
    assert "unexpectedly" in body.lower()
    assert code is None


def test_user_type_mapping() -> None:
    assert _user_type_for_role("admin") == "admin"
    assert _user_type_for_role("user") == "user"
    assert _user_type_for_role("super_admin") == "system_user"
    assert _user_type_for_role("receptionist") == "system_user"
    assert _user_type_for_role(None) is None
    assert _user_type_for_role("unknown_role") is None


# --- notify_job_failure integration-ish tests -------------------------------


@pytest.mark.asyncio
async def test_notify_job_failure_sends_app_exception_message() -> None:
    exc = AppException(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED,
        message="Cannot delete an active discount. Disable it first.",
        details={"discount_id": "x"},
    )

    with (
        patch(
            "repositories.queue_job_log_repo.get_job_log_by_task_id",
            new=AsyncMock(return_value=_job_log()),
        ),
        patch(
            "services.notification_service.send_notification",
            new=AsyncMock(),
        ) as send_mock,
    ):
        await notify_job_failure(
            task_id="task-123",
            writer_key="discount.delete",
            exception=exc,
        )

    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    kwargs = send_mock.await_args.kwargs
    assert kwargs["user_id"] == "actor-1"
    assert kwargs["user_type"] == "admin"
    assert kwargs["title"] == "Couldn't delete discount"
    assert kwargs["body"] == "Cannot delete an active discount. Disable it first."
    assert kwargs["type"] == "error"
    assert kwargs["link"] == "/app/jobs/task-123"
    assert kwargs["tenant_id"] == "tenant-1"


@pytest.mark.asyncio
async def test_notify_job_failure_uses_generic_body_for_unexpected_error() -> None:
    with (
        patch(
            "repositories.queue_job_log_repo.get_job_log_by_task_id",
            new=AsyncMock(return_value=_job_log()),
        ),
        patch(
            "services.notification_service.send_notification",
            new=AsyncMock(),
        ) as send_mock,
    ):
        await notify_job_failure(
            task_id="task-123",
            writer_key="discount.delete",
            exception=RuntimeError("db dropped"),
        )

    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    body = send_mock.await_args.kwargs["body"]
    assert "unexpectedly" in body.lower()
    # Raw traceback content must not leak to the user.
    assert "db dropped" not in body


@pytest.mark.asyncio
async def test_notify_job_failure_maps_tenant_role_to_system_user() -> None:
    with (
        patch(
            "repositories.queue_job_log_repo.get_job_log_by_task_id",
            new=AsyncMock(
                return_value=_job_log(actor_role="super_admin")
            ),
        ),
        patch(
            "services.notification_service.send_notification",
            new=AsyncMock(),
        ) as send_mock,
    ):
        await notify_job_failure(
            task_id="task-123",
            writer_key="branch.create",
            exception=RuntimeError("boom"),
        )

    assert send_mock.await_args is not None
    assert send_mock.await_args.kwargs["user_type"] == "system_user"


@pytest.mark.asyncio
async def test_notify_job_failure_skips_when_no_actor() -> None:
    with (
        patch(
            "repositories.queue_job_log_repo.get_job_log_by_task_id",
            new=AsyncMock(return_value=_job_log(actor_id=None)),
        ),
        patch(
            "services.notification_service.send_notification",
            new=AsyncMock(),
        ) as send_mock,
    ):
        await notify_job_failure(
            task_id="task-123",
            writer_key="invoice.generate",
            exception=RuntimeError("boom"),
        )

    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_job_failure_skips_when_log_missing() -> None:
    with (
        patch(
            "repositories.queue_job_log_repo.get_job_log_by_task_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.notification_service.send_notification",
            new=AsyncMock(),
        ) as send_mock,
    ):
        await notify_job_failure(
            task_id="gone",
            writer_key="discount.delete",
            exception=RuntimeError("boom"),
        )

    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_job_failure_swallows_notification_errors() -> None:
    """A notification failure must not mask the original job failure."""
    with (
        patch(
            "repositories.queue_job_log_repo.get_job_log_by_task_id",
            new=AsyncMock(return_value=_job_log()),
        ),
        patch(
            "services.notification_service.send_notification",
            new=AsyncMock(side_effect=RuntimeError("notif backend down")),
        ),
    ):
        # Must not raise.
        await notify_job_failure(
            task_id="task-123",
            writer_key="discount.delete",
            exception=RuntimeError("boom"),
        )


# --- Dispatcher wiring -----------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_invokes_notify_on_writer_failure() -> None:
    """Writer exceptions must re-raise AND call notify_job_failure."""
    from core.queue import tasks as tasks_module

    exc = AppException(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED,
        message="nope",
    )

    with (
        patch(
            "core.queue.write_pipeline.execute_writer",
            new=AsyncMock(side_effect=exc),
        ),
        patch(
            "repositories.queue_job_log_repo.mark_processing",
            new=AsyncMock(),
        ),
        patch(
            "repositories.queue_job_log_repo.mark_failed",
            new=AsyncMock(),
        ) as mark_failed_mock,
        patch(
            "repositories.queue_job_log_repo.mark_succeeded",
            new=AsyncMock(),
        ),
        patch(
            "services.notification_service.notify_job_failure",
            new=AsyncMock(),
        ) as notify_mock,
    ):
        with pytest.raises(AppException):
            await tasks_module._db_write_dispatcher(
                writer_key="discount.delete",
                resource_id="disc-1",
                data={},
                task_id="celery-task-xyz",
            )

    mark_failed_mock.assert_awaited_once()
    notify_mock.assert_awaited_once()
    assert notify_mock.await_args is not None
    kwargs = notify_mock.await_args.kwargs
    assert kwargs["task_id"] == "celery-task-xyz"
    assert kwargs["writer_key"] == "discount.delete"
    assert kwargs["exception"] is exc
