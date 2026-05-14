"""Precompute loaders for tenant forms.

Tenant-form mutations stay synchronous because the autosave UX needs
the updated draft row in the response (and publish needs to surface
validation errors inline). The writer module is kept so the precompute
fanout knows which resources to refresh — the actual mutations live in
``services.tenant_form_service``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from schemas.imports import FormTargetType
from services.tenant_form_service import (
    list_forms_for_tenant,
    retrieve_active_by_target,
    retrieve_public_active_by_target,
)

logger = logging.getLogger(__name__)


# Resource keys the rest of the codebase invalidates after a form mutation.
RESOURCE_LIST = "tenant_forms.list"
RESOURCE_ACTIVE_PREFIX = "tenant_forms.active"
RESOURCE_PUBLIC_PREFIX = "tenant_forms.public"


def resource_active(target_type: str) -> str:
    return f"{RESOURCE_ACTIVE_PREFIX}.{target_type}"


def resource_public(target_type: str) -> str:
    return f"{RESOURCE_PUBLIC_PREFIX}.{target_type}"


def enqueue_refresh(tenant_id: str, target_type: str | None = None) -> None:
    """Eagerly re-warm the form precompute entries for a tenant.

    Called from the service layer after every mutation so the next read
    sees post-commit state. Failures here are non-fatal — the fanout
    cycle will refresh everything within 60s regardless.
    """
    if not tenant_id:
        return
    try:
        qm = QueueManager.get_instance()
    except RuntimeError:
        return

    targets = (
        [target_type]
        if target_type
        else [t.value for t in FormTargetType]
    )

    payloads: list[dict[str, Any]] = [
        {"tenant_id": tenant_id, "resource": RESOURCE_LIST}
    ]
    for target in targets:
        payloads.append(
            {"tenant_id": tenant_id, "resource": resource_active(target)}
        )
        payloads.append(
            {"tenant_id": tenant_id, "resource": resource_public(target)}
        )

    for payload in payloads:
        try:
            qm.enqueue(
                task_key="precompute.tenant_resource", payload=payload
            )
        except Exception:
            logger.warning(
                "tenant_form_writer: refresh enqueue failed payload=%s",
                payload,
                exc_info=True,
            )


# ─── Precompute loaders ───────────────────────────────────────────


@register_precompute(RESOURCE_LIST, scope=PrecomputeScope.TENANT)
async def _precompute_form_list(tenant_id: str) -> List[Dict[str, Any]]:
    forms = await list_forms_for_tenant(tenant_id, start=0, stop=200)
    return [f.model_dump(mode="json", by_alias=True) for f in forms]


async def _precompute_active(tenant_id: str, target_type: str) -> Any:
    form = await retrieve_active_by_target(
        tenant_id=tenant_id, target_type=target_type
    )
    if form is None:
        return None
    return form.model_dump(mode="json", by_alias=True)


async def _precompute_public(tenant_id: str, target_type: str) -> Any:
    form = await retrieve_public_active_by_target(
        tenant_id=tenant_id, target_type=target_type
    )
    if form is None:
        return None
    return form.model_dump(mode="json", by_alias=True)


@register_precompute(
    resource_active(FormTargetType.APPOINTMENT.value),
    scope=PrecomputeScope.TENANT,
)
async def _precompute_active_appointment(tenant_id: str) -> Any:
    return await _precompute_active(tenant_id, FormTargetType.APPOINTMENT.value)


@register_precompute(
    resource_active(FormTargetType.CHECKIN.value),
    scope=PrecomputeScope.TENANT,
)
async def _precompute_active_checkin(tenant_id: str) -> Any:
    return await _precompute_active(tenant_id, FormTargetType.CHECKIN.value)


@register_precompute(
    resource_active(FormTargetType.VISIT_SESSION.value),
    scope=PrecomputeScope.TENANT,
)
async def _precompute_active_visit_session(tenant_id: str) -> Any:
    return await _precompute_active(
        tenant_id, FormTargetType.VISIT_SESSION.value
    )


@register_precompute(
    resource_public(FormTargetType.APPOINTMENT.value),
    scope=PrecomputeScope.TENANT,
)
async def _precompute_public_appointment(tenant_id: str) -> Any:
    return await _precompute_public(tenant_id, FormTargetType.APPOINTMENT.value)


@register_precompute(
    resource_public(FormTargetType.CHECKIN.value),
    scope=PrecomputeScope.TENANT,
)
async def _precompute_public_checkin(tenant_id: str) -> Any:
    return await _precompute_public(tenant_id, FormTargetType.CHECKIN.value)


@register_precompute(
    resource_public(FormTargetType.VISIT_SESSION.value),
    scope=PrecomputeScope.TENANT,
)
async def _precompute_public_visit_session(tenant_id: str) -> Any:
    return await _precompute_public(
        tenant_id, FormTargetType.VISIT_SESSION.value
    )
