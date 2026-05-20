from __future__ import annotations

from schemas.imports import *


# ── Overlay (persisted) ───────────────────────────────────────────────


def _normalize_text(value: Optional[str]) -> Optional[str]:
    """Strip surrounding whitespace; collapse empty → None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_required(value: str, *, field: str) -> str:
    stripped = (value or "").strip()
    if not stripped:
        raise ValueError(f"{field} must not be empty or whitespace-only")
    return stripped


def normalize_question_key(question: str) -> str:
    """Canonical form of a question for duplicate detection.

    Case-insensitive, whitespace-normalised. Trailing punctuation
    (eg ``?``) is preserved so "How does X work?" and "How does X
    work" are NOT considered the same — they aren't.
    """
    return " ".join((question or "").lower().split())


class FaqItem(BaseModel):
    """One question + answer. Keyed by ``item_key`` for upsert merge.

    ``question`` is the human-facing primary identifier — duplicate
    questions (case-insensitive, whitespace-normalised) are rejected
    by the service layer on PATCH.

    Leading / trailing whitespace is stripped from every text field
    on entry so admins can paste freely without producing duplicate-
    looking rows. Empty strings are rejected for required fields.
    """

    item_key: str  # short slug — eg "billing-how-it-works"
    question: str
    answer: str
    category_key: Optional[str] = (
        None  # null → falls into the default "general" section
    )
    sort_order: int = 0

    @model_validator(mode="after")
    def _strip_and_validate(self):
        # Pydantic v2 model_validator runs after field validation.
        # Rebuild stripped values; avoid `self.x = ...` (frozen-by-default
        # arrangement is forgiving here, but use object.__setattr__ for
        # safety against future model config changes).
        object.__setattr__(
            self, "item_key", _normalize_required(self.item_key, field="item_key")
        )
        object.__setattr__(
            self, "question", _normalize_required(self.question, field="question")
        )
        object.__setattr__(
            self, "answer", _normalize_required(self.answer, field="answer")
        )
        object.__setattr__(self, "category_key", _normalize_text(self.category_key))
        return self


class FaqCategory(BaseModel):
    """Section header that groups items."""

    category_key: str
    label: str
    sort_order: int = 0

    @model_validator(mode="after")
    def _strip_and_validate(self):
        object.__setattr__(
            self,
            "category_key",
            _normalize_required(self.category_key, field="category_key"),
        )
        object.__setattr__(
            self, "label", _normalize_required(self.label, field="label")
        )
        return self


class FaqOverlayBase(BaseModel):
    """Persisted FAQ overlay — singleton document."""

    headline: Optional[str] = None
    subheadline: Optional[str] = None
    # HTML / markdown block rendered under the FAQ list. Use it for
    # the "have an infrequently asked question?" footer with contact
    # info, support email, etc.
    footer_html: Optional[str] = None
    items: List[FaqItem] = Field(default_factory=list)
    categories: List[FaqCategory] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_hero_copy(self):
        object.__setattr__(self, "headline", _normalize_text(self.headline))
        object.__setattr__(self, "subheadline", _normalize_text(self.subheadline))
        # Preserve internal whitespace in HTML but trim outer pad.
        footer = self.footer_html
        if footer is not None:
            footer = footer.strip() or None
        object.__setattr__(self, "footer_html", footer)
        return self


class FaqOverlayCreate(FaqOverlayBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class FaqOverlayPatch(BaseModel):
    """Partial PATCH payload.

    ``items`` and ``categories`` lists merge by natural key
    (``item_key`` / ``category_key``): matching keys upsert in-place,
    new keys append, empty list clears, omitted field leaves the
    list untouched.
    """

    headline: Optional[str] = None
    subheadline: Optional[str] = None
    footer_html: Optional[str] = None
    items: Optional[List[FaqItem]] = None
    categories: Optional[List[FaqCategory]] = None

    @model_validator(mode="after")
    def _normalize_and_dedupe(self):
        object.__setattr__(self, "headline", _normalize_text(self.headline))
        object.__setattr__(self, "subheadline", _normalize_text(self.subheadline))
        footer = self.footer_html
        if footer is not None:
            footer = footer.strip() or None
            object.__setattr__(self, "footer_html", footer)

        # Reject duplicate questions WITHIN the patch payload.
        # Cross-payload uniqueness (vs the persisted overlay) is checked
        # in the service layer where we have access to the current state.
        if self.items:
            seen: dict[str, str] = {}
            for item in self.items:
                qkey = normalize_question_key(item.question)
                if qkey in seen and seen[qkey] != item.item_key:
                    raise ValueError(
                        f"Duplicate question in payload: '{item.question}' "
                        f"appears under itemKeys '{seen[qkey]}' and "
                        f"'{item.item_key}'"
                    )
                seen[qkey] = item.item_key
        return self


class FaqOverlayOut(FaqOverlayBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None

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


# ── Rendered response (GET /v1/faqs) ─────────────────────────────────


class FaqRenderedItem(BaseModel):
    item_key: str
    question: str
    answer: str
    sort_order: int = 0


class FaqRenderedSection(BaseModel):
    category_key: str
    label: str
    sort_order: int = 0
    items: List[FaqRenderedItem]


class FaqOut(BaseModel):
    headline: Optional[str] = None
    subheadline: Optional[str] = None
    footer_html: Optional[str] = None
    sections: List[FaqRenderedSection]
    last_updated: int


# ── Row delete (DELETE /v1/faqs/{kind}/{key}) ────────────────────────


class FaqOverlayRowKind(str, Enum):
    ITEM = "item"
    CATEGORY = "category"
