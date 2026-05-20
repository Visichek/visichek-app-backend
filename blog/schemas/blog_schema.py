"""Blog Pydantic schemas (v2).

Ported from ``visichek-blog-backend/schemas/blog.py``. The four variants
``BlogCreate`` / ``BlogUpdate`` / ``BlogOut`` / ``BlogOutLessDetail``
plus their public-website counterparts ``BlogOutUserVersion`` and
``BlogOutLessDetailUserVersion`` are preserved 1:1 so frontends keep
working.

The main mechanical changes from the original:

* All Pydantic v1 ``root_validator`` / ``validator`` calls became v2
  ``model_validator`` / ``field_validator``.
* ``Config`` inner classes became ``model_config = {...}``.
* ``_id`` → ``str`` coercion uses ``isinstance(values, dict)`` guard so
  it's safe to call on Pydantic model instances during re-validation
  (host-backend convention from CLAUDE.md gotcha #6).
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pydantic import AliasChoices, BaseModel, Field, model_validator

from blog.schemas.imports import (
    Author,
    BlogStatus,
    BlogType,
    Category,
    CategoryNameEnum,  # re-exported for callers # noqa: F401
    MediaAsset,
    Page,
)


# ---------------------------------------------------------------------------
# Helpers — slug + excerpt
# ---------------------------------------------------------------------------


def _generate_slug(title: str) -> str:
    if not title:
        return "untitled-blog"
    cleaned = title.lower()
    cleaned = re.sub(r"[^a-z0-9\s-]", "", cleaned)
    cleaned = re.sub(r"[\s-]+", "-", cleaned)
    cleaned = cleaned.strip("-")
    return cleaned or "untitled-blog"


def _generate_excerpt(
    current_page_body: Optional[List[Dict[str, Any]]], max_length: int = 200
) -> str:
    if not current_page_body:
        return ""
    texts: List[str] = []
    for block in current_page_body:
        for content in block.get("content", []) or []:
            if isinstance(content, dict) and content.get("type") == "text":
                texts.append(content.get("text", ""))
    full_text = " ".join(texts).strip()
    if len(full_text) > max_length:
        return full_text[:max_length].rstrip() + "..."
    return full_text


# ---------------------------------------------------------------------------
# Base + Create + Update + Out
# ---------------------------------------------------------------------------


class BlogBase(BaseModel):
    """Shared fields for blog input/output."""

    title: str = Field(..., description="The main title of the article.")
    author: Author
    category: Category
    blogType: Optional[BlogType] = BlogType.normal
    featureImage: Optional[MediaAsset] = None
    pages: Optional[List[Page]] = None
    currentPageBody: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="List of BlockNote-style content blocks for the current page.",
    )

    @model_validator(mode="after")
    def check_mutually_exclusive_fields(self) -> "BlogBase":
        if self.pages is not None and self.currentPageBody is not None:
            raise ValueError(
                "You must provide EITHER 'pages' OR 'currentPageBody', not both."
            )
        if self.pages is None and self.currentPageBody is None:
            raise ValueError("You must provide one of: 'pages' OR 'currentPageBody'.")
        return self


class BlogCreate(BlogBase):
    """Schema for creating a new Blog entry."""

    state: Optional[BlogStatus] = BlogStatus.draft
    slug: Optional[str] = Field(
        None, description="Optional. Auto-generated from the title if missing."
    )
    excerpt: Optional[str] = Field(
        None,
        description="Optional. Auto-generated from the first text blocks if missing.",
    )
    publishDate: Optional[int] = None
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def set_defaults(self) -> "BlogCreate":
        if not self.slug and self.title:
            self.slug = _generate_slug(self.title)
        elif not self.slug:
            self.slug = "invalid-slug"

        if not self.excerpt or self.excerpt == "Article content is currently empty.":
            self.excerpt = _generate_excerpt(self.currentPageBody) or (
                "Article content is currently empty."
            )

        if self.state == BlogStatus.published and not self.publishDate:
            self.publishDate = int(time.time())

        return self


class BlogUpdate(BaseModel):
    """Partial-update schema; every field optional."""

    state: Optional[BlogStatus] = None
    title: Optional[str] = None
    author: Optional[Author] = None
    publishDate: Optional[int] = None
    category: Optional[Category] = None
    featureImage: Optional[MediaAsset] = None
    excerpt: Optional[str] = None
    pages: Optional[List[Page]] = None
    currentPageBody: Optional[List[Dict[str, Any]]] = None
    blogType: Optional[BlogType] = None
    slug: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def set_defaults(self) -> "BlogUpdate":
        if not self.excerpt and self.currentPageBody is not None:
            self.excerpt = _generate_excerpt(self.currentPageBody)

        if self.state == BlogStatus.published and not self.publishDate:
            self.publishDate = int(time.time())

        return self

    @model_validator(mode="after")
    def check_mutually_exclusive_fields(self) -> "BlogUpdate":
        if self.pages is not None and self.currentPageBody is not None:
            raise ValueError(
                "You must provide EITHER 'pages' OR 'currentPageBody', not both."
            )
        return self


# ---------------------------------------------------------------------------
# Out schemas — admin (full) + admin (less detail) + public website variants
# ---------------------------------------------------------------------------


class BlogOutLessDetail(BaseModel):
    """List-row representation used in admin paginated lists."""

    id: Optional[str] = Field(default=None, alias="_id")
    title: str
    author: Author
    category: Category
    blogType: Optional[BlogType] = BlogType.normal
    featureImage: Optional[MediaAsset] = None
    state: Optional[BlogStatus] = BlogStatus.draft
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
    slug: Optional[str] = None
    excerpt: Optional[str] = None
    totalItems: Optional[int] = None
    itemIndex: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_inputs(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if "_id" in values and isinstance(values["_id"], ObjectId):
            values["_id"] = str(values["_id"])
        # Excerpt fallback (matches blog backend behaviour)
        if (
            not values.get("excerpt")
            or values.get("excerpt") == "Article content is currently empty."
        ):
            values["excerpt"] = _generate_excerpt(
                values.get("currentPageBody", [])
            ) or ("Article content is currently empty.")
        return values

    @model_validator(mode="after")
    def set_defaults(self) -> "BlogOutLessDetail":
        if (not self.slug and self.title) or self.slug == "invalid-slug":
            self.slug = _generate_slug(self.title or "")
        elif not self.slug:
            self.slug = "invalid-slug"
        return self

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class BlogOut(BlogBase):
    """Full admin-facing blog response."""

    id: Optional[str] = Field(default=None, alias="_id")
    state: Optional[BlogStatus] = BlogStatus.draft
    slug: Optional[str] = None
    excerpt: Optional[str] = None
    publishDate: Optional[int] = None
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
    def coerce_objectid(cls, values: Any) -> Any:
        if (
            isinstance(values, dict)
            and "_id" in values
            and isinstance(values["_id"], ObjectId)
        ):
            values["_id"] = str(values["_id"])
        return values

    @model_validator(mode="after")
    def set_defaults(self) -> "BlogOut":
        if (not self.slug and self.title) or self.slug == "invalid-slug":
            self.slug = _generate_slug(self.title or "")
        elif not self.slug:
            self.slug = "invalid-slug"

        if not self.excerpt or self.excerpt == "Article content is currently empty.":
            self.excerpt = _generate_excerpt(self.currentPageBody) or (
                "Article content is currently empty."
            )
        return self

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class BlogOutLessDetailUserVersion(BaseModel):
    """List-row used by the public website."""

    id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("_id", "id"),
        serialization_alias="id",
    )
    title: str
    author: Author
    category: Category
    blogType: Optional[BlogType] = BlogType.normal
    featureImage: Optional[MediaAsset] = None
    state: Optional[BlogStatus] = BlogStatus.draft
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
    slug: Optional[str] = None
    excerpt: Optional[str] = None
    itemIndex: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def coerce_objectid(cls, values: Any) -> Any:
        if (
            isinstance(values, dict)
            and "_id" in values
            and isinstance(values["_id"], ObjectId)
        ):
            values["_id"] = str(values["_id"])
        return values

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class BlogOutUserVersion(BlogBase):
    """Full blog detail page used by the public website."""

    id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("_id", "id"),
        serialization_alias="id",
    )
    state: Optional[BlogStatus] = BlogStatus.draft
    slug: Optional[str] = None
    excerpt: Optional[str] = None
    publishDate: Optional[int] = None
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
    def coerce_objectid(cls, values: Any) -> Any:
        if (
            isinstance(values, dict)
            and "_id" in values
            and isinstance(values["_id"], ObjectId)
        ):
            values["_id"] = str(values["_id"])
        return values

    @model_validator(mode="after")
    def set_defaults(self) -> "BlogOutUserVersion":
        if (not self.slug and self.title) or self.slug == "invalid-slug":
            self.slug = _generate_slug(self.title or "")
        elif not self.slug:
            self.slug = "invalid-slug"

        if not self.excerpt or self.excerpt == "Article content is currently empty.":
            self.excerpt = _generate_excerpt(self.currentPageBody) or (
                "Article content is currently empty."
            )
        return self

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class ListOfBlogs(BaseModel):
    totalItems: int
    blogs: List[BlogOutLessDetailUserVersion]


class ListOfBlogsWithSameCategories(BaseModel):
    totalItems: int
    category: Optional[CategoryNameEnum] = None
    blogs: List[BlogOutLessDetailUserVersion]
