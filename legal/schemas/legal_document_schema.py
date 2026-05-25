"""Pydantic v2 schemas for legal documents.

A legal document is a *head* record (one per ``slug``) that holds the
editable working ``body`` plus the currently-live ``published_body``.
Each publish also writes an immutable snapshot into the
``legal_document_versions`` collection — modelled by
``LegalDocumentVersionOut``.

Field names are snake_case; the ``CaseConversionMiddleware`` converts
request/response payloads to/from camelCase on the wire (so the frontend
sees ``docType``, ``publishedBody``, ``sourceFileUrl``, etc.).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

from legal.schemas.imports import (
    LegalDocStatus,
    LegalDocType,
    excerpt_from_blocks,
    generate_slug,
)


# ---------------------------------------------------------------------------
# Embedded source-file descriptor
# ---------------------------------------------------------------------------


class SourceFile(BaseModel):
    """The original uploaded Word/PDF a document was imported from.

    ``object_key`` points at the privately-stored object in
    ``DocumentStorageManager``. The presigned download URL is resolved on
    read (never persisted) into ``LegalDocumentOut.source_file_url``.
    """

    object_key: str
    file_name: str
    mime_type: str
    size: Optional[int] = None
    uploaded_at: int = Field(default_factory=lambda: int(time.time()))


# ---------------------------------------------------------------------------
# Base / Create / Update
# ---------------------------------------------------------------------------

_BodyBlocks = List[Dict[str, Any]]


class LegalDocumentBase(BaseModel):
    """Shared editable fields."""

    title: str = Field(
        ..., min_length=1, description="Human display name, e.g. 'Privacy Policy'."
    )
    doc_type: LegalDocType = Field(
        default=LegalDocType.other,
        description="Grouping tag. 'other' for admin-defined documents.",
    )
    summary: Optional[str] = Field(
        default=None, description="Short description shown in lists / SEO."
    )
    body: _BodyBlocks = Field(
        default_factory=list,
        description="BlockNote-style content blocks (working/draft copy).",
    )


class LegalDocumentCreate(LegalDocumentBase):
    """Create a new legal document head record (starts as a draft)."""

    slug: Optional[str] = Field(
        default=None, description="Auto-generated from the title when omitted."
    )
    status: LegalDocStatus = LegalDocStatus.draft
    source_file: Optional[SourceFile] = None
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def _set_defaults(self) -> "LegalDocumentCreate":
        if not self.slug:
            self.slug = generate_slug(self.title)
        if not self.summary:
            self.summary = excerpt_from_blocks(self.body) or None
        return self


class LegalDocumentUpdate(BaseModel):
    """Partial update of the working copy / metadata. All fields optional.

    Lifecycle transitions (publish/archive) are NOT done here — they have
    dedicated endpoints so the version snapshot logic stays explicit.
    """

    title: Optional[str] = None
    doc_type: Optional[LegalDocType] = None
    summary: Optional[str] = None
    body: Optional[_BodyBlocks] = None
    slug: Optional[str] = None
    source_file: Optional[SourceFile] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return generate_slug(v)


class LegalDocumentPublishRequest(BaseModel):
    """Publish the current working ``body`` as a new immutable version."""

    effective_at: Optional[int] = Field(
        default=None,
        description="Unix seconds when this version takes effect. Defaults to now.",
    )
    change_note: Optional[str] = Field(
        default=None, description="Optional human note recorded on the version."
    )


# ---------------------------------------------------------------------------
# Out — admin (full) + admin list row
# ---------------------------------------------------------------------------


def _coerce_objectid(values: Any) -> Any:
    if (
        isinstance(values, dict)
        and "_id" in values
        and isinstance(values["_id"], ObjectId)
    ):
        values["_id"] = str(values["_id"])
    return values


class LegalDocumentOut(LegalDocumentBase):
    """Full admin-facing document (working copy + lifecycle metadata)."""

    id: Optional[str] = Field(default=None, alias="_id")
    slug: Optional[str] = None
    status: LegalDocStatus = LegalDocStatus.draft
    current_version: Optional[int] = None
    published_body: Optional[_BodyBlocks] = None
    published_at: Optional[int] = None
    effective_at: Optional[int] = None
    has_unpublished_changes: bool = False
    source_file: Optional[SourceFile] = None
    # Resolved presigned download URL for the original upload (read-only).
    source_file_url: Optional[str] = None
    date_created: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("date_created", "dateCreated"),
        serialization_alias="dateCreated",
    )
    last_updated: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("last_updated", "lastUpdated"),
        serialization_alias="lastUpdated",
    )

    @model_validator(mode="before")
    @classmethod
    def _before(cls, values: Any) -> Any:
        return _coerce_objectid(values)

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class LegalDocumentListRow(BaseModel):
    """Compact admin list row — omits heavy body payloads."""

    id: Optional[str] = Field(default=None, alias="_id")
    slug: Optional[str] = None
    title: str
    doc_type: Optional[LegalDocType] = LegalDocType.other
    summary: Optional[str] = None
    status: LegalDocStatus = LegalDocStatus.draft
    current_version: Optional[int] = None
    published_at: Optional[int] = None
    effective_at: Optional[int] = None
    has_unpublished_changes: bool = False
    has_source_file: bool = False
    date_created: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("date_created", "dateCreated"),
        serialization_alias="dateCreated",
    )
    last_updated: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("last_updated", "lastUpdated"),
        serialization_alias="lastUpdated",
    )

    @model_validator(mode="before")
    @classmethod
    def _before(cls, values: Any) -> Any:
        values = _coerce_objectid(values)
        if isinstance(values, dict) and "has_source_file" not in values:
            values["has_source_file"] = bool(values.get("source_file"))
        return values

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


# ---------------------------------------------------------------------------
# Out — public website variants (published content only)
# ---------------------------------------------------------------------------


class LegalDocumentPublicOut(BaseModel):
    """Public detail view — serves the live ``published_body`` only.

    Working drafts and the internal ``object_key`` are never exposed.
    """

    id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("_id", "id"),
        serialization_alias="id",
    )
    slug: Optional[str] = None
    title: str
    doc_type: Optional[LegalDocType] = LegalDocType.other
    summary: Optional[str] = None
    version: Optional[int] = Field(default=None, description="Live published version.")
    body: _BodyBlocks = Field(default_factory=list)
    effective_at: Optional[int] = None
    published_at: Optional[int] = None
    source_file_url: Optional[str] = None
    last_updated: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("last_updated", "lastUpdated"),
        serialization_alias="lastUpdated",
    )

    @model_validator(mode="before")
    @classmethod
    def _before(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        values = dict(values)
        if "_id" in values and isinstance(values["_id"], ObjectId):
            values["_id"] = str(values["_id"])
        # Map head-doc storage shape → public shape.
        if "version" not in values and "current_version" in values:
            values["version"] = values.get("current_version")
        # The public site serves the LIVE published body, never the working
        # draft. When fed a head doc (which carries both ``body`` and
        # ``published_body``), prefer ``published_body``. A version snapshot
        # has no ``published_body`` key, so its ``body`` is used as-is.
        if "published_body" in values:
            values["body"] = values.get("published_body") or []
        elif values.get("body") is None:
            values["body"] = []
        return values

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class LegalDocumentPublicListRow(BaseModel):
    """Compact public list row — no body."""

    id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("_id", "id"),
        serialization_alias="id",
    )
    slug: Optional[str] = None
    title: str
    doc_type: Optional[LegalDocType] = LegalDocType.other
    summary: Optional[str] = None
    version: Optional[int] = None
    effective_at: Optional[int] = None
    published_at: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _before(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        values = dict(values)
        if "_id" in values and isinstance(values["_id"], ObjectId):
            values["_id"] = str(values["_id"])
        if "version" not in values and "current_version" in values:
            values["version"] = values.get("current_version")
        return values

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


# ---------------------------------------------------------------------------
# Immutable version snapshot
# ---------------------------------------------------------------------------


class LegalDocumentVersionOut(BaseModel):
    """An immutable published snapshot from ``legal_document_versions``."""

    id: Optional[str] = Field(default=None, alias="_id")
    document_id: str
    slug: str
    title: str
    doc_type: Optional[LegalDocType] = LegalDocType.other
    version: int
    body: _BodyBlocks = Field(default_factory=list)
    effective_at: Optional[int] = None
    published_at: Optional[int] = None
    published_by: Optional[str] = None
    change_note: Optional[str] = None
    source_file: Optional[SourceFile] = None
    date_created: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("date_created", "dateCreated"),
        serialization_alias="dateCreated",
    )

    @model_validator(mode="before")
    @classmethod
    def _before(cls, values: Any) -> Any:
        return _coerce_objectid(values)

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


# ---------------------------------------------------------------------------
# Import (Word/PDF upload) response
# ---------------------------------------------------------------------------


class LegalDocumentImportPreview(BaseModel):
    """Returned by the import endpoint after converting an uploaded file.

    ``document`` is the freshly-created draft (already persisted); ``blocks``
    is the converted BlockNote body so the editor can render it immediately
    without a follow-up GET. ``warnings`` flags lossy conversions (e.g. PDF
    heading heuristics).
    """

    document: LegalDocumentOut
    blocks: _BodyBlocks = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
