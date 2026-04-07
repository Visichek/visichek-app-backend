"""Tests for branding schema validation and structure."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.branding_schema import (
    BrandingBase,
    BrandingCreate,
    BrandingUpdate,
    BrandingOut,
)
from schemas.imports import LogoPosition


class TestBrandingColorValidation:
    """Hex color validation on branding schemas."""

    def test_valid_6_digit_hex(self):
        b = BrandingUpdate(primary_color="#FF5500")
        assert b.primary_color == "#FF5500"

    def test_valid_3_digit_hex(self):
        b = BrandingUpdate(primary_color="#F50")
        assert b.primary_color == "#F50"

    def test_lowercase_hex_uppercased(self):
        b = BrandingUpdate(primary_color="#ff5500")
        assert b.primary_color == "#FF5500"

    def test_invalid_hex_no_hash(self):
        with pytest.raises(ValidationError, match="valid hex color"):
            BrandingUpdate(primary_color="FF5500")

    def test_invalid_hex_wrong_length(self):
        with pytest.raises(ValidationError, match="valid hex color"):
            BrandingUpdate(primary_color="#FF550")

    def test_invalid_hex_bad_chars(self):
        with pytest.raises(ValidationError, match="valid hex color"):
            BrandingUpdate(primary_color="#GGGGGG")

    def test_none_color_accepted(self):
        b = BrandingUpdate(primary_color=None)
        assert b.primary_color is None

    def test_all_colors_validated(self):
        b = BrandingUpdate(
            primary_color="#111111",
            secondary_color="#222222",
            accent_color="#333333",
            badge_header_color="#444444",
            badge_text_color="#555555",
        )
        assert b.primary_color == "#111111"
        assert b.badge_text_color == "#555555"


class TestBrandingSchemaFields:
    """Verify schema field structure."""

    def test_branding_update_all_optional(self):
        """All fields in BrandingUpdate should be optional."""
        b = BrandingUpdate()
        assert b.primary_color is None
        assert b.company_display_name is None
        assert b.logo_object_key is None

    def test_branding_out_has_url_fields(self):
        """BrandingOut should have logo_url and favicon_url."""
        assert "logo_url" in BrandingOut.model_fields
        assert "favicon_url" in BrandingOut.model_fields

    def test_branding_out_urls_not_in_base(self):
        """logo_url and favicon_url should not be in BrandingBase (not stored)."""
        assert "logo_url" not in BrandingBase.model_fields
        assert "favicon_url" not in BrandingBase.model_fields

    def test_branding_create_has_timestamps(self):
        b = BrandingCreate(tenant_id="123")
        assert b.date_created > 0
        assert b.last_updated > 0

    def test_branding_update_has_last_updated(self):
        b = BrandingUpdate()
        assert b.last_updated > 0


class TestLogoPosition:
    """LogoPosition enum values."""

    def test_all_positions(self):
        positions = {e.value for e in LogoPosition}
        assert positions == {"top_left", "top_center", "top_right", "center"}

    def test_default_position(self):
        b = BrandingCreate(tenant_id="123")
        assert b.badge_logo_position == LogoPosition.TOP_CENTER

    def test_custom_position(self):
        b = BrandingUpdate(badge_logo_position=LogoPosition.TOP_LEFT)
        assert b.badge_logo_position == LogoPosition.TOP_LEFT
