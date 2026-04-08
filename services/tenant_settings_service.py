from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException

from repositories.tenant_settings_repo import (
    create_tenant_settings,
    get_tenant_settings,
    update_tenant_settings,
)
from schemas.tenant_settings_schema import (
    TenantSettingsCreate,
    TenantSettingsUpdate,
    TenantSettingsOut,
)
from services.audit_service import record_audit_event


async def retrieve_or_create_tenant_settings(tenant_id: str) -> TenantSettingsOut:
    """Get tenant settings, auto-creating defaults on first access (upsert pattern)."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")

    existing = await get_tenant_settings({"tenant_id": tenant_id})
    if existing:
        return existing

    # First access — create default settings
    defaults = TenantSettingsCreate(tenant_id=tenant_id)
    return await create_tenant_settings(defaults)


async def update_tenant_settings_by_id(
    tenant_id: str,
    data: TenantSettingsUpdate,
    actor_id: str,
    actor_role: str,
) -> TenantSettingsOut:
    """Update tenant settings, creating defaults first if needed."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")

    # Ensure settings record exists
    await retrieve_or_create_tenant_settings(tenant_id)

    result = await update_tenant_settings(
        {"tenant_id": tenant_id},
        data,
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to update tenant settings")

    # Record audit event (fire-and-forget)
    try:
        changed_fields = data.model_dump(exclude_none=True)
        changed_fields.pop("last_updated", None)
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            action="tenant_settings.updated",
            resource_type="tenant_settings",
            resource_id=tenant_id,
            tenant_id=tenant_id,
            details={"changed_fields": list(changed_fields.keys())},
        )
    except Exception:
        pass

    return result
