"""Queued write handlers for support cases.

Writer keys:
  * ``support_case.create``       — tenant opens a case
  * ``support_case.message.add``  — tenant or admin appends a message
  * ``support_case.transition``   — any legal state-machine step
  * ``support_case.assign``       — admin claims / re-assigns a case
  * ``support_case.attachment.add`` — register a completed S3 upload

All handlers delegate to ``services.support_case_service`` and return a
JSON-serialisable dict that lands in ``queue_job_log.result``.
"""

from __future__ import annotations

import logging
from typing import Any

from core.bulk import run_bulk_handlers
from core.queue.write_pipeline import write_handler
from schemas.support_case_schema import SupportCaseStatus
from services.support_case_service import (
    add_support_case,
    add_support_case_message,
    assign_support_case,
    transition_support_case,
)

logger = logging.getLogger(__name__)


@write_handler(
    "support_case.create",
    invalidates=["support_cases.list", "support_cases.admin_list"],
)
async def _support_case_create(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    case = await add_support_case(
        subject=data["subject"],
        description=data["description"],
        category=data.get("category", "other"),
        priority=data.get("priority", "medium"),
        tenant_id=data["tenant_id"],
        opened_by=data["opened_by"],
        opened_by_role=data.get("opened_by_role", "super_admin"),
        preassigned_id=resource_id,
    )
    return {
        "id": case.id,
        "tenant_id": case.tenant_id,
        "subject": case.subject,
        "status": case.status.value if case.status is not None else None,
    }


@write_handler(
    "support_case.message.add",
    invalidates=["support_cases.list", "support_cases.admin_list"],
)
async def _support_case_message_add(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    case_id = data.get("case_id") or ""
    msg = await add_support_case_message(
        case_id=case_id,
        author_id=data["author_id"],
        author_role=data["author_role"],
        body=data["body"],
        attachments=data.get("attachments") or [],
        internal_note=bool(data.get("internal_note", False)),
        preassigned_id=resource_id,
    )
    return {
        "id": msg.id,
        "case_id": msg.case_id,
        "internal_note": bool(msg.internal_note),
    }


@write_handler(
    "support_case.transition",
    invalidates=["support_cases.list", "support_cases.admin_list"],
)
async def _support_case_transition(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    case = await transition_support_case(
        case_id=resource_id,
        new_status=data["status"],
        actor_id=data.get("actor_id", ""),
        actor_role=data.get("actor_role", ""),
    )
    return {
        "id": case.id,
        "status": case.status.value if case.status is not None else None,
    }


@write_handler(
    "support_case.assign",
    invalidates=["support_cases.list", "support_cases.admin_list"],
)
async def _support_case_assign(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    case = await assign_support_case(
        case_id=resource_id,
        admin_id=data["admin_id"],
        actor_id=data.get("actor_id", ""),
        actor_role=data.get("actor_role", ""),
    )
    return {
        "id": case.id,
        "assigned_admin_id": case.assigned_admin_id,
    }


@write_handler(
    "support_case.attachment.add",
    invalidates=["support_cases.list", "support_cases.admin_list"],
)
async def _support_case_attachment_add(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Attach an uploaded document to a case as a standalone message.

    Used when the client completes an upload via the presigned URL and wants
    the attachment to appear in the thread without a body. We create a
    placeholder message body referencing the file name so the UI can render it.
    """
    case_id = data.get("case_id") or resource_id
    attachments = data.get("attachments") or []
    body = data.get("body") or "(attachment uploaded)"
    msg = await add_support_case_message(
        case_id=case_id,
        author_id=data["author_id"],
        author_role=data["author_role"],
        body=body,
        attachments=attachments,
        internal_note=bool(data.get("internal_note", False)),
        preassigned_id=resource_id,
    )
    return {
        "id": msg.id,
        "case_id": msg.case_id,
        "attachments": len(msg.attachments),
    }


@write_handler(
    "support_case.bulk_assign",
    invalidates=["support_cases.list", "support_cases.admin_list"],
)
async def _support_case_bulk_assign(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    assignee_id = str(extras.get("assignee_id") or "")
    if not assignee_id:
        return {"succeeded": [], "failed": [], "atomic": atomic}

    async def _handle(case_id: str) -> dict[str, Any]:
        result = await assign_support_case(
            case_id=case_id,
            admin_id=assignee_id,
            actor_id=assignee_id,
            actor_role="admin",
        )
        return {"id": result.id if result else case_id, "assigned_to": assignee_id}

    return await run_bulk_handlers(ids, _handle, atomic=atomic)


@write_handler(
    "support_case.bulk_transition",
    invalidates=["support_cases.list", "support_cases.admin_list"],
)
async def _support_case_bulk_transition(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    ids = list(data.get("ids", []))
    atomic = bool(data.get("atomic", False))
    extras = data.get("extras", {}) or {}
    target_status = extras.get("status")
    if not target_status:
        return {"succeeded": [], "failed": [], "atomic": atomic}
    try:
        status_enum = SupportCaseStatus(target_status)
    except ValueError:
        return {"succeeded": [], "failed": [], "atomic": atomic}

    async def _handle(case_id: str) -> dict[str, Any]:
        result = await transition_support_case(
            case_id=case_id,
            new_status=status_enum,
            actor_id="bulk",
            actor_role="admin",
        )
        return {
            "id": result.id if result else case_id,
            "status": status_enum.value,
        }

    return await run_bulk_handlers(ids, _handle, atomic=atomic)
