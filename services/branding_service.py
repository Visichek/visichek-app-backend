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
from schemas.branding_schema import BrandingCreate, BrandingUpdate, BrandingOut


async def _resolve_logo_urls(branding: BrandingOut) -> BrandingOut:
    """Populate presigned URLs for logo and favicon if object keys exist."""
    try:
        from core.storage.manager import DocumentStorageManager

        manager = DocumentStorageManager.get_instance()
        provider = manager.provider

        if branding.logo_object_key:
            branding.logo_url = await provider.create_download_url(branding.logo_object_key)
        if branding.favicon_object_key:
            branding.favicon_url = await provider.create_download_url(branding.favicon_object_key)
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
        return await _resolve_logo_urls(result)
    else:
        # First-time creation — build a full BrandingCreate
        create_fields = branding_data.model_dump(exclude_none=True)
        create_fields["tenant_id"] = tenant_id
        create_data = BrandingCreate(**create_fields)
        new_branding = await create_branding(create_data)
        return await _resolve_logo_urls(new_branding)


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
        raise HTTPException(status_code=404, detail="Branding not found for this tenant")
