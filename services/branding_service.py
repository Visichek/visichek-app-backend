from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException
from typing import Optional

from repositories.branding_repo import (
    create_branding,
    get_branding,
    update_branding,
    delete_branding,
)
from schemas.branding_schema import (
    BrandingCreate,
    BrandingUpdate,
    BrandingOut,
    BrandingPublicOut,
)
from services.audit_service import record_audit_event


async def _resolve_logo_urls(branding: BrandingOut) -> BrandingOut:
    """Populate presigned URLs for logo and favicon if object keys exist."""
    try:
        from core.storage.manager import DocumentStorageManager

        manager = DocumentStorageManager.get_instance()
        provider = manager.provider

        if branding.logo_object_key:
            branding.logo_url = provider.download_url(
                object_key=branding.logo_object_key
            )
        if branding.favicon_object_key:
            branding.favicon_url = provider.download_url(
                object_key=branding.favicon_object_key
            )
    except Exception:
        # If storage is not configured, return branding without URLs
        pass

    return branding


async def upsert_branding(tenant_id: str, branding_data: BrandingUpdate) -> BrandingOut:
    """Create or update branding for a tenant.

    Each tenant has exactly one branding record.  If one already exists
    it is updated (partial); otherwise a new one is created.
    """
    existing = await get_branding({"tenant_id": tenant_id})

    if existing:
        result = await update_branding(
            {"tenant_id": tenant_id},
            branding_data,
        )
        if not result:
            raise HTTPException(status_code=500, detail="Failed to update branding")

        # Record audit event (fire-and-forget)
        try:
            await record_audit_event(
                actor_id="system",
                actor_role="admin",
                action="branding.updated",
                resource_type="branding",
                resource_id=tenant_id,
                tenant_id=tenant_id,
                details={
                    "company_display_name": branding_data.company_display_name,
                    "primary_color": branding_data.primary_color,
                    "secondary_color": branding_data.secondary_color,
                    "accent_color": branding_data.accent_color,
                    "logo_updated": branding_data.logo_object_key is not None,
                    "favicon_updated": branding_data.favicon_object_key is not None,
                },
            )
        except Exception:
            pass

        return await _resolve_logo_urls(result)
    else:
        # First-time creation — build a full BrandingCreate
        create_fields = branding_data.model_dump(exclude_none=True)
        create_fields["tenant_id"] = tenant_id
        create_data = BrandingCreate(**create_fields)
        new_branding = await create_branding(create_data)

        # Record audit event (fire-and-forget)
        try:
            await record_audit_event(
                actor_id="system",
                actor_role="admin",
                action="branding.created",
                resource_type="branding",
                resource_id=tenant_id,
                tenant_id=tenant_id,
                details={
                    "company_display_name": branding_data.company_display_name,
                    "primary_color": branding_data.primary_color,
                    "secondary_color": branding_data.secondary_color,
                    "accent_color": branding_data.accent_color,
                },
            )
        except Exception:
            pass

        return await _resolve_logo_urls(new_branding)


async def retrieve_public_branding_by_tenant(
    tenant_id: str,
) -> Optional[BrandingPublicOut]:
    """Get public-facing branding for a tenant (login screen, unauthenticated).

    Returns only the fields needed for UI rendering — no internal object keys,
    no badge-specific colors, no timestamps.
    """
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")

    branding = await get_branding({"tenant_id": tenant_id})
    if not branding:
        return None

    # Resolve presigned URLs for logo and favicon
    branding = await _resolve_logo_urls(branding)

    return BrandingPublicOut(
        tenant_id=branding.tenant_id,
        company_display_name=branding.company_display_name,
        primary_color=branding.primary_color,
        secondary_color=branding.secondary_color,
        accent_color=branding.accent_color,
        logo_url=branding.logo_url,
        favicon_url=branding.favicon_url,
    )


async def retrieve_branding_by_tenant(tenant_id: str) -> Optional[BrandingOut]:
    """Get the branding config for a tenant. Returns None if not set."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")

    branding = await get_branding({"tenant_id": tenant_id})
    if not branding:
        return None

    return await _resolve_logo_urls(branding)


async def remove_branding(tenant_id: str) -> None:
    """Delete branding config for a tenant (reset to defaults)."""
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")

    result = await delete_branding({"tenant_id": tenant_id})
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=404, detail="Branding not found for this tenant"
        )

    # Record audit event (fire-and-forget)
    try:
        await record_audit_event(
            actor_id="system",
            actor_role="admin",
            action="branding.deleted",
            resource_type="branding",
            resource_id=tenant_id,
            tenant_id=tenant_id,
            details={},
        )
    except Exception:
        pass
