from __future__ import annotations

from typing import Any

from core.queue.provider import QueueProvider
from core.queue.types import QueueJobResult, QueueTaskKey


def _pick_celery_task_name(task_key: str) -> str:
    """Choose the celery wrapper task based on the task_key prefix.

    The wrappers are bound to dedicated queues in ``celery_worker.py`` —
    matching on task_key keeps route code decoupled from queue plumbing.
    """
    key = str(task_key)
    if key == "db.write" or key.startswith("db.write:") or key.startswith("write."):
        return "celery_worker.run_write_task"
    if key.startswith("gate."):
        return "celery_worker.run_gate_task"
    if key.startswith("precompute."):
        return "celery_worker.run_precompute_task"
    return "celery_worker.run_async_task"


class CeleryQueueProvider(QueueProvider):
    backend_name = "celery"

    def __init__(self, celery_app: Any) -> None:
        self._celery_app = celery_app

    def enqueue(
        self,
        task_key: QueueTaskKey,
        payload: dict[str, Any],
        task_id: str | None = None,
    ) -> QueueJobResult:
        wrapper = _pick_celery_task_name(str(task_key))
        send_kwargs: dict[str, Any] = {"args": [str(task_key), payload]}
        if task_id:
            send_kwargs["task_id"] = task_id
        result = self._celery_app.send_task(wrapper, **send_kwargs)
        return QueueJobResult(
            task_id=result.id, backend=self.backend_name, status="queued"
        )

    def enqueue_in(
        self,
        seconds: int,
        task_key: QueueTaskKey,
        payload: dict[str, Any],
        task_id: str | None = None,
    ) -> QueueJobResult:
        wrapper = _pick_celery_task_name(str(task_key))
        send_kwargs: dict[str, Any] = {
            "args": [str(task_key), payload],
            "countdown": max(seconds, 0),
        }
        if task_id:
            send_kwargs["task_id"] = task_id
        result = self._celery_app.send_task(wrapper, **send_kwargs)
        return QueueJobResult(
            task_id=result.id, backend=self.backend_name, status="scheduled"
        )

    def get_status(self, task_id: str) -> str:
        return self._celery_app.AsyncResult(task_id).status

    def revoke(self, task_id: str) -> None:
        self._celery_app.control.revoke(task_id)
