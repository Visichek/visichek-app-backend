from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

TaskFunc = Callable[..., Awaitable[Any]]
_TASK_REGISTRY: dict[str, TaskFunc] = {}

logger = logging.getLogger(__name__)


def register_task(task_key: str, func: TaskFunc) -> None:
    if task_key in _TASK_REGISTRY:
        raise ValueError(f"Task key '{task_key}' is already registered")
    _TASK_REGISTRY[task_key] = func


def task(task_key: str) -> Callable[[TaskFunc], TaskFunc]:
    def decorator(func: TaskFunc) -> TaskFunc:
        register_task(task_key, func)
        return func

    return decorator


async def execute_registered_task(task_key: str, payload: dict[str, Any]) -> Any:
    target = _TASK_REGISTRY.get(task_key)
    if target is None:
        valid_keys = ", ".join(sorted(_TASK_REGISTRY)) or "<none>"
        raise ValueError(
            f"Task key '{task_key}' is not registered. Available keys: {valid_keys}"
        )
    return await target(**payload)


def list_registered_task_keys() -> list[str]:
    return sorted(_TASK_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Registered tasks
# ---------------------------------------------------------------------------
#
# Keep handlers thin — they should load the minimum context from their
# payload (IDs + timestamps), delegate to a service function, and let the
# service layer own the real logic. Payloads must be JSON-serializable; do
# not pass Pydantic models, ``ObjectId``, or ``datetime``.


@task("invoice.generate_pdf")
async def _invoice_generate_pdf(invoice_id: str) -> None:
    """Render the invoice PDF and attach the object key back to the record.

    Called from the billing path (renewal, dunning, checkout success) to keep
    the synchronous reportlab+storage work off the request hot path.
    """
    from bson import ObjectId

    from repositories.invoice_repo import get_invoice, update_invoice
    from schemas.invoice_schema import InvoiceUpdate
    from services.invoice_pdf_service import generate_invoice_pdf

    if not ObjectId.is_valid(invoice_id):
        logger.warning("invoice.generate_pdf: invalid invoice_id=%s", invoice_id)
        return
    invoice = await get_invoice({"_id": ObjectId(invoice_id)})
    if not invoice:
        logger.warning("invoice.generate_pdf: invoice not found id=%s", invoice_id)
        return
    pdf_object_key = await generate_invoice_pdf(invoice)
    if pdf_object_key:
        await update_invoice(
            invoice.id or "", InvoiceUpdate(pdf_object_key=pdf_object_key)
        )
        logger.info(
            "invoice.generate_pdf: attached pdf to invoice id=%s key=%s",
            invoice_id,
            pdf_object_key,
        )


@task("cache.plan.invalidate_plan_fanout")
async def _invalidate_plan_fanout(plan_id: str) -> None:
    """Sweep the tenant_plan cache for any tenant subscribed to ``plan_id``.

    Moved off the request path because it does a Redis SCAN which is O(size
    of keyspace) and was being run inline on every plan update.
    """
    from services.plan_cache_service import invalidate_plan_cache

    await invalidate_plan_cache(plan_id)
    logger.info("cache.plan.invalidate_plan_fanout: done plan_id=%s", plan_id)


# ---------------------------------------------------------------------------
# Write pipeline dispatcher
# ---------------------------------------------------------------------------


# Writer keys whose handler (or the service it delegates to) already records
# its own audit event. Listed here so the auto-audit step below skips them
# and we don't get duplicate entries in /v1/audit-logs.
_AUTO_AUDIT_SKIP: frozenset[str] = frozenset(
    {
        # appointment_writer self-audits
        "appointment.create",
        "appointment.update",
        "appointment.delete",
        # visitor_profile_writer self-audits the DSR erasure lifecycle
        "visitor_profile.erase",
        "visitor_profile.restore",
        # system_user_service self-audits these specific operations
        "system_user.invite",
        "system_user.delete",
        # branding_service self-audits
        "branding.upsert",
        "branding.delete",
        # subscription_service self-audits
        "subscription.create",
        "subscription.change_plan",
        "subscription.cancel",
        "subscription.update_overrides",
        # plan_service self-audits these specific operations
        "plan.create",
        "plan.archive",
        "plan.activate",
        "plan.delete",
        # support_case_service self-audits
        "support_case.create",
        "support_case.message.add",
        "support_case.transition",
        "support_case.assign",
        "support_case.attachment.add",
        # tenant_settings_service self-audits
        "tenant_settings.update",
    }
)


@task("db.write")
async def _db_write_dispatcher(
    writer_key: str,
    resource_id: str,
    data: dict[str, Any],
    task_id: str = "",
    resource_type: str = "",
) -> Any:
    """Dispatch a queued write to its registered handler.

    ``task_id`` is threaded through from ``enqueue_write`` (which
    pre-generates it before the celery send) so the same row in
    ``queue_job_log`` can be transitioned through processing ->
    succeeded / failed without depending on ``celery.current_task``
    propagation, which is fragile under ``celery-aio-pool``.

    ``resource_type`` is used to invalidate the per-id entity cache
    after the write commits. The cache is also invalidated up-front in
    ``enqueue_write``; the post-commit invalidation closes the (rare)
    race where a concurrent GET repopulates the cache between the
    enqueue-time delete and the commit.
    """
    from core.queue.entity_cache import invalidate_entity
    from core.queue.write_pipeline import execute_writer
    from repositories.queue_job_log_repo import (
        mark_failed,
        mark_processing,
        mark_succeeded,
    )

    try:
        await mark_processing(task_id)
    except Exception:
        logger.warning("mark_processing failed for task_id=%s", task_id, exc_info=True)

    try:
        result = await execute_writer(
            writer_key=writer_key, resource_id=resource_id, data=data
        )
    except Exception as exc:
        try:
            await mark_failed(task_id, f"{type(exc).__name__}: {exc}")
        except Exception:
            logger.warning(
                "mark_failed logging failed for task_id=%s",
                task_id,
                exc_info=True,
            )
        try:
            from services.notification_service import notify_job_failure

            await notify_job_failure(
                task_id=task_id, writer_key=writer_key, exception=exc
            )
        except Exception:
            logger.warning(
                "job-failure notification failed for task_id=%s",
                task_id,
                exc_info=True,
            )
        logger.exception(
            "db.write failed: writer=%s resource_id=%s", writer_key, resource_id
        )
        # FastAPI's HTTPException (and some other service-layer exceptions)
        # are not picklable by celery's result backend, producing noisy
        # UnpickleableExceptionWrapper tracebacks. The job_log row is
        # already marked FAILED above with the original type+message, so
        # surface a plain RuntimeError to celery for a clean traceback.
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    try:
        await mark_succeeded(task_id, result if isinstance(result, dict) else None)
    except Exception:
        logger.warning(
            "mark_succeeded logging failed for task_id=%s",
            task_id,
            exc_info=True,
        )

    # Auto-record an audit event for every successful queued write so the
    # tenant audit log isn't empty just because a writer / service forgot
    # to call record_audit_event. Skipped for writers in _AUTO_AUDIT_SKIP
    # which already self-audit (avoids duplicate entries).
    if writer_key not in _AUTO_AUDIT_SKIP:
        await _auto_record_audit_for_write(
            writer_key=writer_key,
            task_id=task_id,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
        )

    # Drop the per-id cache again post-commit. The eager delete in
    # enqueue_write covers the gap before this point; this second drop
    # handles the rare case where a concurrent GET re-populated the
    # cache with pre-commit state between enqueue and worker commit.
    if resource_type and resource_id:
        invalidate_entity(resource_type, resource_id)

    return result


async def _auto_record_audit_for_write(
    *,
    writer_key: str,
    task_id: str,
    resource_type: str,
    resource_id: str,
    result: Any,
) -> None:
    """Record an audit event using actor info from queue_job_log.

    Reads the row inserted by ``enqueue_write`` to recover the actor /
    tenant / request context that the route originally captured, then
    fires off ``record_audit_event``. Failures are swallowed — a missing
    audit row should never fail the write itself.
    """
    if not task_id:
        return
    try:
        from repositories.queue_job_log_repo import get_job_log_by_task_id
        from services.audit_service import record_audit_event

        job_log = await get_job_log_by_task_id(task_id)
        if job_log is None or not job_log.actor_id:
            return

        # Prefer the writer's reported id (real, post-commit) over the
        # speculative pre-assigned one in queue_job_log. Some writers
        # like subscription.create assign their own ids server-side.
        effective_resource_id = resource_id
        if isinstance(result, dict):
            res_id = result.get("id")
            if isinstance(res_id, str) and res_id:
                effective_resource_id = res_id

        await record_audit_event(
            actor_id=job_log.actor_id,
            actor_role=job_log.actor_role or "system_user",
            action=writer_key,
            resource_type=resource_type or job_log.resource_type or "",
            resource_id=effective_resource_id or job_log.resource_id or "",
            tenant_id=job_log.tenant_id,
            details={"task_id": task_id},
            request_id=job_log.request_id,
        )
    except Exception:
        logger.warning(
            "auto-audit failed for task_id=%s writer=%s",
            task_id,
            writer_key,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Gate cache refresh
# ---------------------------------------------------------------------------


@task("gate.refresh")
async def _gate_refresh(user_id: str, role: str) -> None:
    """Re-run the full account-status + permission check and write the
    fresh state into Redis under the gate-cache key.

    Enqueued from the hot path whenever a cached gate is served so the
    next request sees up-to-date state without paying the DB latency.
    """
    from core.queue.gate_cache import refresh_gate_state

    await refresh_gate_state(user_id=user_id, role=role)


# ---------------------------------------------------------------------------
# Precompute pipeline
# ---------------------------------------------------------------------------


@task("precompute.tenant_resource")
async def _precompute_tenant_resource(
    tenant_id: str, resource: str, user_id: str = ""
) -> None:
    """Compute a named GET payload and store it in Redis.

    ``resource`` identifies the precomputed view (e.g. ``departments.list``,
    ``dashboard.stats``, ``notifications.unread_count``). The scope of the
    view comes from the registry entry — user-scoped resources receive
    ``user_id`` from the fanout so a single handler covers all scopes.
    """
    from core.queue.precompute import run_precompute

    await run_precompute(
        tenant_id=tenant_id,
        resource=resource,
        user_id=user_id or None,
    )


@task("precompute.fanout_active_tenants")
async def _precompute_fanout_active_tenants() -> None:
    """Fan out `precompute.tenant_resource` for every active tenant.

    Scans Redis for live auth-token markers and enqueues a refresh per
    registered precompute resource. Scheduled on APScheduler.
    """
    from core.queue.precompute import fanout_for_active_tenants

    await fanout_for_active_tenants()
