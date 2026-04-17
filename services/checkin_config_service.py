from __future__ import annotations

from core.errors import resource_not_found
from repositories.checkin_config_repo import (
    create_checkin_config,
    delete_checkin_config,
    get_checkin_config,
    get_checkin_configs,
    update_checkin_config,
)
from repositories.tenant_repo import get_tenant
from schemas.checkin_config_schema import (
    CheckinConfigCreate,
    CheckinConfigOut,
    CheckinConfigUpdate,
    PublicCheckinConfigOut,
)


async def create_config(payload: CheckinConfigCreate) -> CheckinConfigOut:
    """Create a new check-in configuration."""
    tenant = await get_tenant({"_id": payload.tenant_id})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=payload.tenant_id)
    return await create_checkin_config(payload)


async def resolve_public_config(checkin_config_id: str) -> PublicCheckinConfigOut:
    """Resolve a public check-in config with tenant info and logo.

    Returns a sanitized DTO suitable for public/unauthenticated access.
    """
    config = await get_checkin_config({"_id": checkin_config_id, "active": True})
    if not config:
        raise resource_not_found(
            resource="CheckinConfig", resource_id=checkin_config_id
        )

    tenant = await get_tenant({"_id": config.tenant_id})
    if not tenant:
        raise resource_not_found(
            resource="Tenant", resource_id=config.tenant_id
        )

    # Fetch logo URL if branding is configured
    logo_url = None
    try:
        from repositories.branding_repo import get_branding

        branding = await get_branding({"tenant_id": config.tenant_id})
        if branding and branding.logo_object_key:
            from core.storage import DocumentStorageManager

            manager = DocumentStorageManager.get_instance()
            logo_url = manager.provider.download_url(
                object_key=branding.logo_object_key
            )
    except Exception:
        pass  # Gracefully skip branding errors

    return PublicCheckinConfigOut(
        checkin_config_id=config.id or "",
        tenant_id=config.tenant_id,
        tenant_name=tenant.company_name or "",
        logo_url=logo_url,
        id_upload_enabled=config.id_upload_enabled,
        allow_returning_visitor_lookup=config.allow_returning_visitor_lookup,
        required_fields=config.required_fields,
    )


async def update_config(
    config_id: str, data: CheckinConfigUpdate
) -> CheckinConfigOut:
    """Update a check-in configuration."""
    return await update_checkin_config(config_id, data)


async def list_configs_for_tenant(
    tenant_id: str, skip: int = 0, limit: int = 20
) -> tuple[list[CheckinConfigOut], int]:
    """List check-in configurations for a tenant."""
    configs = await get_checkin_configs(
        {"tenant_id": tenant_id}, skip=skip, limit=limit
    )
    from repositories.checkin_config_repo import (
        COLLECTION,
    )
    from core.database import db

    total = await db[COLLECTION].count_documents({"tenant_id": tenant_id})
    return configs, total


async def delete_config(config_id: str) -> None:
    """Delete a check-in configuration."""
    await delete_checkin_config(config_id)
