from __future__ import annotations

from schemas.imports import *


# ── Overlay (persisted) ───────────────────────────────────────────────


class PricingPlanCopy(BaseModel):
    """Per-plan marketing copy. Keyed by ``plan_name`` (plan slug)."""

    plan_name: str
    tagline: Optional[str] = None
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    # None means "use the ship-with-code default bullets". An empty
    # list ([]) means "admin explicitly wants no bullets". A populated
    # list overrides the defaults.
    highlight_bullets: Optional[List[str]] = None
    badge: Optional[str] = None


class PricingFeatureCopy(BaseModel):
    """Per-row marketing copy. Keyed by ``row_key``.

    ``row_key`` is the stable id for one comparison row (see
    ``services.pricing_marketing_service.ROW_KEY_*`` patterns).
    The overlay only owns the row label, description, and which
    category section it appears under — never the values.
    """

    row_key: str
    label: Optional[str] = None
    description: Optional[str] = None
    category_key: Optional[str] = None


class PricingCategoryCopy(BaseModel):
    """Comparison-table section. Keyed by ``category_key``."""

    category_key: str
    label: str
    sort_order: int = 0


class PricingMarketingOverlayBase(BaseModel):
    """Persisted marketing overlay — the singleton document."""

    headline: Optional[str] = None
    subheadline: Optional[str] = None
    currency_display: Optional[str] = None  # eg "$" / "₦" — purely cosmetic
    plans: List[PricingPlanCopy] = Field(default_factory=list)
    features: List[PricingFeatureCopy] = Field(default_factory=list)
    categories: List[PricingCategoryCopy] = Field(default_factory=list)


class PricingMarketingOverlayCreate(PricingMarketingOverlayBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class PricingMarketingOverlayPatch(BaseModel):
    """Partial PATCH payload.

    ``plans`` / ``features`` / ``categories`` lists are merged by their
    natural key (``plan_name`` / ``row_key`` / ``category_key``): items
    with a matching key are upserted, new keys are appended. Pass an
    empty list to clear a section entirely; omit the field to leave it
    untouched.
    """

    headline: Optional[str] = None
    subheadline: Optional[str] = None
    currency_display: Optional[str] = None
    plans: Optional[List[PricingPlanCopy]] = None
    features: Optional[List[PricingFeatureCopy]] = None
    categories: Optional[List[PricingCategoryCopy]] = None


class PricingMarketingOverlayOut(PricingMarketingOverlayBase):
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


# ── Rendered response (GET /v1/pricing-marketing) ────────────────────


class PricingPlanCard(BaseModel):
    """One card in the summary pricing table."""

    plan_id: str
    plan_name: str
    display_name: str
    tier: str
    tagline: Optional[str] = None
    # ``None`` for the Enterprise card — render "Contact sales".
    price_monthly: Optional[float] = None
    price_yearly: Optional[float] = None
    currency: str
    cta_label: str
    cta_url: Optional[str] = None
    badge: Optional[str] = None
    highlight_bullets: List[str] = Field(default_factory=list)
    sort_order: int = 0


class PricingComparisonCell(BaseModel):
    """One (row × plan) cell. ``display`` is what the FE renders."""

    plan_name: str
    # Raw value as it came off the plan doc. ``bool`` for flag rows,
    # ``int`` for caps/quotas, ``str`` for support tier / sla / trial,
    # ``None`` when the plan didn't surface a value.
    value: Any = None
    display: str  # "✓", "—", "25", "Unlimited", "Custom", "4h", "Priority"


class PricingComparisonRow(BaseModel):
    row_key: str
    label: str
    description: Optional[str] = None
    cells: List[PricingComparisonCell]  # one per card, same order as plans[]


class PricingComparisonSection(BaseModel):
    category_key: str
    label: str
    sort_order: int = 0
    rows: List[PricingComparisonRow]


class PricingMarketingOut(BaseModel):
    """Full rendered marketing page payload."""

    headline: Optional[str] = None
    subheadline: Optional[str] = None
    currency: str
    plans: List[PricingPlanCard]
    sections: List[PricingComparisonSection]
    last_updated: int


# ── Row delete (DELETE /v1/pricing-marketing/{kind}/{key}) ───────────


class PricingMarketingOverlayRowKind(str, Enum):
    PLAN = "plan"
    FEATURE = "feature"
    CATEGORY = "category"
