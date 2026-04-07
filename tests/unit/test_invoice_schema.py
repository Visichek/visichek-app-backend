"""
Unit tests for invoice schema validation.
"""
from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from schemas.invoice_schema import (
    InvoiceCreate,
    InvoiceUpdate,
    InvoiceOut,
    InvoiceStatus,
    InvoiceLineItem,
)


class TestInvoiceLineItem:
    """Tests for InvoiceLineItem schema."""

    def test_line_item_valid(self):
        item = InvoiceLineItem(
            description="Professional Plan Monthly",
            quantity=1,
            unit_price_minor=10000,
            total_minor=10000,
        )
        assert item.description == "Professional Plan Monthly"
        assert item.quantity == 1
        assert item.unit_price_minor == 10000
        assert item.total_minor == 10000

    def test_line_item_with_metadata(self):
        item = InvoiceLineItem(
            description="Plan with discount",
            quantity=1,
            unit_price_minor=10000,
            total_minor=8000,
            metadata={
                "plan_id": "plan_123",
                "discount_id": "disc_456",
                "discount_amount": 2000,
            },
        )
        assert item.metadata["plan_id"] == "plan_123"
        assert item.metadata["discount_id"] == "disc_456"

    def test_line_item_multiple_quantity(self):
        item = InvoiceLineItem(
            description="Bulk users",
            quantity=10,
            unit_price_minor=1000,
            total_minor=10000,
        )
        assert item.quantity == 10
        assert item.total_minor == 10000


class TestInvoiceCreate:
    """Tests for InvoiceCreate schema."""

    def test_invoice_create_minimal(self):
        now = int(time.time())
        invoice = InvoiceCreate(
            tenant_id="tenant_123",
            subscription_id="sub_456",
            invoice_number="INV-2026-000001",
            billing_cycle="monthly",
            currency="NGN",
            subtotal_minor=50000,
            total_minor=50000,
            period_start=now,
            period_end=now + 2592000,
        )
        assert invoice.tenant_id == "tenant_123"
        assert invoice.subscription_id == "sub_456"
        assert invoice.invoice_number == "INV-2026-000001"
        assert invoice.status == InvoiceStatus.DRAFT
        assert invoice.total_minor == 50000
        assert invoice.date_created > 0

    def test_invoice_create_with_discount(self):
        now = int(time.time())
        invoice = InvoiceCreate(
            tenant_id="tenant_123",
            subscription_id="sub_456",
            invoice_number="INV-2026-000002",
            billing_cycle="monthly",
            currency="NGN",
            subtotal_minor=50000,
            discount_total_minor=5000,
            total_minor=45000,
            period_start=now,
            period_end=now + 2592000,
            line_items=[
                InvoiceLineItem(
                    description="Professional Plan",
                    quantity=1,
                    unit_price_minor=50000,
                    total_minor=50000,
                    metadata={"plan_id": "plan_123"},
                ),
            ],
        )
        assert invoice.discount_total_minor == 5000
        assert invoice.total_minor == 45000
        assert len(invoice.line_items) == 1

    def test_invoice_create_with_tax(self):
        now = int(time.time())
        invoice = InvoiceCreate(
            tenant_id="tenant_123",
            subscription_id="sub_456",
            invoice_number="INV-2026-000003",
            billing_cycle="monthly",
            currency="NGN",
            subtotal_minor=50000,
            tax_minor=5000,
            total_minor=55000,
            period_start=now,
            period_end=now + 2592000,
        )
        assert invoice.tax_minor == 5000
        assert invoice.total_minor == 55000

    def test_invoice_create_rejects_negative_total(self):
        now = int(time.time())
        with pytest.raises(ValidationError) as exc_info:
            InvoiceCreate(
                tenant_id="tenant_123",
                subscription_id="sub_456",
                invoice_number="INV-2026-000004",
                billing_cycle="monthly",
                currency="NGN",
                subtotal_minor=50000,
                total_minor=-1000,
                period_start=now,
                period_end=now + 2592000,
            )
        assert "total_minor must be non-negative" in str(exc_info.value)

    def test_invoice_create_with_payment_details(self):
        now = int(time.time())
        invoice = InvoiceCreate(
            tenant_id="tenant_123",
            subscription_id="sub_456",
            invoice_number="INV-2026-000005",
            status=InvoiceStatus.PAID,
            billing_cycle="monthly",
            currency="NGN",
            subtotal_minor=50000,
            total_minor=50000,
            period_start=now,
            period_end=now + 2592000,
            payment_transaction_id="txn_stripe_123",
            issued_at=now,
            paid_at=now + 3600,
            provider="stripe",
        )
        assert invoice.status == InvoiceStatus.PAID
        assert invoice.payment_transaction_id == "txn_stripe_123"
        assert invoice.provider == "stripe"

    def test_invoice_create_zero_total_valid(self):
        now = int(time.time())
        invoice = InvoiceCreate(
            tenant_id="tenant_123",
            subscription_id="sub_456",
            invoice_number="INV-2026-000006",
            billing_cycle="monthly",
            currency="NGN",
            subtotal_minor=0,
            total_minor=0,
            period_start=now,
            period_end=now + 2592000,
        )
        assert invoice.total_minor == 0


