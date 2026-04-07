from __future__ import annotations

from schemas.imports import *
from pydantic import Field
import time


class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"
    VOID = "void"
    REFUNDED = "refunded"


class InvoiceLineItem(BaseModel):
    description: str
    quantity: int = 1
    unit_price_minor: int  # minor currency units (kobo/cents)
    total_minor: int
    metadata: Optional[dict] = None  # plan_id, discount_id refs


class InvoiceBase(BaseModel):
    tenant_id: str
    subscription_id: str
    invoice_number: str  # e.g. "INV-2026-000042"
    status: InvoiceStatus = InvoiceStatus.DRAFT
    billing_cycle: str  # "monthly" or "yearly"
    currency: str = "NGN"
    subtotal_minor: int  # before discounts
    discount_total_minor: int = 0
    tax_minor: int = 0
    total_minor: int  # final amount
    line_items: List[InvoiceLineItem] = Field(default_factory=list)
    payment_transaction_id: Optional[str] = None
    issued_at: Optional[int] = None
    paid_at: Optional[int] = None
    period_start: int
    period_end: int
    pdf_object_key: Optional[str] = None
    provider: Optional[str] = None  # "stripe" or "flutterwave"


class InvoiceCreate(InvoiceBase):
    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_on_create(self):
        if self.total_minor < 0:
            raise ValueError("total_minor must be non-negative")
        return self


class InvoiceUpdate(BaseModel):
    status: Optional[InvoiceStatus] = None
    payment_transaction_id: Optional[str] = None
    paid_at: Optional[int] = None
    pdf_object_key: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class InvoiceOut(InvoiceBase):
    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None
    pdf_url: Optional[str] = None  # presigned URL, resolved at retrieval

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
