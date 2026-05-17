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
from typing import Any, Awaitable, Callable, Iterable, Optional
from uuid import uuid4

from bson import ObjectId

from core.queue.entity_cache import invalidate_entity
from core.queue.manager import QueueManager
from core.queue.precompute import (
    PrecomputeScope,
    delete_precompute,
    get_resource_scope,
    mark_scope_dirty,
)
from repositories.queue_job_log_repo import insert_job_log
from schemas.queue_job_log_schema import QueueJobLogCreate, QueueJobStatus

logger = logging.getLogger(__name__)

WriterFunc = Callable[..., Awaitable[Optional[dict[str, Any]]]]
_WRITE_REGISTRY: dict[str, WriterFunc] = {}

# Maps writer_key → list of precompute resource names that the write
# invalidates. Populated via the `invalidates=[...]` arg on
# :func:`write_handler`. Used by :func:`enqueue_write` to eagerly
# delete cached payloads + flag the scope dirty so reads in the
# worker-commit gap don't re-populate the cache with stale data.
_INVALIDATION_REGISTRY: dict[str, tuple[str, ...]] = {}

# Resources that aggregate / record every tenant write — cascaded
# automatically when a writer is invoked with a ``tenant_id``. The audit
# trail records every action, the dashboard rolls up counts, and the
# tenant-scoped usage summary tracks quota burn.
_AUTO_TENANT_CASCADE: tuple[str, ...] = (
    "audit.recent",
    "dashboard.stats",
    "usage.my_usage",
)

# Resources that aggregate every write at the platform level. Always
# cascaded so application admin views stay live across writes.
_AUTO_GLOBAL_CASCADE: tuple[str, ...] = (
    "audit.admin_recent",
    "admin_dashboard.stats",
)

# Writer-key prefixes that touch the billing surface. Reaching any of
# these cascades to the billing rollups (revenue / discrepancies / admin
# invoice list) so application-admin billing views reflect changes
# without waiting for the 60s precompute fanout.
_BILLING_PREFIXES: tuple[str, ...] = (
    "subscription.",
    "invoice.",
    "discount.",
    "plan.",
)
_BILLING_CASCADE: tuple[str, ...] = (
    "admin_dashboard.billing_30d",
    "admin_dashboard.discrepancies",
    "invoices.admin_list",
    "invoices.for_tenant",
)

# Per-id entity_cache entries that piggy-back off a writer's tenant_id.
# The HttpCache dirty marker covers authenticated reads, but public
# endpoints (kiosk / public registration) run in the anonymous scope and
# can't be flagged that way — so writers that change tenant-public state
# explicitly drop the per-id cache key for the affected public resource.
_TENANT_ENTITY_INVALIDATIONS: dict[str, tuple[str, ...]] = {
    "tenant.create": ("public_tenant_info",),
    "tenant.update": ("public_tenant_info",),
    "department.create": ("public_tenant_departments",),
    "department.update": ("public_tenant_departments",),
    "department.delete": ("public_tenant_departments",),
    "privacy_notice.create": ("public_privacy_notice",),
    "privacy_notice.update": ("public_privacy_notice",),
    "branding.upsert": ("public_tenant_info",),
    "branding.delete": ("public_tenant_info",),
    "checkin_config.create": ("checkin_config", "public_tenant_info"),
    "checkin_config.update": ("checkin_config", "public_tenant_info"),
}

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


def register_writer_invalidations(
    writer_key: str, resources: Iterable[str]
) -> None:
    """Record which precompute resources a writer invalidates.

    Called from :func:`write_handler` when ``invalidates`` is provided.
    Re-registration extends the list rather than replacing it so a
    handler decorated multiple times accumulates invalidations.
    """
    existing = _INVALIDATION_REGISTRY.get(writer_key, ())
    merged: list[str] = list(existing)
    for resource in resources:
        if resource not in merged:
            merged.append(resource)
    _INVALIDATION_REGISTRY[writer_key] = tuple(merged)


def get_writer_invalidations(writer_key: str) -> tuple[str, ...]:
    return _INVALIDATION_REGISTRY.get(writer_key, ())


