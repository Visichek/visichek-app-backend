from __future__ import annotations

import pytest

from schemas.tenant_settings_schema import TenantSettingsBase, TenantSettingsUpdate
from services.tenant_settings_writer import _GATED_FIELDS_BY_FEATURE


@pytest.mark.unit
class TestNoDeadHostApprovalField:
    """require_host_approval was persisted and surfaced in the UI but never
    read by any code path. It must not reappear - a toggle that claims to
    gate access and does not is worse than no toggle at all."""

    def test_absent_from_base_schema(self):
        assert "require_host_approval" not in TenantSettingsBase.model_fields

    def test_absent_from_update_schema(self):
        assert "require_host_approval" not in TenantSettingsUpdate.model_fields

    def test_absent_from_writer_allowlist(self):
        # There is no standalone "_ALLOWED_SETTING_KEYS" collection in
        # tenant_settings_writer.py. The plan-gating map
        # _GATED_FIELDS_BY_FEATURE is the module-level collection whose
        # "visitor_policies" tuple carried the "require_host_approval"
        # literal referenced by the task brief (line 52). Assert it is
        # absent from every gated-field tuple in the map.
        all_gated_fields = {
            field for fields in _GATED_FIELDS_BY_FEATURE.values() for field in fields
        }
        assert "require_host_approval" not in all_gated_fields

    def test_update_payload_drops_the_key(self):
        upd = TenantSettingsUpdate.model_validate({"require_host_approval": True})
        assert "require_host_approval" not in upd.model_dump(exclude_unset=True)