class TestInvoiceUpdate:
    """Tests for InvoiceUpdate schema."""

    def test_invoice_update_status(self):
        update = InvoiceUpdate(status=InvoiceStatus.ISSUED)
        assert update.status == InvoiceStatus.ISSUED

    def test_invoice_update_partial(self):
        now = int(time.time())
        update = InvoiceUpdate(
            status=InvoiceStatus.PAID,
            payment_transaction_id="txn_123",
            paid_at=now,
        )
        assert update.status == InvoiceStatus.PAID
        assert update.payment_transaction_id == "txn_123"

    def test_invoice_update_minimal(self):
        update = InvoiceUpdate()
        assert update.status is None
        assert update.payment_transaction_id is None
        assert update.last_updated > 0

    def test_invoice_update_void(self):
        update = InvoiceUpdate(status=InvoiceStatus.VOID)
        assert update.status == InvoiceStatus.VOID

    def test_invoice_update_refunded(self):
        now = int(time.time())
        update = InvoiceUpdate(
            status=InvoiceStatus.REFUNDED,
            paid_at=now,
        )
        assert update.status == InvoiceStatus.REFUNDED


class TestInvoiceOut:
    """Tests for InvoiceOut schema (response schema)."""

    def test_invoice_out_basic(self):
        now = int(time.time())
        invoice_data = {
            "_id": "invoice_789",
            "tenant_id": "tenant_123",
            "subscription_id": "sub_456",
            "invoice_number": "INV-2026-000007",
            "status": InvoiceStatus.ISSUED.value,
            "billing_cycle": "monthly",
            "currency": "NGN",
            "subtotal_minor": 50000,
            "discount_total_minor": 0,
            "tax_minor": 0,
            "total_minor": 50000,
            "line_items": [],
            "period_start": now,
            "period_end": now + 2592000,
            "date_created": now,
            "last_updated": now,
        }
        invoice = InvoiceOut(**invoice_data)
        assert invoice.id == "invoice_789"
        assert invoice.invoice_number == "INV-2026-000007"

    def test_invoice_out_objectid_conversion(self):
        from bson import ObjectId

        now = int(time.time())
        object_id = ObjectId()
        invoice_data = {
            "_id": object_id,
            "tenant_id": "tenant_123",
            "subscription_id": "sub_456",
            "invoice_number": "INV-2026-000008",
            "status": InvoiceStatus.ISSUED.value,
            "billing_cycle": "monthly",
            "currency": "NGN",
            "subtotal_minor": 50000,
            "total_minor": 50000,
            "period_start": now,
            "period_end": now + 2592000,
            "date_created": now,
            "last_updated": now,
        }
        invoice = InvoiceOut(**invoice_data)
        assert isinstance(invoice.id, str)
        assert invoice.id == str(object_id)

    def test_invoice_out_with_pdf_url(self):
        now = int(time.time())
        invoice_data = {
            "_id": "invoice_790",
            "tenant_id": "tenant_123",
            "subscription_id": "sub_456",
            "invoice_number": "INV-2026-000009",
            "status": InvoiceStatus.PAID.value,
            "billing_cycle": "monthly",
            "currency": "NGN",
            "subtotal_minor": 50000,
            "total_minor": 50000,
            "period_start": now,
            "period_end": now + 2592000,
            "date_created": now,
            "last_updated": now,
            "pdf_url": "https://storage.example.com/invoices/inv_790.pdf",
        }
        invoice = InvoiceOut(**invoice_data)
        assert invoice.pdf_url == "https://storage.example.com/invoices/inv_790.pdf"

    def test_invoice_status_enum_values(self):
        assert InvoiceStatus.DRAFT.value == "draft"
        assert InvoiceStatus.ISSUED.value == "issued"
        assert InvoiceStatus.PAID.value == "paid"
        assert InvoiceStatus.VOID.value == "void"
        assert InvoiceStatus.REFUNDED.value == "refunded"
