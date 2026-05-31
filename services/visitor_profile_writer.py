"""Queued write handler + precompute loader for visitor profiles.

Note: creation happens internally via ``get_or_create_visitor_profile``
during check-in flows; only the update path is exposed to clients and
queued here.
"""

from __future__ import annotations

import logging
from typing import Any, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.visitor_profile_schema import VisitorProfileUpdate
from services.audit_service import record_audit_event
from services.visitor_profile_service import (
    restore_profile_erasure,
    retrieve_visitor_profiles_with_summary,
    schedule_profile_erasure,
    update_profile_by_id,
)

logger = logging.getLogger(__name__)


def _enqueue_list_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "visitor_profiles.list"},
        )
    except Exception:
        logger.warning(
            "visitor_profile_writer: refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


def _pop_actor(data: dict[str, Any]) -> tuple[str, str, str | None]:
    """Strip actor metadata threaded into the payload by the route so the
    handler can self-audit (and stamp ``performed_by`` on deletion logs)."""
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or ""
    request_id = data.pop("_request_id", None)
    return actor_id, actor_role, request_id


# Caches invalidated whenever a visitor profile's lifecycle changes — the
# visitor_profile_summary is embedded across active-visit / appointment /
# incident views, so they all go stale on an erase / restore.
_PROFILE_INVALIDATES = [
    "visitor_profiles.list",
    "dashboard.visitors_active",
    "dashboard.visitors_page1",
    "appointments.list",
    "incidents.list",
]


@write_handler(
    "visitor_profile.update",
    invalidates=[
        "visitor_profiles.list",
        # visitor_profile_summary embedded across active visit / appointment / incident views
        "dashboard.visitors_active",
        "dashboard.visitors_page1",
        "appointments.list",
        "incidents.list",
    ],
)
async def _visitor_profile_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", "") or ""
    upd = VisitorProfileUpdate(**data)
    result = await update_profile_by_id(
        profile_id=resource_id, tenant_id=tenant_id, profile_data=upd
    )
    _enqueue_list_refresh(tenant_id)
    return {"id": result.id, "full_name": result.full_name}


@write_handler("visitor_profile.erase", invalidates=_PROFILE_INVALIDATES)
async def _visitor_profile_erase(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """DSR erasure: soft-delete the profile and schedule its permanent purge."""
    actor_id, actor_role, request_id = _pop_actor(data)
    tenant_id = data.get("tenant_id", "") or ""
    reason = data.get("reason")
    result = await schedule_profile_erasure(
        profile_id=resource_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        reason=reason,
    )
    _enqueue_list_refresh(tenant_id)
    if actor_id:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role or "system_user",
            action="visitor_profile.erasure_scheduled",
            resource_type="visitor_profile",
            resource_id=result.id or resource_id,
            tenant_id=tenant_id,
            details={
                "deleted_at": result.deleted_at,
                "scheduled_purge_at": result.scheduled_purge_at,
                "reason": reason or "dsr_erasure_request",
            },
            request_id=request_id,
        )
    return {
        "id": result.id,
        "deleted_at": result.deleted_at,
        "scheduled_purge_at": result.scheduled_purge_at,
    }


@write_handler("visitor_profile.restore", invalidates=_PROFILE_INVALIDATES)
async def _visitor_profile_restore(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Reverse a scheduled erasure (within the grace window)."""
    actor_id, actor_role, request_id = _pop_actor(data)
    tenant_id = data.get("tenant_id", "") or ""
    result = await restore_profile_erasure(profile_id=resource_id, tenant_id=tenant_id)
    _enqueue_list_refresh(tenant_id)
    if actor_id:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role or "system_user",
            action="visitor_profile.restored",
            resource_type="visitor_profile",
            resource_id=result.id or resource_id,
            tenant_id=tenant_id,
            details={},
            request_id=request_id,
        )
    return {"id": result.id, "restored": True}


@register_precompute("visitor_profiles.list", scope=PrecomputeScope.TENANT)
async def _precompute_visitor_profiles_list(tenant_id: str) -> List[dict[str, Any]]:
    profiles = await retrieve_visitor_profiles_with_summary(
        tenant_id=tenant_id, start=0, stop=100
    )
    return [p.model_dump(mode="json", by_alias=True) for p in profiles]
