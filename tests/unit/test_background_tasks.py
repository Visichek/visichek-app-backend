"""Unit tests for the fire-and-forget background helper."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from core import background_tasks

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _reset_pending():
    # Drain anything a prior test leaked (shouldn't happen, but defensive).
    await background_tasks.drain_pending(timeout=0.1)
    yield
    await background_tasks.drain_pending(timeout=0.1)


async def test_fire_and_forget_runs_coroutine() -> None:
    observed = MagicMock()

    async def _work() -> None:
        await asyncio.sleep(0)
        observed("done")

    task = background_tasks.fire_and_forget(_work())
    assert task is not None
    await task
    observed.assert_called_once_with("done")


async def test_fire_and_forget_suppresses_exceptions() -> None:
    async def _blow_up() -> None:
        raise RuntimeError("boom")

    task = background_tasks.fire_and_forget(_blow_up())
    assert task is not None
    # Waiting on the task must not propagate the exception via _on_done.
    await asyncio.gather(task, return_exceptions=True)
    # The exception is captured on the task object itself, but the done
    # callback logged and swallowed it.
    assert isinstance(task.exception(), RuntimeError)


async def test_drain_pending_waits_for_tasks() -> None:
    flag = {"done": False}

    async def _slow() -> None:
        await asyncio.sleep(0.05)
        flag["done"] = True

    background_tasks.fire_and_forget(_slow())
    await background_tasks.drain_pending(timeout=1.0)
    assert flag["done"] is True
    assert background_tasks.pending_count() == 0


async def test_drain_pending_respects_timeout() -> None:
    async def _forever() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise

    background_tasks.fire_and_forget(_forever())
    # Should return without hanging even though the task is still running.
    await background_tasks.drain_pending(timeout=0.05)
    # Clean up so we don't leak into other tests.
    for t in list(background_tasks._pending):  # type: ignore[attr-defined]
        t.cancel()
    await background_tasks.drain_pending(timeout=0.5)
