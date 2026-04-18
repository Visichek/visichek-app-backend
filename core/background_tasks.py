"""Helpers for scheduling work that shouldn't block the request response.

Two primitives:

- ``fire_and_forget(coro)`` — schedule a coroutine on the running loop and
  return immediately. Errors get logged but don't propagate. Use for audit
  writes, non-critical cache invalidations, fan-out notifications.
- ``drain_pending(timeout)`` — wait for in-flight background tasks to
  finish. Call during the lifespan shutdown so we don't drop work on graceful
  reload.

What this is NOT for:

- CPU-bound work (reportlab, OCR) — those need ``asyncio.to_thread`` OR a
  Celery task so they don't block the event loop.
- Work whose *result* the caller needs — if your response body depends on
  it, ``await`` it. Background tasks disappear on process crash.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger(__name__)

# Strong references to in-flight tasks. Without this, the asyncio event loop
# can garbage-collect tasks mid-flight (see CPython bug #91887).
_pending: Set[asyncio.Task[Any]] = set()


def fire_and_forget(
    coro: Coroutine[Any, Any, Any], *, name: Optional[str] = None
) -> Optional[asyncio.Task[Any]]:
    """Schedule ``coro`` on the current event loop and return immediately.

    Returns the scheduled ``Task`` so tests can optionally await it. Returns
    ``None`` when called with no running loop (e.g. in some test harnesses)
    and the coroutine is silently closed — avoid calling this outside an
    async request handler.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — close the coroutine to suppress "never awaited"
        # warnings and return None. Caller should not be using this path.
        try:
            coro.close()
        except Exception:
            pass
        logger.debug("fire_and_forget called with no running loop; dropped")
        return None

    task = loop.create_task(coro, name=name)
    _pending.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task[Any]) -> None:
    _pending.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task %r failed", task.get_name(), exc_info=exc)


async def drain_pending(timeout: float = 5.0) -> None:
    """Wait for all in-flight background tasks to settle.

    Use in the lifespan shutdown path. Tasks still running after ``timeout``
    are logged and abandoned (the process is going down anyway).
    """
    if not _pending:
        return
    snapshot = list(_pending)
    try:
        await asyncio.wait_for(
            asyncio.gather(*snapshot, return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "drain_pending timed out after %.1fs with %d tasks still running",
            timeout,
            len(_pending),
        )


def pending_count() -> int:
    """How many background tasks are in-flight right now."""
    return len(_pending)
