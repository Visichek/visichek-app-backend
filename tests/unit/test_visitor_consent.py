"""Unit tests for the visitor privacy-notice + consent feature.

All DB access is mocked at the service's import sites, so these run without a
live MongoDB (see CLAUDE.md "Writing unit tests for the queued architecture").
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

from core.errors import AppException, ErrorCode
from schemas.imports import NoticeDisplayMode
from schemas.privacy_notice_schema import PrivacyNoticeUpdate
from services.privacy_notice_defaults import build_default_notice_content


# --- Default notice template (APPENDIX 1) -----------------------------------


@pytest.mark.unit
def test_default_notice_substitutes_company_and_dpo():
    content = build_default_notice_content(
        company_name="Acme Health", dpo_contact_email="dpo@acme.example.com"
    )
    assert content["title"] == "Visitor Privacy Notice"
    assert "Acme Health" in content["summary"]
    assert "Acme Health" in content["full_text"]
    # DPO contact line is appended when an email exists.
    assert "at dpo@acme.example.com" in content["full_text"]
    assert "{{" not in content["full_text"]


@pytest.mark.unit
def test_default_notice_omits_dpo_line_when_absent():
    content = build_default_notice_content(company_name="Acme Health")
    assert "{{DPO_CONTACT_LINE}}" not in content["full_text"]
    assert "dpo@" not in content["full_text"]


# --- Version minting on content change (A.1) ---------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_mints_new_version_on_text_change():
    from services import privacy_notice_service as svc

    notice_id = str(ObjectId())
    current = SimpleNamespace(
        id=notice_id,
        title="Old Title",
        summary="Old summary",
        full_text="Old body",
        display_mode=NoticeDisplayMode.ACTIVE_CONSENT,
        version_code="v-old",
    )

    with (
        patch.object(svc, "get_active_notice_for_tenant", new_callable=AsyncMock) as ga,
        patch.object(svc, "update_privacy_notice", new_callable=AsyncMock) as up,
    ):
        ga.return_value = current
        up.return_value = SimpleNamespace(id=notice_id, version_code="x")

        await svc.update_notice_by_id(
            notice_id=notice_id,
            tenant_id="tenant-001",
            notice_data=PrivacyNoticeUpdate(title="Brand New Title"),
        )

        passed: PrivacyNoticeUpdate = up.await_args.args[1]
        assert passed.version_code is not None
        assert passed.version_code != "v-old"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_does_not_mint_version_for_is_active_only():
    from services import privacy_notice_service as svc

    notice_id = str(ObjectId())
    current = SimpleNamespace(
        id=notice_id,
        title="Title",
        summary="s",
        full_text="b",
        display_mode=NoticeDisplayMode.ACTIVE_CONSENT,
        version_code="v-old",
    )

    with (
        patch.object(svc, "get_active_notice_for_tenant", new_callable=AsyncMock) as ga,
        patch.object(svc, "update_privacy_notice", new_callable=AsyncMock) as up,
    ):
        ga.return_value = current
        up.return_value = SimpleNamespace(id=notice_id, version_code="v-old")

        # is_active True re-activation deactivates the prior active notice; here
        # current IS this notice so no extra deactivate, and no version bump.
        await svc.update_notice_by_id(
            notice_id=notice_id,
            tenant_id="tenant-001",
            notice_data=PrivacyNoticeUpdate(is_active=True),
        )

        passed: PrivacyNoticeUpdate = up.await_args.args[1]
        assert passed.version_code is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_rejects_overlong_title():
    from services import privacy_notice_service as svc

    with pytest.raises(AppException) as exc:
        await svc.update_notice_by_id(
            notice_id=str(ObjectId()),
            tenant_id="tenant-001",
            notice_data=PrivacyNoticeUpdate(title="x" * 201),
        )
    assert exc.value.detail["code"] == ErrorCode.VALIDATION_FAILED.value


# --- Consent enforcement (A.5) -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enforce_consent_raises_when_active_consent_and_not_granted():
    from services import consent_service as cs

    notice = SimpleNamespace(display_mode=NoticeDisplayMode.ACTIVE_CONSENT)
    with patch.object(cs, "get_active_notice_for_tenant", new_callable=AsyncMock) as ga:
        ga.return_value = notice
        with pytest.raises(AppException) as exc:
            await cs.enforce_consent_if_required("tenant-001", consent_granted=None)
    assert exc.value.detail["code"] == ErrorCode.CONSENT_REQUIRED.value


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enforce_consent_passes_when_granted():
    from services import consent_service as cs

    notice = SimpleNamespace(display_mode=NoticeDisplayMode.ACTIVE_CONSENT)
    with patch.object(cs, "get_active_notice_for_tenant", new_callable=AsyncMock) as ga:
        ga.return_value = notice
        # Should not raise.
        await cs.enforce_consent_if_required("tenant-001", consent_granted=True)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enforce_consent_noop_for_passive_notice():
    from services import consent_service as cs

    notice = SimpleNamespace(display_mode=NoticeDisplayMode.PASSIVE)
    with patch.object(cs, "get_active_notice_for_tenant", new_callable=AsyncMock) as ga:
        ga.return_value = notice
        await cs.enforce_consent_if_required("tenant-001", consent_granted=None)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_enforce_consent_noop_when_no_active_notice():
    from services import consent_service as cs

    with patch.object(cs, "get_active_notice_for_tenant", new_callable=AsyncMock) as ga:
        ga.return_value = None
        await cs.enforce_consent_if_required("tenant-001", consent_granted=None)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_record_visitor_consent_persists_when_granted():
    from services import consent_service as cs

    with patch(
        "repositories.consent_record_repo.create_consent_record",
        new_callable=AsyncMock,
    ) as create:
        await cs.record_visitor_consent(
            tenant_id="tenant-001",
            consent_granted=True,
            consent_method="kiosk_checkbox",
            privacy_notice_id="n1",
            privacy_notice_version_id="v1",
            consent_accepted_at=1716200000,
            checkin_id="c1",
            visitor_id="vis1",
        )
        create.assert_awaited_once()
        saved = create.await_args.args[0]
        assert saved.consent_granted is True
        assert saved.consent_timestamp == 1716200000
        assert saved.checkin_id == "c1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_record_visitor_consent_noop_without_signal():
    from services import consent_service as cs

    with patch(
        "repositories.consent_record_repo.create_consent_record",
        new_callable=AsyncMock,
    ) as create:
        await cs.record_visitor_consent(
            tenant_id="tenant-001",
            consent_granted=None,
        )
        create.assert_not_awaited()