def write_handler(
    writer_key: str,
    *,
    invalidates: Optional[Iterable[str]] = None,
) -> Callable[[WriterFunc], WriterFunc]:
    """Decorator: register an async function as the writer for ``writer_key``.

    ``invalidates`` is the list of precompute resource names this writer
    affects. ``enqueue_write`` uses it to eagerly drop the cached payload
    and mark the scope dirty so the next read sees fresh state instead of
    waiting on the precompute worker.
    """

    def decorator(func: WriterFunc) -> WriterFunc:
        register_writer(writer_key, func)
        if invalidates:
            register_writer_invalidations(writer_key, invalidates)
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


def _resolve_cascade_resources(
    writer_key: str, tenant_id: Optional[str]
) -> tuple[str, ...]:
    """Return the auto-cascade resources for a writer.

    Combines:
    * Per-writer ``invalidates=[...]`` declarations.
    * Tenant cascades (audit / dashboard / usage) when ``tenant_id`` is set.
    * Global cascades (audit / admin dashboard) — always included so
      platform-wide views never go stale, regardless of whether the
      writer ran in tenant context.
    * Billing cascades for subscription / invoice / discount / plan
      writers.

    De-duplicated while preserving registration order so the loop in
    ``_invalidate_caches_for_writer`` sees each resource once.
    """
    declared = list(get_writer_invalidations(writer_key))
    cascades: list[str] = list(declared)

    if tenant_id:
        for r in _AUTO_TENANT_CASCADE:
            if r not in cascades:
                cascades.append(r)

    for r in _AUTO_GLOBAL_CASCADE:
        if r not in cascades:
            cascades.append(r)

    if writer_key.startswith(_BILLING_PREFIXES):
        for r in _BILLING_CASCADE:
            if r not in cascades:
                cascades.append(r)

    return tuple(cascades)


