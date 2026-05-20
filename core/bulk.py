"""Shared bulk-action helpers.

Frontend bulk endpoints take ``{ ids: [...], <extras> }`` and return a
queued job: ``202 Accepted + { id, job_id, status: "queued" }``. The
worker-side bulk writer iterates the ids, captures per-id success /
failure, and writes the result onto ``queue_job_log.result``. Clients
poll ``GET /v1/jobs/{job_id}`` for the final ``{ succeeded, failed }``
breakdown.

This module enforces the cross-cutting safety rules described in the
frontend tables spec:

* The ids array is required and capped per call (default 500). A
  larger batch should be split client-side; refusing it here also
  blocks an attacker from blowing up the worker on a single request.
* Every id is validated as a hex ``ObjectId`` before being forwarded
  to the worker. This prevents Mongo operator injection (``{$ne: ""}``)
  and keeps writers from having to defend themselves.
* Duplicate ids are dropped — the worker shouldn't double-process the
  same row, and the de-dupe is idempotent for replays.
* All bulk endpoints use the queued write pipeline so the audit row,
  cache invalidation cascades, and dirty-scope markers all work the
  same as for single writes.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from bson import ObjectId

from core.errors import AppException, ErrorCode
from core.queue.write_pipeline import enqueue_write

DEFAULT_MAX_BATCH = 500


def _bulk_error(
    message: str, code: str, details: Optional[dict[str, Any]] = None
) -> AppException:
    detail_payload: dict[str, Any] = {"code": code}
    if details:
        detail_payload.update(details)
    return AppException(
        status_code=400,
        code=ErrorCode.VALIDATION_FAILED,
        message=message,
        details=detail_payload,
    )


def validate_bulk_ids(
    ids: Sequence[Any],
    *,
    max_batch: int = DEFAULT_MAX_BATCH,
    field_name: str = "ids",
) -> list[str]:
    """Return a deduped list of valid ObjectId strings or raise 400.

    Rejects: non-string entries, malformed ObjectIds, empty input, and
    inputs over ``max_batch``. The ordering of input is preserved.
    """
    if not isinstance(ids, (list, tuple)):
        raise _bulk_error(
            f"{field_name} must be an array",
            "BULK_IDS_INVALID",
            {"field": field_name},
        )
    if not ids:
        raise _bulk_error(
            f"{field_name} must not be empty",
            "BULK_IDS_EMPTY",
            {"field": field_name},
        )
    if len(ids) > max_batch:
        raise _bulk_error(
            f"{field_name} exceeds the per-call cap of {max_batch}",
            "BULK_BATCH_TOO_LARGE",
            {"field": field_name, "max_batch": max_batch, "received": len(ids)},
        )

    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in ids:
        if not isinstance(raw, str):
            raise _bulk_error(
                f"{field_name} entries must be strings",
                "BULK_ID_INVALID",
            )
        if not ObjectId.is_valid(raw):
            raise _bulk_error(
                f"Invalid id in {field_name}: {raw!r}",
                "BULK_ID_INVALID",
                {"id": raw},
            )
        if raw in seen:
            continue
        seen.add(raw)
        cleaned.append(raw)
    return cleaned


async def enqueue_bulk_write(
    *,
    writer_key: str,
    ids: Sequence[Any],
    resource_type: str,
    extras: Optional[dict[str, Any]] = None,
    atomic: bool = False,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
    max_batch: int = DEFAULT_MAX_BATCH,
) -> dict[str, str]:
    """Validate the input and enqueue ONE bulk-writer task.

    The returned ``id`` is the synthetic id of the bulk envelope (not a
    resource id) — clients should treat it as opaque and poll
    ``/v1/jobs/{job_id}`` for the per-id success/failure outcome.
    """
    cleaned_ids = validate_bulk_ids(ids, max_batch=max_batch)
    payload: dict[str, Any] = {
        "ids": cleaned_ids,
        "atomic": bool(atomic),
        "extras": dict(extras) if extras else {},
    }
    return await enqueue_write(
        writer_key=writer_key,
        payload=payload,
        resource_type=resource_type,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=request_id,
    )


async def run_bulk_handlers(
    ids: Sequence[str],
    handler: Any,
    *,
    atomic: bool = False,
) -> dict[str, Any]:
    """Apply ``handler(id)`` to each id and collect results.

    ``handler`` is an async callable that succeeds or raises; success
    payloads (when returned) are surfaced under each entry of
    ``succeeded``. When ``atomic`` is true and any id raises, all
    completed work is rolled back via the registered compensator on the
    handler — but in practice atomic mode is reserved for writers that
    can opt in by overriding this function (most writers run
    non-atomically because Mongo does not give us cross-collection
    transactions in this codebase).
    """
    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for resource_id in ids:
        try:
            result = await handler(resource_id)
            entry: dict[str, Any] = {"id": resource_id}
            if isinstance(result, dict):
                entry["result"] = result
            succeeded.append(entry)
        except AppException as exc:
            detail = (
                exc.detail
                if isinstance(exc.detail, dict)
                else {"message": str(exc.detail)}
            )
            failed.append(
                {
                    "id": resource_id,
                    "error": {
                        "code": detail.get("code", "BULK_ITEM_FAILED"),
                        "message": detail.get("message", "Item failed"),
                    },
                }
            )
            if atomic:
                break
        except Exception as exc:
            failed.append(
                {
                    "id": resource_id,
                    "error": {
                        "code": "BULK_ITEM_FAILED",
                        "message": str(exc)[:200],
                    },
                }
            )
            if atomic:
                break
    return {"succeeded": succeeded, "failed": failed, "atomic": atomic}


__all__ = [
    "DEFAULT_MAX_BATCH",
    "validate_bulk_ids",
    "enqueue_bulk_write",
    "run_bulk_handlers",
]
