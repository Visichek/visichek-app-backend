"""Queued write handlers + precompute loaders for tenant branding.

Branding is upsert-style (one record per tenant) so the ``resource_id``
passed to the writer is always the tenant_id. The writer delegates to
the existing service layer which handles audit events and logo URL
resolution.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.branding_schema import BrandingUpdate
from services.branding_service import (
    remove_branding,
    retrieve_branding_by_tenant,
    retrieve_public_branding_by_tenant,
    upsert_branding,
)

logger = logging.getLogger(__name__)


def _enqueue_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    qm = QueueManager.get_instance()
    for resource in ("branding.full", "branding.public"):
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={"tenant_id": tenant_id, "resource": resource},
            )
        except Exception:
            logger.warning(
                "branding_writer: refresh enqueue failed tenant=%s resource=%s",
                tenant_id,
                resource,
                exc_info=True,
            )


@write_handler("branding.upsert")
async def _branding_upsert(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    # resource_id is the tenant_id for branding (one-per-tenant upsert).
    tenant_id = data.pop("tenant_id", resource_id) or resource_id
    upd = BrandingUpdate(**data)
    result = await upsert_branding(tenant_id=tenant_id, branding_data=upd)
    _enqueue_refresh(tenant_id)
    return {"id": result.id, "tenant_id": result.tenant_id}


@write_handler("branding.delete")
async def _branding_delete(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = data.get("tenant_id", resource_id) or resource_id
    await remove_branding(tenant_id=tenant_id)
    _enqueue_refresh(tenant_id)
    return {"tenant_id": tenant_id, "deleted": True}


@register_precompute("branding.full", scope=PrecomputeScope.TENANT)
async def _precompute_branding_full(tenant_id: str) -> Optional[dict[str, Any]]:
    branding = await retrieve_branding_by_tenant(tenant_id)
    if not branding:
        return None
    return branding.model_dump(mode="json", by_alias=True)


@register_precompute("branding.public", scope=PrecomputeScope.TENANT)
async def _precompute_branding_public(tenant_id: str) -> Optional[dict[str, Any]]:
    branding = await retrieve_public_branding_by_tenant(tenant_id)
    if not branding:
        return None
    return branding.model_dump(mode="json", by_alias=True)