def _invalidate_caches_for_writer(
    *,
    writer_key: str,
    tenant_id: Optional[str],
    actor_id: Optional[str],
    actor_role: Optional[str],
) -> None:
    """Eagerly drop precompute keys + set dirty scope markers.

    Runs on the request hot path — Redis ops only, no DB. Bounds visibility
    of the in-flight write to ``DIRTY_TTL_SECONDS`` even before the worker
    commits, by forcing reads in the gap to bypass the HTTP cache and
    refusing to repopulate the precompute with stale loader output.

    Cascade behaviour: along with the writer's declared ``invalidates``,
    every write also drops broad aggregations (``audit.*``,
    ``dashboard.stats``, ``admin_dashboard.stats``) and — for billing
    writers — the revenue / discrepancy rollups. This keeps cross-list
    views (dashboards, audit feeds, billing tables) live across foreign-
    key changes without each writer needing to enumerate the full graph.
    """
    resources = _resolve_cascade_resources(writer_key, tenant_id)
    dirty_scopes: set[str] = set()

    for resource in resources:
        scope = get_resource_scope(resource)
        if scope is None:
            continue
        if scope is PrecomputeScope.TENANT and tenant_id:
            delete_precompute(resource, tenant_id=tenant_id)
            dirty_scopes.add(f"t:{tenant_id}")
        elif scope is PrecomputeScope.USER and actor_id:
            delete_precompute(resource, tenant_id=tenant_id, user_id=actor_id)
            dirty_scopes.add(f"usr:{actor_id}")
            if tenant_id:
                dirty_scopes.add(f"t:{tenant_id}")
        elif scope is PrecomputeScope.GLOBAL:
            delete_precompute(resource)
            # Global resources (plans, tenants list, admin dashboard) are
            # consumed across every authenticated scope — flag the global
            # marker so HttpCache bypasses for everyone in the dirty TTL.
            dirty_scopes.add("global")
            if tenant_id:
                dirty_scopes.add(f"t:{tenant_id}")
            if actor_id and actor_role == "admin":
                dirty_scopes.add(f"adm:{actor_id}")

    # Even with no declared invalidations, a write should at least dirty
    # the actor's own scope so HttpCache responses cached pre-write don't
    # mask the new state for up to 60s.
    if not dirty_scopes:
        if tenant_id:
            dirty_scopes.add(f"t:{tenant_id}")
        if actor_id and actor_role == "admin":
            dirty_scopes.add(f"adm:{actor_id}")
        elif actor_id and actor_role == "user":
            dirty_scopes.add(f"usr:{actor_id}")

    for dirty_scope in dirty_scopes:
        mark_scope_dirty(dirty_scope)


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

    _invalidate_caches_for_writer(
        writer_key=writer_key,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
    )
    # Drop the per-id cache up front so a read in the worker-commit gap
    # doesn't return the pre-write snapshot. The dispatcher invalidates
    # again post-commit which covers the (rare) case where a concurrent
    # GET repopulates the cache between this delete and commit.
    invalidate_entity(resource_type, resource_id)
    # Public endpoints (kiosk / registration / login screens) run in the
    # anonymous HttpCache scope which the tenant dirty marker doesn't
    # cover. Drop their per-id cache keys explicitly so kiosks see
    # branding / department / notice changes without waiting on TTL.
    for public_type in _TENANT_ENTITY_INVALIDATIONS.get(writer_key, ()):
        if tenant_id:
            invalidate_entity(public_type, tenant_id)
        if public_type == "checkin_config":
            invalidate_entity("checkin_config", resource_id)

    # Pre-generate the celery task_id so we can insert the audit row BEFORE
    # the worker ever sees the task. Otherwise a fast worker can start
    # processing (mark_processing / mark_failed / notify_job_failure) before
    # the queue_job_log insert completes, leaving those lookups racy.
    task_id = str(uuid4())

    try:
        await insert_job_log(
            QueueJobLogCreate(
                task_id=task_id,
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
            task_id,
            writer_key,
        )

    job_payload: dict[str, Any] = {
        "writer_key": writer_key,
        "resource_id": resource_id,
        "data": payload,
        "task_id": task_id,
        "resource_type": resource_type,
    }

    job_result = QueueManager.get_instance().enqueue(
        task_key="db.write", payload=job_payload, task_id=task_id
    )

    return {
        "id": resource_id,
        "job_id": job_result.task_id,
        "status": "queued",
    }


async def record_inline_completed_write(
    *,
    writer_key: str,
    payload: dict[str, Any],
    resource_type: str,
    result: dict[str, Any],
    resource_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict[str, str]:
    """Record a write that has ALREADY completed inline on the request thread.

    Same response shape as :func:`enqueue_write` (``{ id, job_id, status }``)
    so callers can swap one for the other without changing their HTTP
    contract. The difference is:

    * No celery task is enqueued — the work was done synchronously by
      the caller.
    * The ``queue_job_log`` row is written with
      ``status=QueueJobStatus.SUCCEEDED`` and the ``result`` populated.
      The first poll of ``GET /v1/jobs/{job_id}`` resolves immediately.
    * Cache invalidation cascades fire exactly as in ``enqueue_write``
      so reads stay consistent.

    Used by routes that need to do heavy I/O (e.g. streaming a video
    upload to R2) that would crash if shuffled through the celery
    broker as a base64-encoded JSON payload, while keeping the
    queued-write API contract the frontend expects.
    """
    if not resource_id:
        resource_id = str(ObjectId())

    _invalidate_caches_for_writer(
        writer_key=writer_key,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
    )
    invalidate_entity(resource_type, resource_id)
    for public_type in _TENANT_ENTITY_INVALIDATIONS.get(writer_key, ()):
        if tenant_id:
            invalidate_entity(public_type, tenant_id)
        if public_type == "checkin_config":
            invalidate_entity("checkin_config", resource_id)

    task_id = str(uuid4())
    try:
        await insert_job_log(
            QueueJobLogCreate(
                task_id=task_id,
                task_key=f"db.write:{writer_key}",
                resource_type=resource_type,
                resource_id=resource_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                actor_role=actor_role,
                request_id=request_id,
                status=QueueJobStatus.SUCCEEDED,
                payload_redacted=_redact(payload),
                result=result,
            )
        )
    except Exception:
        logger.exception(
            "Failed to persist inline-completed queue_job_log "
            "for task_id=%s writer=%s",
            task_id,
            writer_key,
        )

    return {
        "id": resource_id,
        "job_id": task_id,
        "status": "queued",
    }
