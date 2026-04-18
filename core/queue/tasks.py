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
