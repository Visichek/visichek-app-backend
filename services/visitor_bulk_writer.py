"""Bulk writers for visitor session queue actions.

Four bulk operations are exposed to the receptionist UI:

* ``visitor.bulk_host_approve``  — host approves multiple pending sessions
* ``visitor.bulk_deny``          — deny multiple pending sessions with one reason
* ``visitor.bulk_confirm``       — confirm + issue signed badge tokens
* ``visitor.bulk_check_out``     — close out multiple checked-in sessions

Per-id outputs land in ``queue_job_log.result`` as a
``{succeeded[], failed[]}`` shape via ``run_bulk_handlers``. The
writers never carry PDF bytes — badge PDFs are rendered by the
frontend from the signed token + session snapshots returned on the
follow-up GET.
"""

from __future__ import annotations

import logging
from typing import Any

from core.bulk import run_bulk_handlers
from core.queue.write_pipeline import write_handler
from services.visit_session_service import (
    approve_visitor_by_host,
    check_out_visitor,
    confirm_check_in,
    deny_visitor,
)

logger = logging.getLogger(__name__)


_VISITOR_INVALIDATES = [
    "dashboard.visitors_active",
    "dashboard.visitors_page1",
    "dashboard.stats",
    # visitor_profiles.list mutates on check-in (total_visits++,
    # last_visit_time) — sync path goes via invalidate_tenant_dashboard_cache,
    # keep the queued path symmetric so list GETs don't lag bulk ops.
    "visitor_profiles.list",
]


@write_handler("visitor.bulk_host_approve", invalidates=_VISITOR_INVALIDATES)
async def _visitor_bulk_host_approve(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = str(extras.get("tenant_scope") or "")
    host_id = str(extras.get("host_id") or "")

    async def _handle(session_id: str) -> dict[str, Any]:
        result = await approve_visitor_by_host(
            session_id=session_id, tenant_id=tenant_scope, host_id=host_id
        )
        return {"id": result.id if result else session_id, "approved": True}

    return await run_bulk_handlers(ids, _handle, atomic=atomic)


@write_handler("visitor.bulk_deny", invalidates=_VISITOR_INVALIDATES)
async def _visitor_bulk_deny(resource_id: str, data: dict[str, Any]) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = str(extras.get("tenant_scope") or "")
    reason = str(extras.get("reason") or "")[:500]

    denied_by = str(extras.get("actor_id") or extras.get("host_id") or "")

    async def _handle(session_id: str) -> dict[str, Any]:
        result = await deny_visitor(
            session_id=session_id,
            reason=reason or "bulk_deny",
            denied_by=denied_by or "bulk",
            tenant_id=tenant_scope,
        )
        return {"id": result.id if result else session_id, "status": "denied"}

    return await run_bulk_handlers(ids, _handle, atomic=atomic)


@write_handler("visitor.bulk_confirm", invalidates=_VISITOR_INVALIDATES)
async def _visitor_bulk_confirm(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = str(extras.get("tenant_scope") or "")
    badge_format = str(extras.get("badge_format") or "A7")
    receptionist_id = str(extras.get("actor_id") or "bulk")

    async def _handle(session_id: str) -> dict[str, Any]:
        result = await confirm_check_in(
            session_id=session_id,
            receptionist_id=receptionist_id,
            tenant_id=tenant_scope,
            badge_format=badge_format,
        )
        token = (
            result.get("badge_qr_token") if isinstance(result, dict) else None
        )
        out: dict[str, Any] = {"id": session_id}
        if token:
            out["badgeQrToken"] = token
        return out

    return await run_bulk_handlers(ids, _handle, atomic=atomic)


@write_handler("visitor.bulk_check_out", invalidates=_VISITOR_INVALIDATES)
async def _visitor_bulk_check_out(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    from schemas.imports import CheckOutMethod
    from schemas.visit_session_schema import CheckOutRequest

    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    tenant_scope = str(extras.get("tenant_scope") or "")
    method = str(extras.get("method") or "manual")
    method_enum = (
        CheckOutMethod.QR_SCAN if method == "qr_scan" else CheckOutMethod.MANUAL
    )

    async def _handle(session_id: str) -> dict[str, Any]:
        body = CheckOutRequest(
            session_id=session_id,
            check_out_method=method_enum,
        )
        result = await check_out_visitor(request=body, tenant_id=tenant_scope)
        return {
            "id": session_id,
            "status": "checked_out",
            "check_out_time": getattr(result, "check_out_time", None),
        }

    return await run_bulk_handlers(ids, _handle, atomic=atomic)
