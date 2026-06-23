from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException

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
    from services.storage_url_service import try_resolve_download_url

    branding.logo_url = try_resolve_download_url(branding.logo_object_key)
    branding.favicon_url = try_resolve_download_url(branding.favicon_object_key)
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


async def _is_free_plan_tenant(tenant_id: str) -> bool:
    """Best-effort check for whether ``tenant_id`` is on the Free plan.

    Defaults to ``False`` (no watermark) on lookup failure — keeping the
    public kiosk flow renderable matters more than guaranteeing the
    watermark on every request.
    """
    try:
        from services.plan_cache_service import resolve_tenant_plan

        resolved = await resolve_tenant_plan(tenant_id)
        if resolved and resolved.get("tier"):
            return str(resolved["tier"]).lower() == "free"
    except Exception:
        pass
    return False


async def retrieve_public_branding_by_tenant(
    tenant_id: str,
) -> BrandingPublicOut:
    """Get public-facing branding for a tenant (login screen, unauthenticated).

    Returns only the fields needed for UI rendering — no internal object keys,
    no badge-specific colors, no timestamps. If the tenant has not configured
    branding, returns a default BrandingPublicOut with just the tenant_id so
    the frontend always has a stable shape to render against.

    Free-plan tenants get ``powered_by_visichek=True`` so the frontend
    renders the "Powered by Visichek" watermark on the public visitor flow.
    """
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")

    powered_by = await _is_free_plan_tenant(tenant_id)

    branding = await get_branding({"tenant_id": tenant_id})
    if not branding:
        return BrandingPublicOut(tenant_id=tenant_id, powered_by_visichek=powered_by)

    branding = await _resolve_logo_urls(branding)

    return BrandingPublicOut(
        tenant_id=branding.tenant_id,
        company_display_name=branding.company_display_name,
        primary_color=branding.primary_color,
        secondary_color=branding.secondary_color,
        accent_color=branding.accent_color,
        logo_url=branding.logo_url,
        favicon_url=branding.favicon_url,
        powered_by_visichek=powered_by,
    )


async def retrieve_branding_by_tenant(tenant_id: str) -> BrandingOut:
    """Get the branding config for a tenant.

    Returns a default BrandingOut with just the tenant_id when no record
    exists, so callers always get a renderable payload. Schema-level
    defaults (e.g. badge_logo_position) are applied by Pydantic.
    """
    if not ObjectId.is_valid(tenant_id):
        raise HTTPException(status_code=400, detail="Invalid tenant ID format")

    branding = await get_branding({"tenant_id": tenant_id})
    if not branding:
        return BrandingOut(tenant_id=tenant_id)

    return await _resolve_logo_urls(branding)


async def get_email_branding_context(tenant_id: str | None) -> dict:
    """Best-effort tenant branding fields for CUSTOMER-FACING email contexts.

    Returns ``{"tenant_logo_url": <presigned url>, "tenant_brand_color": <hex>}``
    with only the keys that resolve. **Never raises** — transactional email is
    fire-and-forget, so a branding/S3 hiccup must never block a send.

    The email shell's ``build_email_brand`` (email_templates/_shell.py) reads
    exactly these keys to render the tenant logo + accent. Internal auth/admin
    emails do NOT call this and stay VisiChek-branded.
    """
    out: dict = {}
    if not tenant_id:
        return out
    try:
        branding = await retrieve_branding_by_tenant(tenant_id)
    except Exception:
        return out
    logo_url = getattr(branding, "logo_url", None)
    color = getattr(branding, "primary_color", None)
    if logo_url:
        out["tenant_logo_url"] = logo_url
    if isinstance(color, str) and color.startswith("#"):
        out["tenant_brand_color"] = color
    return out


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
