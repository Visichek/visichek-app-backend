"""Unit tests for the legal documents feature.

Split into:
  * pure schema tests          (no DB)
  * pure conversion-service    (text path; no optional deps)
  * permission-config coverage (no DB)
  * route tests                (patch enqueue_write; override admin dep)

The route tests follow the host-route pattern: a request carrying an
Authorization header still triggers the real pre-dep token lookup, so they
require a reachable MongoDB like every other unit route test in this suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from legal.schemas.imports import LegalDocStatus, generate_slug
from legal.schemas.legal_document_schema import (
    LegalDocumentCreate,
    LegalDocumentOut,
    LegalDocumentPublicOut,
    LegalDocumentPublishRequest,
)
from legal.services.legal_conversion_service import (
    _looks_like_heading,
    _text_to_blocks,
    detect_kind,
)


# ---------------------------------------------------------------------------
# Schema tests (pure)
# ---------------------------------------------------------------------------


class TestLegalSchemas:
    @pytest.mark.unit
    def test_create_autogenerates_slug_and_summary(self):
        doc = LegalDocumentCreate(
            title="Privacy Policy",
            body=[
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Hello world."}],
                }
            ],
        )
        assert doc.slug == "privacy-policy"
        assert doc.status == LegalDocStatus.draft
        assert doc.summary and "Hello world" in doc.summary

    @pytest.mark.unit
    def test_create_respects_explicit_slug(self):
        doc = LegalDocumentCreate(title="Terms", slug="custom-terms-2024", body=[])
        assert doc.slug == "custom-terms-2024"

    @pytest.mark.unit
    def test_generate_slug_sanitizes(self):
        assert generate_slug("Acceptable Use Policy!!") == "acceptable-use-policy"
        assert generate_slug("") == "untitled-document"

    @pytest.mark.unit
    def test_publish_request_optional_fields(self):
        req = LegalDocumentPublishRequest()
        assert req.effective_at is None
        assert req.change_note is None

    @pytest.mark.unit
    def test_out_coerces_objectid_and_aliases(self):
        from bson import ObjectId

        oid = ObjectId()
        out = LegalDocumentOut(
            **{
                "_id": oid,
                "title": "T",
                "slug": "t",
                "date_created": 100,
                "last_updated": 200,
            }
        )
        assert out.id == str(oid)
        dumped = out.model_dump(by_alias=True)
        assert dumped["dateCreated"] == 100
        assert dumped["lastUpdated"] == 200

    @pytest.mark.unit
    def test_public_out_maps_published_body_and_version(self):
        head = {
            "title": "Privacy",
            "slug": "privacy",
            "current_version": 3,
            "published_body": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Live."}]}
            ],
            "body": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Draft edit."}],
                }
            ],
        }
        public = LegalDocumentPublicOut(**head)
        # Public must serve the published body, NOT the working draft.
        assert public.version == 3
        assert public.body[0]["content"][0]["text"] == "Live."


# ---------------------------------------------------------------------------
# Conversion service (text path — no optional deps required)
# ---------------------------------------------------------------------------


class TestConversion:
    @pytest.mark.unit
    def test_detect_kind(self):
        assert detect_kind("a.docx", "") == "docx"
        assert detect_kind("a.pdf", "") == "pdf"
        assert detect_kind("a.txt", "") == "text"
        assert detect_kind("a.md", "") == "markdown"
        assert detect_kind("a.markdown", "") == "markdown"
        assert detect_kind("", "text/markdown") == "markdown"
        assert detect_kind("", "application/pdf") == "pdf"
        assert detect_kind("a.bin", "application/octet-stream") == "unsupported"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_markdown_converts_to_structured_blocks(self):
        from legal.services.legal_conversion_service import convert_upload_to_blocks

        md = b"# Title\n\nA **bold** word.\n\n## Sub\n\n- one\n- two\n\n#### Deep\n"
        blocks, _ = await convert_upload_to_blocks(md, "doc.md", "text/markdown")
        types = [b["type"] for b in blocks]
        # Markdown structure must survive — not be dumped as plain paragraphs.
        assert "heading" in types
        assert "bulletListItem" in types
        # First block is the H1 with level 1.
        assert blocks[0]["type"] == "heading" and blocks[0]["props"]["level"] == 1
        # h4 clamps to BlockNote's max supported level (3).
        deep = [b for b in blocks if b["type"] == "heading"][-1]
        assert deep["props"]["level"] == 3
        # Inline bold style is preserved.
        para = next(b for b in blocks if b["type"] == "paragraph")
        assert any(n.get("styles", {}).get("bold") for n in para["content"])

    @pytest.mark.unit
    def test_looks_like_heading(self):
        assert _looks_like_heading("Data Retention Policy") is True
        assert _looks_like_heading("INTRODUCTION") is True
        assert (
            _looks_like_heading(
                "This is a long sentence that clearly is a body paragraph, "
                "with a trailing period."
            )
            is False
        )

    @pytest.mark.unit
    def test_text_to_blocks_paragraphs_and_headings(self):
        text = "INTRODUCTION\n\nThis is the body of the section. It continues here."
        blocks = _text_to_blocks(text, detect_headings=True)
        assert blocks[0]["type"] == "heading"
        assert blocks[0]["props"]["level"] == 2
        assert blocks[1]["type"] == "paragraph"

    @pytest.mark.unit
    def test_text_to_blocks_no_heading_detection(self):
        text = "Section One\n\nSome content."
        blocks = _text_to_blocks(text, detect_headings=False)
        assert all(b["type"] == "paragraph" for b in blocks)


# ---------------------------------------------------------------------------
# Permission config coverage (pure)
# ---------------------------------------------------------------------------


class TestPermissions:
    @pytest.mark.unit
    def test_legal_routes_registered_in_admin_permissions(self):
        from config.role_permissions import ADMIN_PERMISSIONS

        paths = {p.path for p in ADMIN_PERMISSIONS}
        # A representative sample of the route paths must be present.
        for path in (
            "/v1/legal-documents",
            "/v1/legal-documents/{document_id}",
            "/v1/legal-documents/{document_id}/publish",
            "/v1/legal-documents/import",
        ):
            assert path in paths, f"missing permission for {path}"

    @pytest.mark.unit
    def test_content_only_preset_includes_legal(self):
        from config.role_permissions import ADMIN_ACCESS_PRESETS

        for preset in ("content_only", "content_support"):
            perms = ADMIN_ACCESS_PRESETS[preset]
            assert any(p.path.startswith("/v1/legal-documents") for p in perms), (
                f"{preset} missing legal permissions"
            )


# ---------------------------------------------------------------------------
# Route tests (need reachable MongoDB; mirror host-route pattern)
# ---------------------------------------------------------------------------

MOCK_ADMIN = SimpleNamespace(id="admin-123", role="admin")


@pytest.fixture
def cleanup_dependency_overrides():
    yield
    from main import app

    app.dependency_overrides.clear()


class TestLegalRoutes:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_create_legal_document_returns_202(
        self, cleanup_dependency_overrides
    ):
        from main import app
        from security.account_status_check import (
            check_admin_account_status_and_permissions,
        )

        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MOCK_ADMIN
        )

        with patch(
            "legal.routes.admin_legal_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": "legal-001",
                "job_id": "job-1",
                "status": "queued",
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/v1/legal-documents",
                    json={"title": "Privacy Policy", "docType": "privacy_policy"},
                    headers={"Authorization": "Bearer t"},
                )
        assert resp.status_code == 202
        body = resp.json()
        assert body["data"]["jobId"] == "job-1"
        assert mock_enqueue.await_args.kwargs["writer_key"] == "legal_document.create"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_publish_returns_202(self, cleanup_dependency_overrides):
        from bson import ObjectId

        from main import app
        from security.account_status_check import (
            check_admin_account_status_and_permissions,
        )

        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MOCK_ADMIN
        )
        doc_id = str(ObjectId())

        with patch(
            "legal.routes.admin_legal_route.enqueue_write", new_callable=AsyncMock
        ) as mock_enqueue:
            mock_enqueue.return_value = {
                "id": doc_id,
                "job_id": "job-2",
                "status": "queued",
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/v1/legal-documents/{doc_id}/publish",
                    json={"changeNote": "initial publish"},
                    headers={"Authorization": "Bearer t"},
                )
        assert resp.status_code == 202
        assert mock_enqueue.await_args.kwargs["writer_key"] == "legal_document.publish"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_update_rejects_bad_id(self, cleanup_dependency_overrides):
        from main import app
        from security.account_status_check import (
            check_admin_account_status_and_permissions,
        )

        app.dependency_overrides[check_admin_account_status_and_permissions] = lambda: (
            MOCK_ADMIN
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/v1/legal-documents/not-an-objectid",
                json={"title": "x"},
                headers={"Authorization": "Bearer t"},
            )
        assert resp.status_code == 400
