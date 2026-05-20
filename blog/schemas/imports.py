"""Shared imports + blog-domain types.

This file is the blog-namespace equivalent of ``schemas/imports.py`` in
the host backend, but is intentionally separate so it can't collide with
the host's enums (``AccountStatus``, ``Permission``, ``SystemUserRole``,
``LogoPosition``, etc.).

Most types here are 1:1 ports of the originals in
``visichek-blog-backend/schemas/imports.py``, with Pydantic v1
``root_validator`` / ``validator`` calls rewritten as Pydantic v2
``model_validator`` / ``field_validator`` so they coexist with the host
backend's Pydantic v2 base.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

# ---------------------------------------------------------------------------
# Author / Media
# ---------------------------------------------------------------------------


class Author(BaseModel):
    """Article author details (snapshot — not a foreign key)."""

    name: str
    avatarUrl: Optional[str] = Field(
        None, description="URL of the author's avatar image."
    )
    affiliation: str


class MediaAsset(BaseModel):
    """Feature and inline image descriptor."""

    url: str
    altText: str = Field(
        ..., description="A brief description of the image for accessibility."
    )
    credit: Optional[str] = Field(None, description="Image credit/source information.")


# ---------------------------------------------------------------------------
# Categories — preserved verbatim from blog backend
# ---------------------------------------------------------------------------


class CategorySlugEnum(str, Enum):
    # Security & Compliance
    PHYSICAL_SECURITY = "physical-security"
    ACCESS_CONTROL = "access-control"
    DATA_PRIVACY = "data-privacy-and-compliance"

    # Workplace & Front Desk Operations
    WORKPLACE_MANAGEMENT = "workplace-management"
    FRONT_DESK_OPERATIONS = "front-desk-operations"
    VISITOR_EXPERIENCE = "visitor-experience"

    # Technology & Innovation
    DIGITAL_TRANSFORMATION = "digital-transformation"
    AI_AND_OCR = "ai-and-ocr-technology"
    PRODUCT_UPDATES = "product-updates"

    # Resources & Case Studies
    INDUSTRY_INSIGHTS = "industry-insights"
    CASE_STUDIES = "case-studies"
    BEST_PRACTICES = "best-practices"


class CategoryNameEnum(str, Enum):
    PHYSICAL_SECURITY = "Physical Security"
    ACCESS_CONTROL = "Access Control"
    DATA_PRIVACY = "Data Privacy & Compliance"

    WORKPLACE_MANAGEMENT = "Workplace Management"
    FRONT_DESK_OPERATIONS = "Front Desk Operations"
    VISITOR_EXPERIENCE = "Visitor Experience"

    DIGITAL_TRANSFORMATION = "Digital Transformation"
    AI_AND_OCR = "AI & OCR Technology"
    PRODUCT_UPDATES = "Product Updates"

    INDUSTRY_INSIGHTS = "Industry Insights"
    CASE_STUDIES = "Case Studies"
    BEST_PRACTICES = "Best Practices"


CATEGORY_PAIRS: Dict[CategoryNameEnum, CategorySlugEnum] = {
    CategoryNameEnum.PHYSICAL_SECURITY: CategorySlugEnum.PHYSICAL_SECURITY,
    CategoryNameEnum.ACCESS_CONTROL: CategorySlugEnum.ACCESS_CONTROL,
    CategoryNameEnum.DATA_PRIVACY: CategorySlugEnum.DATA_PRIVACY,
    CategoryNameEnum.WORKPLACE_MANAGEMENT: CategorySlugEnum.WORKPLACE_MANAGEMENT,
    CategoryNameEnum.FRONT_DESK_OPERATIONS: CategorySlugEnum.FRONT_DESK_OPERATIONS,
    CategoryNameEnum.VISITOR_EXPERIENCE: CategorySlugEnum.VISITOR_EXPERIENCE,
    CategoryNameEnum.DIGITAL_TRANSFORMATION: CategorySlugEnum.DIGITAL_TRANSFORMATION,
    CategoryNameEnum.AI_AND_OCR: CategorySlugEnum.AI_AND_OCR,
    CategoryNameEnum.PRODUCT_UPDATES: CategorySlugEnum.PRODUCT_UPDATES,
    CategoryNameEnum.INDUSTRY_INSIGHTS: CategorySlugEnum.INDUSTRY_INSIGHTS,
    CategoryNameEnum.CASE_STUDIES: CategorySlugEnum.CASE_STUDIES,
    CategoryNameEnum.BEST_PRACTICES: CategorySlugEnum.BEST_PRACTICES,
}


class Category(BaseModel):
    imageUrl: Optional[str] = None
    itemIndex: Optional[int] = None
    name: CategoryNameEnum
    slug: CategorySlugEnum

    @model_validator(mode="after")
    def validate_pair(self) -> "Category":
        if CATEGORY_PAIRS[self.name] != self.slug:
            raise ValueError(
                f"Category '{self.name}' must use slug '{CATEGORY_PAIRS[self.name]}'"
            )
        return self


class ListOfCategories(BaseModel):
    listOfCategories: List[Category]
    totalItems: int


# ---------------------------------------------------------------------------
# Status / Type enums
# ---------------------------------------------------------------------------


class BlogStatus(str, Enum):
    published = "published"
    draft = "draft"


class BlogType(str, Enum):
    editors_pick = "editors pick"
    featured_story = "featured story"
    hero_section = "hero section"
    normal = "normal"


# Public (URL-friendly) variant used by the website's path filter.
class PublicBlogType(str, Enum):
    editors_pick = "editors-pick"
    featured_story = "featured"
    hero_section = "hero-section"
    normal = "normal"


class SortType(str, Enum):
    newest = "newest"
    oldest = "oldest"
    mostRecentlyUpdated = "mostRecentlyUpdated"
    leastRecentlyUpdated = "leastRecentlyUpdated"
    latestPublished = "latestPublished"
    earliestPublished = "earliestPublished"


class Pagination(BaseModel):
    currentPage: int
    totalPages: int


# ---------------------------------------------------------------------------
# BlockNote inline / block content models — Pydantic v2 port
# ---------------------------------------------------------------------------


class StyledText(BaseModel):
    type: Literal["text"]
    text: str
    bold: Optional[bool] = False
    italic: Optional[bool] = False
    underline: Optional[bool] = False
    strike: Optional[bool] = False
    styles: Optional[Dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_styles_shape(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if values.get("styles") is None:
            styles: Dict[str, Any] = {}
            for k in ("bold", "italic", "underline", "strike"):
                if k in values:
                    styles[k] = values.get(k)
            values["styles"] = styles if styles else None
        return values


class LinkInline(BaseModel):
    type: Literal["link"]
    content: List[StyledText]
    href: str


APIInlineContent = Annotated[
    Union[StyledText, LinkInline],
    Field(discriminator="type"),
]


class BaseBlock(BaseModel):
    """Permissive BlockNote block. Subclasses tighten the ``type`` literal."""

    id: Optional[str] = None
    type: str
    props: Optional[Dict[str, Any]] = None
    content: Optional[Union[str, List[APIInlineContent], Dict[str, Any]]] = None
    children: Optional[List["BaseBlock"]] = None
    align: Optional[Literal["left", "center", "right"]] = None

    model_config = {
        "extra": "allow",
        "populate_by_name": True,
    }

    @field_validator("children", mode="before")
    @classmethod
    def parse_children(cls, v: Any) -> Any:
        if v is None:
            return v
        parsed_children: List[BaseBlock] = []
        for c in v:
            if isinstance(c, BaseBlock):
                parsed_children.append(c)
            elif isinstance(c, dict):
                parsed_children.append(BaseBlock.model_validate(c))
            else:
                raise ValueError(
                    "children must be list of block dicts or BaseBlock instances"
                )
        return parsed_children


BaseBlock.model_rebuild()


class ParagraphBlock(BaseBlock):
    type: Literal["paragraph"]


class HeadingBlock(BaseBlock):
    type: Literal["heading"]
    level: Optional[int] = None


class QuoteBlock(BaseBlock):
    type: Literal["quote"]


class DividerBlock(BaseBlock):
    type: Literal["divider"]


class CodeBlock(BaseBlock):
    type: Literal["codeBlock"]


class ListItemBlock(BaseBlock):
    type: Literal[
        "bulletListItem",
        "numberedListItem",
        "checkListItem",
        "toggleListItem",
    ]


class ListBlock(BaseBlock):
    type: Literal["bulletList", "numberedList", "checkList", "toggleList"]


class TableContentRow(BaseModel):
    cells: List[Union[str, List[APIInlineContent], Dict[str, Any]]]


class TableContent(BaseModel):
    type: Literal["tableContent"]
    rows: List[TableContentRow]


class TableBlock(BaseBlock):
    type: Literal["table"]
    content: TableContent  # type: ignore[assignment]


class FileBlock(BaseBlock):
    type: Literal["file"]


class ImageBlock(BaseBlock):
    type: Literal["image"]


class VideoBlock(BaseBlock):
    type: Literal["video"]


class AudioBlock(BaseBlock):
    type: Literal["audio"]


BlockUnion = Annotated[
    Union[
        ParagraphBlock,
        HeadingBlock,
        QuoteBlock,
        DividerBlock,
        CodeBlock,
        ListItemBlock,
        ListBlock,
        TableBlock,
        FileBlock,
        ImageBlock,
        VideoBlock,
        AudioBlock,
        BaseBlock,  # permissive fallback
    ],
    Field(discriminator="type"),
]


def parse_block_dict(block_dict: Dict[str, Any]) -> BaseBlock:
    """Parse a single BlockNote block into the most specific model possible.

    Falls back to ``BaseBlock`` (extra="allow") when the discriminator
    doesn't match a known type so unknown block kinds round-trip without
    data loss.
    """

    try:
        # Discriminated unions in Pydantic v2 are validated via the
        # adapter pattern.
        from pydantic import TypeAdapter

        adapter: TypeAdapter[BaseBlock] = TypeAdapter(BlockUnion)
        return adapter.validate_python(block_dict)
    except ValidationError:
        base = BaseBlock.model_validate(block_dict)
        if base.children:
            base.children = [
                parse_block_dict(c) if isinstance(c, dict) else c for c in base.children
            ]
        return base


def parse_document(blocks_json: List[Dict[str, Any]]) -> List[BaseBlock]:
    return [parse_block_dict(b) for b in blocks_json]


# ---------------------------------------------------------------------------
# Page + SearchQuery
# ---------------------------------------------------------------------------


class Page(BaseModel):
    pageNumber: int
    pageBody: List[Dict[str, Any]]


class SearchQuery(BaseModel):
    title: Optional[str] = Field(None, description="Search blog titles")
    author: Optional[str] = Field(None, description="Search blog authors")
    start: Optional[int] = 0
    stop: Optional[int] = 100
