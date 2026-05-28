"""Queued legal-document mutations + precompute loaders.

Routes call ``enqueue_write(writer_key="legal_document.{create,update,delete,
publish,archive}", ...)`` and return ``202 + job_id``. The celery
``worker-writes`` queue dispatches to the ``@write_handler`` functions here.

Every mutation records an audit event (platform-level actor) and refreshes
two GLOBAL precompute resources so admin edits surface on the public site
within ~1s of the worker committing:

* ``legal_documents.list_admin``     — admin list first page.
* ``legal_documents.list_published`` — public list first page.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from legal.schemas.imports import LegalDocStatus
from legal.schemas.legal_document_schema import (
    LegalDocumentCreate,
    LegalDocumentPublicListRow,
    LegalDocumentPublishRequest,
    LegalDocumentUpdate,
)
from legal.services.legal_document_service import (
    add_legal_document,
    archive_legal_document,
    publish_legal_document,
    remove_legal_document,
    retrieve_legal_documents,
    update_legal_document_by_id,
)
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)

_LEGAL_INVALIDATIONS = [
    "legal_documents.list_admin",
    "legal_documents.list_published",
]

_ACTOR_KEYS = ("_actor_id", "_actor_role", "_request_id")


def _pop_actor(data: dict[str, Any]) -> tuple[Optional[str], str, Optional[str]]:
    actor_id = data.pop("_actor_id", None)
    actor_role = data.pop("_actor_role", "admin") or "admin"
    request_id = data.pop("_request_id", None)
    # Defensive: strip any other reserved keys the schema doesn't expect.
    for k in _ACTOR_KEYS:
        data.pop(k, None)
    return actor_id, actor_role, request_id


async def _audit(
    *,
    actor_id: Optional[str],
    actor_role: str,
    action: str,
    resource_id: str,
    details: Optional[dict] = None,
    request_id: Optional[str] = None,
) -> None:
    try:
        await record_audit_event(
            actor_id=actor_id or "",
            actor_role=actor_role,
            action=action,
            resource_type="legal_document",
            resource_id=resource_id,
            tenant_id=None,
            details=details or {},
            request_id=request_id,
        )
    except Exception:  # pragma: no cover - audit is fire-and-forget
        logger.warning("legal audit failed action=%s id=%s", action, resource_id)


def _enqueue_legal_list_refresh() -> None:
    """Best-effort nudge to repopulate the precompute caches after a commit."""
    try:
        qm = QueueManager.get_instance()
    except RuntimeError:
        return
    for resource in _LEGAL_INVALIDATIONS:
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={"tenant_id": "", "resource": resource},
            )
        except Exception:
            logger.warning(
                "legal_document_writer: precompute refresh enqueue failed resource=%s",
                resource,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


@write_handler("legal_document.create", invalidates=_LEGAL_INVALIDATIONS)
async def _legal_create(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    doc = await add_legal_document(
        LegalDocumentCreate(**data), preassigned_id=resource_id
    )
    await _audit(
        actor_id=actor_id,
        actor_role=actor_role,
        action="legal_document.created",
        resource_id=doc.id or resource_id,
        details={"slug": doc.slug, "title": doc.title},
        request_id=request_id,
    )
    _enqueue_legal_list_refresh()
    return {
        "id": doc.id,
        "slug": doc.slug,
        "title": doc.title,
        "status": doc.status.value,
    }


@write_handler("legal_document.update", invalidates=_LEGAL_INVALIDATIONS)
async def _legal_update(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    changed = sorted(k for k in data.keys() if k != "last_updated")
    doc = await update_legal_document_by_id(resource_id, LegalDocumentUpdate(**data))
    await _audit(
        actor_id=actor_id,
        actor_role=actor_role,
        action="legal_document.updated",
        resource_id=resource_id,
        details={"changes": changed},
        request_id=request_id,
    )
    _enqueue_legal_list_refresh()
    return {"id": doc.id, "slug": doc.slug, "status": doc.status.value}


@write_handler("legal_document.delete", invalidates=_LEGAL_INVALIDATIONS)
async def _legal_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    result = await remove_legal_document(resource_id)
    await _audit(
        actor_id=actor_id,
        actor_role=actor_role,
        action="legal_document.deleted",
        resource_id=resource_id,
        request_id=request_id,
    )
    _enqueue_legal_list_refresh()
    return result


@write_handler("legal_document.publish", invalidates=_LEGAL_INVALIDATIONS)
async def _legal_publish(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    doc = await publish_legal_document(
        resource_id, LegalDocumentPublishRequest(**data), actor_id=actor_id
    )
    await _audit(
        actor_id=actor_id,
        actor_role=actor_role,
        action="legal_document.published",
        resource_id=resource_id,
        details={"version": doc.current_version, "effectiveAt": doc.effective_at},
        request_id=request_id,
    )
    _enqueue_legal_list_refresh()
    _on_agreement_master_published(doc.slug)
    return {
        "id": doc.id,
        "slug": doc.slug,
        "status": doc.status.value,
        "version": doc.current_version,
    }


def _on_agreement_master_published(slug: Optional[str]) -> None:
    """When an agreement master is (re)published, force every tenant to
    re-evaluate acceptance: drop the cached master version + all per-tenant
    gate states so the next request re-prompts. Best-effort."""
    try:
        from services.tenant_agreements.config import is_agreement_slug

        if not is_agreement_slug(slug):
            return
        from services.tenant_agreement_service import clear_all_states
        from services.tenant_agreements.master import invalidate_master_version

        invalidate_master_version(slug or "")
        clear_all_states()
    except Exception:
        logger.warning("agreement publish cache invalidation failed", exc_info=True)


@write_handler("legal_document.archive", invalidates=_LEGAL_INVALIDATIONS)
async def _legal_archive(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    actor_id, actor_role, request_id = _pop_actor(data)
    doc = await archive_legal_document(resource_id)
    await _audit(
        actor_id=actor_id,
        actor_role=actor_role,
        action="legal_document.archived",
        resource_id=resource_id,
        request_id=request_id,
    )
    _enqueue_legal_list_refresh()
    return {"id": doc.id, "slug": doc.slug, "status": doc.status.value}


# ---------------------------------------------------------------------------
# Precompute loaders (GLOBAL scope)
# ---------------------------------------------------------------------------


@register_precompute("legal_documents.list_admin", scope=PrecomputeScope.GLOBAL)
async def _precompute_legal_admin(_scope: str) -> List[dict[str, Any]]:
    rows = await retrieve_legal_documents(
        filters=None, start=0, stop=100, sort_field="date_created", sort_order=-1
    )
    return [r.model_dump(mode="json", by_alias=True) for r in rows]


@register_precompute("legal_documents.list_published", scope=PrecomputeScope.GLOBAL)
async def _precompute_legal_published(_scope: str) -> List[dict[str, Any]]:
    rows = await retrieve_legal_documents(
        filters={"status": LegalDocStatus.published.value},
        start=0,
        stop=100,
        sort_field="date_created",
        sort_order=-1,
    )
    out: List[dict[str, Any]] = []
    for row in rows:
        public = LegalDocumentPublicListRow.model_validate(
            row.model_dump(by_alias=False)
        )
        out.append(public.model_dump(mode="json", by_alias=True))
    return out
