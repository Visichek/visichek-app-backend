"""Queued write handlers + precompute loaders for privacy notices."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.privacy_notice_schema import PrivacyNoticeCreate, PrivacyNoticeUpdate
from services.privacy_notice_service import (
    add_privacy_notice,
    retrieve_active_notice,
    retrieve_privacy_notices,
    update_notice_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    qm = QueueManager.get_instance()
    for resource in ("privacy_notices.list", "privacy_notice.active"):
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource",
                payload={"tenant_id": tenant_id, "resource": resource},
            )
        except Exception:
            logger.warning(
                "privacy_notice_writer: refresh enqueue failed tenant=%s resource=%s",
                tenant_id,
                resource,
                exc_info=True,
            )


@write_handler("privacy_notice.create")
async def _privacy_notice_create(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    notice = PrivacyNoticeCreate(**data)
    result = await add_privacy_notice(notice_data=notice, preassigned_id=resource_id)
    _enqueue_refresh(result.tenant_id)
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "version_code": result.version_code,
    }


@write_handler("privacy_notice.update")
async def _privacy_notice_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = PrivacyNoticeUpdate(**data)
    result = await update_notice_by_id(
        notice_id=resource_id, tenant_id=tenant_id, notice_data=upd
    )
    _enqueue_refresh(tenant_id)
    return {"id": result.id, "version_code": result.version_code}


@register_precompute("privacy_notices.list", scope=PrecomputeScope.TENANT)
async def _precompute_privacy_notices_list(tenant_id: str) -> List[dict[str, Any]]:
    notices = await retrieve_privacy_notices(tenant_id=tenant_id, start=0, stop=100)
    return [n.model_dump(mode="json", by_alias=True) for n in notices]


@register_precompute("privacy_notice.active", scope=PrecomputeScope.TENANT)
async def _precompute_privacy_notice_active(
    tenant_id: str,
) -> Optional[dict[str, Any]]:
    try:
        notice = await retrieve_active_notice(tenant_id=tenant_id)
    except Exception:
        return None
    return notice.model_dump(mode="json", by_alias=True)
