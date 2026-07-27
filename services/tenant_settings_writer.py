"""Queued write handler + precompute loader for tenant settings.

Tenant settings is upsert-style — one record per tenant keyed by
``tenant_id`` — so the writer's ``resource_id`` is always the tenant_id.
The same writer backs both ``/v1/tenants/{id}/settings`` and the
unified ``/v1/tenant-settings`` endpoints.

Plan-gated fields: certain settings groups are only available on paid
tiers. Rather than rejecting the whole request, the writer SILENTLY
DROPS the gated fields from the update payload — the user's prior
stored value is preserved across plan changes, and an upgrade
immediately restores their preference. The matching frontend gate
lives in ``Limitations.deniedFeatures`` (see
``services/me_limitations_service.py:_EXTRA_FEATURE_KEYS_BY_TIER``).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from core.queue.manager import QueueManager
from core.queue.precompute import PrecomputeScope, register_precompute
from core.queue.write_pipeline import write_handler
from schemas.tenant_settings_schema import TenantSettingsUpdate
from services.tenant_settings_service import (
    retrieve_or_create_tenant_settings,
    update_tenant_settings_by_id,
)

logger = logging.getLogger(__name__)


# Map of feature key → tenant_settings field names gated by that key.
# When the resolved plan denies a key (via the per-tier extras list in
# ``me_limitations_service._EXTRA_FEATURE_KEYS_BY_TIER``), every field
# in the corresponding tuple is removed from the inbound update payload
# before the Pydantic model is built — Pydantic's partial-update
# semantics then leave the stored value untouched.
#
# Keep this dict in sync with the frontend keys in
# ``Limitations.deniedFeatures``. The matching schema field names live
# in ``schemas/tenant_settings_schema.py``.
_GATED_FIELDS_BY_FEATURE: dict[str, tuple[str, ...]] = {
    "email_preferences": (
        "send_welcome_email",
        "send_host_notification_on_arrival",
        "send_visitor_badge_email",
    ),
    "visitor_policies": (
        "require_id_scan",
        "require_consent_before_check_in",
        "allow_self_registration",
        "auto_checkout_after_hours",
        "visitor_badge_expiry",
        "visitor_badge_expiry_hours",
        "self_registration_fields",
    ),
    "geofencing": (
        "geofencing_enabled",
        "geofencing_radius_meters",
        "geofencing_reference_lat",
        "geofencing_reference_lng",
    ),
}


async def _strip_plan_gated_fields(tenant_id: str, data: dict[str, Any]) -> None:
    """Drop every field whose feature key is denied by the tenant's plan.

    Mutates ``data`` in place. Resolves the plan via the standard plan
    cache; on resolution failure leaves ``data`` untouched (defaulting
    to honouring the update is safer than silently swallowing changes
    for a tenant whose plan we couldn't look up).
    """
    if not tenant_id or not data:
        return
    try:
        from services.me_limitations_service import _EXTRA_FEATURE_KEYS_BY_TIER
        from services.plan_cache_service import resolve_tenant_plan

        resolved = await resolve_tenant_plan(tenant_id)
        if not resolved:
            return
        tier = str(resolved.get("tier") or "").lower()
        denied_keys = _EXTRA_FEATURE_KEYS_BY_TIER.get(tier, ())
        for key in denied_keys:
            for field in _GATED_FIELDS_BY_FEATURE.get(key, ()):
                data.pop(field, None)
    except Exception:
        logger.debug(
            "tenant_settings_writer: plan-gate strip failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


def _enqueue_refresh(tenant_id: str) -> None:
    if not tenant_id:
        return
    try:
        QueueManager.get_instance().enqueue(
            task_key="precompute.tenant_resource",
            payload={"tenant_id": tenant_id, "resource": "tenant.settings"},
        )
    except Exception:
        logger.warning(
            "tenant_settings_writer: refresh enqueue failed tenant=%s",
            tenant_id,
            exc_info=True,
        )


@write_handler("tenant_settings.update", invalidates=["tenant.settings"])
async def _tenant_settings_update(
    resource_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    tenant_id = data.pop("tenant_id", resource_id) or resource_id
    actor_id = data.pop("_actor_id", "") or ""
    actor_role = data.pop("_actor_role", "") or ""
    await _strip_plan_gated_fields(tenant_id, data)
    upd = TenantSettingsUpdate(**data)
    result = await update_tenant_settings_by_id(
        tenant_id=tenant_id,
        data=upd,
        actor_id=actor_id,
        actor_role=actor_role,
    )
    _enqueue_refresh(tenant_id)
    return {"id": result.id, "tenant_id": result.tenant_id}


@register_precompute("tenant.settings", scope=PrecomputeScope.TENANT)
async def _precompute_tenant_settings(tenant_id: str) -> Optional[dict[str, Any]]:
    if not tenant_id:
        return None
    result = await retrieve_or_create_tenant_settings(tenant_id)
    return result.model_dump(mode="json", by_alias=True)
