"""Unit tests for the per-tenant Data Processing Agreement feature.

The DPA template asset only exists in production, so every test that needs a
template patches the loader with a small fake — nothing here touches the real
asset file or a live database.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

# A minimal stand-in for the committed template asset, with the same
# Organization placeholders as the real document (curly apostrophe).
_FAKE_TEMPLATE = {
    "title": "Data Processing Agreement",
    "summary": "DPA for [Insert Organization’s representative legal name].",
    "body": [
        {
            "id": "x1",
            "type": "paragraph",
            "props": {},
            "content": [
                {
                    "type": "text",
                    "text": "Name: [Insert Organization’s representative legal name]",
                }
            ],
            "children": [],
        },
        {
            "id": "x2",
            "type": "paragraph",
            "props": {},
            "content": [
                {
                    "type": "text",
                    "text": "Address: [Insert Organization’s representative address]",
                }
            ],
            "children": [],
        },
        {
            "id": "x3",
            "type": "paragraph",
            "props": {},
            "content": [
                {
                    "type": "text",
                    "text": "Contact Email: [Insert Organization’s representative email]",
                }
            ],
            "children": [],
        },
        {
            "id": "x4",
            "type": "heading",
            "props": {"level": 2},
            "content": [
                {"type": "text", "text": "1. Parties", "styles": {"bold": True}}
            ],
            "children": [],
        },
    ],
}


def _flatten(blocks):
    return "\n".join(
        "".join(n.get("text", "") for n in b.get("content", [])) for b in blocks
    )


# --- Template substitution ---------------------------------------------------


@pytest.mark.unit
def test_dpa_template_substitutes_org_details():
    from services import dpa_defaults as d

    with patch.object(d, "_load_template", return_value=_FAKE_TEMPLATE):
        content = d.build_tenant_dpa_content(
            company_name="Acme Ltd",
            organization_address="1 Main St, Lagos",
            contact_email="admin@acme.example.com",
        )

    assert content is not None
    assert content["title"] == "Data Processing Agreement"
    text = _flatten(content["body"])
    assert "Acme Ltd" in text
    assert "1 Main St, Lagos" in text
    assert "admin@acme.example.com" in text
    assert "[Insert Organization" not in text
    # The fixed VisiChek party / heading text survives untouched.
    assert "1. Parties" in text
    # Block ids are regenerated (no template ids leak through).
    assert all(b["id"].startswith("b_") for b in content["body"])
    # full_text mirrors the block content.
    assert "Acme Ltd" in content["full_text"]


@pytest.mark.unit
def test_dpa_template_unset_fields_fall_back():
    from services import dpa_defaults as d

    with patch.object(d, "_load_template", return_value=_FAKE_TEMPLATE):
        content = d.build_tenant_dpa_content(company_name="Acme Ltd")

    assert content is not None
    text = _flatten(content["body"])
    assert "[To be provided]" in text  # address + email unset
    assert "[Insert Organization" not in text


@pytest.mark.unit
def test_dpa_build_returns_none_without_template():
    from services import dpa_defaults as d

    with patch.object(d, "_load_template", return_value=None):
        assert d.build_tenant_dpa_content(company_name="X") is None
        assert d.template_is_available() is False


# --- Service: build / freeze on accept --------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_retrieve_or_build_creates_when_missing():
    from services import dpa_service as svc

    built_content = {
        "title": "Data Processing Agreement",
        "summary": "s",
        "full_text": "f",
        "body": [
            {
                "id": "b_1",
                "type": "paragraph",
                "props": {},
                "content": [],
                "children": [],
            }
        ],
    }
    with (
        patch.object(svc, "get_dpa_for_tenant", new_callable=AsyncMock) as gd,
        patch.object(svc, "_build_content_for_tenant", new_callable=AsyncMock) as bc,
        patch.object(svc, "upsert_dpa", new_callable=AsyncMock) as up,
    ):
        gd.return_value = None
        bc.return_value = built_content
        up.return_value = SimpleNamespace(id="d1", accepted=False, version="1.0")

        res = await svc.retrieve_or_build_tenant_dpa(str(ObjectId()))

    assert res is not None and res.id == "d1"
    up.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_retrieve_returns_frozen_copy_when_accepted():
    from services import dpa_service as svc

    existing = SimpleNamespace(accepted=True, id="d1", version="1.0")
    with (
        patch.object(svc, "get_dpa_for_tenant", new_callable=AsyncMock) as gd,
        patch.object(svc, "upsert_dpa", new_callable=AsyncMock) as up,
        patch.object(svc, "_build_content_for_tenant", new_callable=AsyncMock) as bc,
    ):
        gd.return_value = existing

        res = await svc.retrieve_or_build_tenant_dpa("tenant-1")

    assert res is existing
    up.assert_not_awaited()
    bc.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mark_accepted_freezes_and_stamps():
    from services import dpa_service as svc

    unaccepted = SimpleNamespace(accepted=False, id="d1", version="1.0")
    with (
        patch.object(svc, "retrieve_or_build_tenant_dpa", new_callable=AsyncMock) as rb,
        patch.object(svc, "update_dpa", new_callable=AsyncMock) as up,
        patch("services.audit_service.record_audit_event", new_callable=AsyncMock),
    ):
        rb.return_value = unaccepted
        up.return_value = SimpleNamespace(id="d1", version="1.0")

        res = await svc.mark_tenant_dpa_accepted("tenant-1", actor_id="u1")

    assert res is not None
    up.assert_awaited_once()
    passed = up.await_args.args[1]
    assert passed.accepted is True
    assert passed.accepted_by == "u1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mark_accepted_noop_without_template():
    from services import dpa_service as svc

    with (
        patch.object(svc, "retrieve_or_build_tenant_dpa", new_callable=AsyncMock) as rb,
        patch.object(svc, "update_dpa", new_callable=AsyncMock) as up,
    ):
        rb.return_value = None  # template unavailable

        res = await svc.mark_tenant_dpa_accepted("tenant-1", actor_id="u1")

    assert res is None
    up.assert_not_awaited()


# --- Backfill ----------------------------------------------------------------


# --- Startup bootstrap -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_bootstrap_loads_template_then_backfills():
    from services import dpa_bootstrap as bs

    with (
        patch(
            "services.dpa_service.ensure_dpa_template_loaded", new_callable=AsyncMock
        ) as ensure,
        patch(
            "services.dpa_backfill.backfill_tenant_dpa", new_callable=AsyncMock
        ) as backfill,
    ):
        ensure.return_value = True
        backfill.return_value = {"created": 2}

        summary = await bs.run_dpa_bootstrap()

    assert summary["template_loaded"] is True
    assert summary["backfill"] == {"created": 2}
    backfill.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_bootstrap_skips_backfill_without_template():
    from services import dpa_bootstrap as bs

    with (
        patch(
            "services.dpa_service.ensure_dpa_template_loaded", new_callable=AsyncMock
        ) as ensure,
        patch(
            "services.dpa_backfill.backfill_tenant_dpa", new_callable=AsyncMock
        ) as backfill,
    ):
        ensure.return_value = False

        summary = await bs.run_dpa_bootstrap()

    assert summary["template_loaded"] is False
    assert summary["backfill"] is None
    backfill.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ensure_template_loaded_reads_db_when_cache_empty():
    from services import dpa_service as svc

    fake_doc = SimpleNamespace(
        title="Data Processing Agreement",
        summary="s",
        published_body=None,
        body=[
            {"id": "x", "type": "paragraph", "props": {}, "content": [], "children": []}
        ],
    )
    with (
        patch.object(svc, "template_is_available", return_value=False),
        patch(
            "legal.repositories.legal_document_repo.get_legal_document_by_slug",
            new_callable=AsyncMock,
        ) as by_slug,
        patch.object(svc, "set_runtime_template") as setter,
    ):
        by_slug.return_value = fake_doc

        loaded = await svc.ensure_dpa_template_loaded()

    assert loaded is True
    setter.assert_called_once()
    assert setter.call_args.kwargs["body"] == fake_doc.body


@pytest.mark.asyncio
@pytest.mark.unit
async def test_backfill_aborts_without_template():
    from services import dpa_backfill as bf

    with patch.object(bf, "template_is_available", return_value=False):
        summary = await bf.backfill_tenant_dpa()

    assert summary["created"] == 0
    assert summary["errors"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_backfill_creates_unaccepted_for_new_tenant():
    from services import dpa_backfill as bf

    tenant = SimpleNamespace(id=str(ObjectId()), dpa_accepted=False)
    with (
        patch.object(bf, "template_is_available", return_value=True),
        patch.object(bf, "get_tenants", new_callable=AsyncMock) as gt,
        patch.object(bf, "get_dpa_for_tenant", new_callable=AsyncMock) as gd,
        patch.object(bf, "retrieve_or_build_tenant_dpa", new_callable=AsyncMock) as rb,
        patch.object(bf, "update_dpa", new_callable=AsyncMock) as up,
    ):
        gt.return_value = [tenant]
        gd.return_value = None
        rb.return_value = SimpleNamespace(id="d1", version="1.0", accepted=False)

        summary = await bf.backfill_tenant_dpa()

    assert summary["created"] == 1
    assert summary["created_accepted"] == 0
    up.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_backfill_freezes_accepted_tenant_metadata():
    from services import dpa_backfill as bf

    tenant = SimpleNamespace(
        id=str(ObjectId()),
        dpa_accepted=True,
        dpa_accepted_at=111,
        dpa_accepted_by="u1",
        dpa_version="1.0",
    )
    with (
        patch.object(bf, "template_is_available", return_value=True),
        patch.object(bf, "get_tenants", new_callable=AsyncMock) as gt,
        patch.object(bf, "get_dpa_for_tenant", new_callable=AsyncMock) as gd,
        patch.object(bf, "retrieve_or_build_tenant_dpa", new_callable=AsyncMock) as rb,
        patch.object(bf, "update_dpa", new_callable=AsyncMock) as up,
    ):
        gt.return_value = [tenant]
        gd.return_value = None
        rb.return_value = SimpleNamespace(id="d1", version="1.0", accepted=False)

        summary = await bf.backfill_tenant_dpa()

    assert summary["created_accepted"] == 1
    up.assert_awaited_once()
    passed = up.await_args.args[1]
    assert passed.accepted is True
    assert passed.accepted_at == 111
    assert passed.accepted_by == "u1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_backfill_skips_tenant_with_existing_dpa():
    from services import dpa_backfill as bf

    tenant = SimpleNamespace(id=str(ObjectId()), dpa_accepted=False)
    with (
        patch.object(bf, "template_is_available", return_value=True),
        patch.object(bf, "get_tenants", new_callable=AsyncMock) as gt,
        patch.object(bf, "get_dpa_for_tenant", new_callable=AsyncMock) as gd,
        patch.object(bf, "retrieve_or_build_tenant_dpa", new_callable=AsyncMock) as rb,
    ):
        gt.return_value = [tenant]
        gd.return_value = SimpleNamespace(id="d1")  # already has a DPA

        summary = await bf.backfill_tenant_dpa()

    assert summary["skipped"] == 1
    assert summary["created"] == 0
    rb.assert_not_awaited()
