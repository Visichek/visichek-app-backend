"""Enqueue-driven write pipeline.

Routes no longer hit the database directly for mutations. Instead they
call :func:`enqueue_write`, which:

* Pre-generates an ``ObjectId`` when the caller does not supply a
  ``resource_id`` — the frontend receives a stable id immediately so it
  can poll the resource or its list view.
* Inserts a ``queue_job_log`` audit row keyed by the celery ``task_id``.
* Enqueues a ``db.write`` task. The worker-side dispatcher (registered
  in :mod:`core.queue.tasks`) looks up the concrete writer in the local
  registry via ``writer_key`` and calls it with the payload.

Writer functions register themselves with :func:`write_handler`. Each
receives the pre-assigned ``resource_id`` plus the JSON payload that was
enqueued and must return a JSON-serialisable dict (logged as the task
result) or ``None``.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from bson import ObjectId

from core.queue.manager import QueueManager
from repositories.queue_job_log_repo import insert_job_log
from schemas.queue_job_log_schema import QueueJobLogCreate, QueueJobStatus

logger = logging.getLogger(__name__)

WriterFunc = Callable[..., Awaitable[Optional[dict[str, Any]]]]
_WRITE_REGISTRY: dict[str, WriterFunc] = {}

# Payload keys that are redacted before persistence to queue_job_log.
_REDACTED_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "secret",
        "token",
        "refresh_token",
        "access_token",
        "api_key",
        "otp",
        "totp",
        "backup_code",
    }
)


def register_writer(writer_key: str, func: WriterFunc) -> None:
    """Register a writer function under ``writer_key``."""
    if writer_key in _WRITE_REGISTRY:
        raise ValueError(f"Writer '{writer_key}' is already registered")
    _WRITE_REGISTRY[writer_key] = func


def write_handler(writer_key: str) -> Callable[[WriterFunc], WriterFunc]:
    """Decorator: register an async function as the writer for ``writer_key``."""

    def decorator(func: WriterFunc) -> WriterFunc:
        register_writer(writer_key, func)
        return func

    return decorator


async def execute_writer(
    writer_key: str, resource_id: str, data: dict[str, Any]
) -> Optional[dict[str, Any]]:
    target = _WRITE_REGISTRY.get(writer_key)
    if target is None:
        available = ", ".join(sorted(_WRITE_REGISTRY)) or "<none>"
        raise ValueError(
            f"Writer '{writer_key}' not registered. Available: {available}"
        )
    return await target(resource_id=resource_id, data=data)


def list_registered_writers() -> list[str]:
    return sorted(_WRITE_REGISTRY.keys())


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _REDACTED_KEYS:
            clean[key] = "***"
        elif isinstance(value, dict):
            clean[key] = _redact(value)
        elif isinstance(value, list):
            clean[key] = [_redact(v) if isinstance(v, dict) else v for v in value]
        else:
            clean[key] = value
    return clean


async def enqueue_write(
    *,
    writer_key: str,
    payload: dict[str, Any],
    resource_type: str,
    resource_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict[str, str]:
    """Enqueue a DB mutation and persist an audit row.

    Returns ``{ id, job_id, status }`` — the id is generated up-front so the
    caller can return it to the client before the write has actually run.
    """
    if not resource_id:
        resource_id = str(ObjectId())

    job_payload: dict[str, Any] = {
        "writer_key": writer_key,
        "resource_id": resource_id,
        "data": payload,
    }

    job_result = QueueManager.get_instance().enqueue(
        task_key="db.write", payload=job_payload
    )

    try:
        await insert_job_log(
            QueueJobLogCreate(
                task_id=job_result.task_id,
                task_key=f"db.write:{writer_key}",
                resource_type=resource_type,
                resource_id=resource_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                actor_role=actor_role,
                request_id=request_id,
                status=QueueJobStatus.QUEUED,
                payload_redacted=_redact(payload),
            )
        )
    except Exception:
        logger.exception(
            "Failed to persist queue_job_log for task_id=%s writer=%s",
            job_result.task_id,
            writer_key,
        )

    return {
        "id": resource_id,
        "job_id": job_result.task_id,
        "status": "queued",
    }
