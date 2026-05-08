from __future__ import annotations

from typing import Optional

from bson import ObjectId

from core.errors import resource_not_found
from repositories.checkin_config_repo import (
    create_checkin_config,
    delete_checkin_config,
    get_active_checkin_config_for_tenant,
    get_checkin_config,
    get_checkin_configs,
    update_checkin_config,
)
from repositories.tenant_repo import get_tenant
from schemas.checkin_config_schema import (
    CheckinConfigCreate,
    CheckinConfigOut,
    CheckinConfigUpdate,
    CheckinFieldDef,
    PublicCheckinConfigOut,
)
from schemas.imports import CheckinFieldCategory, TenantEnumKind


# System-default required fields. Phone + Full Name are *system-mandated*
# (the visitor profile uniqueness key + badge label); everything else is
# tenant-configurable on the active CheckinConfig. Email is shown by
# default but optional — tenants who care about email capture can flip
# ``required=True`` on their copy of the config.
DEFAULT_REQUIRED_FIELDS: list[CheckinFieldDef] = [
    CheckinFieldDef(
        key="full_name",
        label="Full Name",
        type="text",
        required=True,
        category=CheckinFieldCategory.BIO,
    ),
    CheckinFieldDef(
        key="phone",
        label="Phone Number",
        type="tel",
        required=True,
        category=CheckinFieldCategory.BIO,
    ),
    CheckinFieldDef(
        key="email",
        label="Email (optional)",
        type="email",
        required=False,
        category=CheckinFieldCategory.BIO,
    ),
    CheckinFieldDef(
        key="company",
        label="Company",
        type="text",
        required=False,
        category=CheckinFieldCategory.BIO,
    ),
    CheckinFieldDef(
        key="purpose",
        label="Purpose of Visit",
        type="select",
        required=True,
        category=CheckinFieldCategory.TENANT_SPECIFIC,
        enum_kind=TenantEnumKind.PURPOSE_OF_VISIT,
    ),
]


async def _resolve_tenant_logo_url(tenant_id: str) -> Optional[str]:
    try:
        from repositories.branding_repo import get_branding

        branding = await get_branding({"tenant_id": tenant_id})
        if branding and branding.logo_object_key:
            from core.storage import DocumentStorageManager

            manager = DocumentStorageManager.get_instance()
            return manager.provider.download_url(object_key=branding.logo_object_key)
    except Exception:
        pass
    return None


async def create_config(
    payload: CheckinConfigCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> CheckinConfigOut:
    """Create a new check-in configuration."""
    if not ObjectId.is_valid(payload.tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=payload.tenant_id)
    tenant = await get_tenant({"_id": ObjectId(payload.tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=payload.tenant_id)
    return await create_checkin_config(payload, preassigned_id=preassigned_id)


async def resolve_public_config(checkin_config_id: str) -> PublicCheckinConfigOut:
    """Resolve a public check-in config with tenant info and logo.

    Returns a sanitized DTO suitable for public/unauthenticated access.
    """
    config = await get_checkin_config({"_id": checkin_config_id, "active": True})
    if not config:
        raise resource_not_found(
            resource="CheckinConfig", resource_id=checkin_config_id
        )

    if not ObjectId.is_valid(config.tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=config.tenant_id)
    tenant = await get_tenant({"_id": ObjectId(config.tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=config.tenant_id)

    logo_url = await _resolve_tenant_logo_url(config.tenant_id)

    return PublicCheckinConfigOut(
        checkin_config_id=config.id or "",
        tenant_id=config.tenant_id,
        tenant_name=tenant.company_name or "",
        logo_url=logo_url,
        id_upload_enabled=config.id_upload_enabled,
        allow_returning_visitor_lookup=config.allow_returning_visitor_lookup,
        required_fields=config.required_fields,
    )


async def resolve_public_config_by_tenant(tenant_id: str) -> PublicCheckinConfigOut:
    """Resolve the active check-in config for a tenant.

    Falls back to a default config when the tenant hasn't configured one yet
    so the public kiosk/registration UI can still render a usable form.
    """
    if not ObjectId.is_valid(tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)

    logo_url = await _resolve_tenant_logo_url(tenant_id)
    config = await get_active_checkin_config_for_tenant(tenant_id)

    if config is None:
        return PublicCheckinConfigOut(
            checkin_config_id="",
            tenant_id=tenant_id,
            tenant_name=tenant.company_name or "",
            logo_url=logo_url,
            id_upload_enabled=True,
            allow_returning_visitor_lookup=True,
            required_fields=list(DEFAULT_REQUIRED_FIELDS),
        )

    return PublicCheckinConfigOut(
        checkin_config_id=config.id or "",
        tenant_id=tenant_id,
        tenant_name=tenant.company_name or "",
        logo_url=logo_url,
        id_upload_enabled=config.id_upload_enabled,
        allow_returning_visitor_lookup=config.allow_returning_visitor_lookup,
        required_fields=config.required_fields,
    )


async def update_config(config_id: str, data: CheckinConfigUpdate) -> CheckinConfigOut:
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
