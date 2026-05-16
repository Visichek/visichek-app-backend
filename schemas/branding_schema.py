from __future__ import annotations

from schemas.imports import *
from pydantic import Field, field_validator
import time
import re


# --- Hex color validation helper ---

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _validate_hex_color(v: str | None, field_name: str) -> str | None:
    if v is None:
        return v
    if not _HEX_COLOR_RE.match(v):
        raise ValueError(
            f"{field_name} must be a valid hex color (e.g. #FF5500 or #F50)"
        )
    return v.upper()


class BrandingBase(BaseModel):
    """Tenant branding configuration.

    Controls the visual appearance of the tenant's UI, badges, and
    generated documents.  One branding record per tenant.
    """

    tenant_id: str

    # --- Display name (may differ from legal company_name) ---
    company_display_name: Optional[str] = None

    # --- Color palette ---
    primary_color: Optional[str] = Field(
        default=None,
        description="Primary brand color as hex (e.g. #1A73E8). Used for buttons, headers, badges.",
    )
    secondary_color: Optional[str] = Field(
        default=None,
        description="Secondary brand color as hex. Used for accents and secondary elements.",
    )
    accent_color: Optional[str] = Field(
        default=None,
        description="Accent color as hex. Used for highlights and call-to-action elements.",
    )

    # --- Badge-specific colors ---
    badge_header_color: Optional[str] = Field(
        default=None,
        description="Badge header background color as hex. Falls back to primary_color if not set.",
    )
    badge_text_color: Optional[str] = Field(
        default=None,
        description="Badge text color as hex. Falls back to #FFFFFF if not set.",
    )

    # --- Logo / favicon (DocumentStorage object keys) ---
    logo_object_key: Optional[str] = Field(
        default=None,
        description="Object key for the tenant logo in DocumentStorage. Use upload intent → complete flow.",
    )
    favicon_object_key: Optional[str] = Field(
        default=None,
        description="Object key for the favicon in DocumentStorage.",
    )

    # --- Badge layout ---
    badge_logo_position: Optional[LogoPosition] = Field(
        default=LogoPosition.TOP_CENTER,
        description="Position of the logo on generated visitor badges.",
    )

    # --- Validators ---

    @field_validator(
        "primary_color",
        "secondary_color",
        "accent_color",
        "badge_header_color",
        "badge_text_color",
        mode="before",
    )
    @classmethod
    def validate_colors(cls, v, info):
        return _validate_hex_color(v, info.field_name)


class BrandingCreate(BrandingBase):
    """Internal creation schema — built by the service layer."""

    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class BrandingUpdate(BaseModel):
    """Partial update — all fields optional."""

    company_display_name: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    badge_header_color: Optional[str] = None
    badge_text_color: Optional[str] = None
    logo_object_key: Optional[str] = None
    favicon_object_key: Optional[str] = None
    badge_logo_position: Optional[LogoPosition] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @field_validator(
        "primary_color",
        "secondary_color",
        "accent_color",
        "badge_header_color",
        "badge_text_color",
        mode="before",
    )
    @classmethod
    def validate_colors(cls, v, info):
        return _validate_hex_color(v, info.field_name)


class BrandingOut(BrandingBase):
    """Response schema for branding configuration."""

    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

    # --- Presigned URLs (populated by service layer, not stored) ---
    logo_url: Optional[str] = Field(
        default=None,
        description="Presigned URL for the tenant logo. Generated on read, not stored.",
    )
    favicon_url: Optional[str] = Field(
        default=None,
        description="Presigned URL for the favicon. Generated on read, not stored.",
    )

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class BrandingPublicOut(BaseModel):
    """Minimal branding for public / unauthenticated contexts (e.g. login screen).

    Omits internal object keys, badge-specific colors, and timestamps.

    ``powered_by_visichek`` is true for tenants on the Free plan — the
    frontend renders a "Powered by Visichek" watermark on the public
    visitor flow when this flag is set. False (or absent) for paid tiers.
    """

    tenant_id: str
    company_display_name: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    powered_by_visichek: bool = False
