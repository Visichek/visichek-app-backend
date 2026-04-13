"""
Unit tests for billing report service with mocked database aggregations.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

pytestmark = pytest.mark.asyncio


class TestBillingReportService:
    """Tests for billing report service functions."""

    @patch("services.billing_report_service.db")
    async def test_get_billing_summary_empty_collections(self, mock_db):
        """Test billing summary with empty collections."""
        mock_invoices = AsyncMock()
        mock_subscriptions = AsyncMock()

        # Mock aggregation returns for empty collections
        async def mock_aggregate_empty(*args, **kwargs):
            mock_cursor = AsyncMock()
            mock_cursor.to_list.return_value = []
            return mock_cursor

        mock_invoices.aggregate = mock_aggregate_empty
        mock_subscriptions.aggregate = mock_aggregate_empty

        mock_db.__getitem__ = MagicMock(
            side_effect=lambda x: {
                "invoices": mock_invoices,
                "subscriptions": mock_subscriptions,
            }[x]
        )

        from services.billing_report_service import get_billing_summary

        now = int(time.time())
        start = now - 2592000  # 30 days ago
        end = now

        result = await get_billing_summary(start, end)

        assert result["total_revenue_minor"] == 0
        assert result["invoice_count"] == 0
        assert result["new_subscriptions"] == 0
        assert result["cancelled_subscriptions"] == 0
        assert result["active_subscriptions"] == 0
        assert result["mrr_minor"] == 0
        assert result["period"]["start"] == start
        assert result["period"]["end"] == end

    @patch("services.billing_report_service.db")
    async def test_get_billing_summary_with_revenue(self, mock_db):
        """Test billing summary with revenue data."""
        now = int(time.time())
        start = now - 2592000
        end = now

        # Create mock database
        mock_invoices = AsyncMock()
        mock_subscriptions = AsyncMock()

        # Mock revenue aggregation
        async def mock_invoice_aggregate(*args, **kwargs):
            mock_cursor = AsyncMock()
            mock_cursor.to_list.return_value = [
                {
                    "_id": None,
                    "total_revenue_minor": 500000,
                    "invoice_count": 10,
                }
            ]
            return mock_cursor

        # Mock subscription aggregations
        async def mock_sub_aggregate(*args, **kwargs):
            pipeline = args[0] if args else kwargs.get("pipeline", [])
            mock_cursor = AsyncMock()

            # Return different results based on pipeline
            if pipeline and "$count" in str(pipeline):
                mock_cursor.to_list.return_value = [{"count": 5}]
            elif pipeline and "$group" in str(pipeline):
                # MRR query
                mock_cursor.to_list.return_value = [{"mrr_minor": 50000}]
            else:
                mock_cursor.to_list.return_value = [{"count": 20}]

            return mock_cursor

        mock_invoices.aggregate = mock_invoice_aggregate
        mock_subscriptions.aggregate = mock_sub_aggregate

        mock_db.__getitem__ = MagicMock(
            side_effect=lambda x: {
                "invoices": mock_invoices,
                "subscriptions": mock_subscriptions,
            }[x]
        )

        from services.billing_report_service import get_billing_summary

        result = await get_billing_summary(start, end)

        assert result["total_revenue_minor"] == 500000
        assert result["invoice_count"] == 10
        assert "new_subscriptions" in result
        assert "mrr_minor" in result
        assert result["generated_at"] > 0

    @patch("services.billing_report_service.db")
    async def test_get_billing_summary_calculates_annual_mrr(self, mock_db):
        """Test that annual subscriptions are included in MRR calculation (1/12)."""
        now = int(time.time())
        start = now - 2592000
        end = now

        mock_invoices = AsyncMock()
        mock_subscriptions = AsyncMock()

        async def mock_invoice_aggregate(*args, **kwargs):
            mock_cursor = AsyncMock()
            mock_cursor.to_list.return_value = [
                {
                    "_id": None,
                    "total_revenue_minor": 0,
                    "invoice_count": 0,
                }
            ]
            return mock_cursor

        call_count = 0

        async def mock_sub_aggregate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_cursor = AsyncMock()

            if call_count == 1:  # new subscriptions count
                mock_cursor.to_list.return_value = [{"count": 0}]
            elif call_count == 2:  # cancelled subscriptions count
                mock_cursor.to_list.return_value = [{"count": 0}]
            elif call_count == 3:  # active subscriptions count
                mock_cursor.to_list.return_value = [{"count": 0}]
            elif call_count == 4:  # monthly MRR
                mock_cursor.to_list.return_value = [{"mrr_minor": 1200000}]
            elif call_count == 5:  # annual subscriptions (for MRR 1/12)
                mock_cursor.to_list.return_value = [{"annual_minor": 1200000}]
            else:
                mock_cursor.to_list.return_value = []

            return mock_cursor

        mock_invoices.aggregate = mock_invoice_aggregate
        mock_subscriptions.aggregate = mock_sub_aggregate

        mock_db.__getitem__ = MagicMock(
            side_effect=lambda x: {
                "invoices": mock_invoices,
                "subscriptions": mock_subscriptions,
            }[x]
        )

        from services.billing_report_service import get_billing_summary

        result = await get_billing_summary(start, end)

        # MRR should be monthly (1200000) + annual/12 (100000) = 1300000
        assert result["mrr_minor"] == 1300000

    @patch("services.billing_report_service.db")
    async def test_get_subscription_by_period(self, mock_db):
        """Test tenant-scoped subscription period stats."""
        now = int(time.time())
        start = now - 2592000
        end = now
        tenant_id = "tenant_test_123"

        mock_subscriptions = AsyncMock()
        mock_invoices = AsyncMock()

        # Mock count_documents
        mock_subscriptions.count_documents = AsyncMock(side_effect=[2, 1, 5])

        # Mock invoice aggregation
        async def mock_invoice_aggregate(*args, **kwargs):
            mock_cursor = AsyncMock()
            mock_cursor.to_list.return_value = [{"total": 100000}]
            return mock_cursor

        mock_invoices.aggregate = mock_invoice_aggregate

        mock_db.__getitem__ = MagicMock(
            side_effect=lambda x: {
                "subscriptions": mock_subscriptions,
                "invoices": mock_invoices,
            }[x]
        )

        from services.billing_report_service import get_subscription_by_period

        result = await get_subscription_by_period(tenant_id, start, end)

        assert result["tenant_id"] == tenant_id
        assert result["new_subscriptions"] == 2
        assert result["cancelled_subscriptions"] == 1
        assert result["active_subscriptions"] == 5
        assert result["total_revenue_minor"] == 100000

    @patch("services.billing_report_service.db")
    async def test_get_payment_discrepancies_missing_invoices(self, mock_db):
        """Test detection of active subscriptions with missing invoices."""
        mock_subscriptions = AsyncMock()

        async def mock_aggregate(*args, **kwargs):
            mock_cursor = AsyncMock()
            # Return one subscription with missing invoice
            mock_cursor.to_list.return_value = [
                {
                    "_id": "sub_missing_invoice",
                    "tenant_id": "tenant_123",
                    "current_period_start": 1704067200,
                    "current_period_end": 1706745600,
                    "effective_price": 100.0,
                    "last_renewal_attempt_at": 1704067200,
                }
            ]
            return mock_cursor

        mock_subscriptions.aggregate = mock_aggregate

        mock_db.__getitem__ = MagicMock(return_value=mock_subscriptions)

        from services.billing_report_service import get_payment_discrepancies

        result = await get_payment_discrepancies()

        # Should find at least the missing invoice
        assert any(d["type"] == "missing_invoice" for d in result)

    @patch("services.billing_report_service.db")
    async def test_get_payment_discrepancies_empty(self, mock_db):
        """Test payment discrepancies detection with no issues."""
        mock_subscriptions = AsyncMock()
        mock_payments = AsyncMock()

        async def mock_aggregate_empty(*args, **kwargs):
            mock_cursor = AsyncMock()
            mock_cursor.to_list.return_value = []
            return mock_cursor

        mock_subscriptions.aggregate = mock_aggregate_empty
        mock_payments.aggregate = mock_aggregate_empty

        mock_db.__getitem__ = MagicMock(
            side_effect=lambda x: {
                "subscriptions": mock_subscriptions,
                "payment_transactions": mock_payments,
            }[x]
        )

        from services.billing_report_service import get_payment_discrepancies

        result = await get_payment_discrepancies()

        assert len(result) == 0

    @patch("services.billing_report_service.db")
    async def test_get_billing_summary_error_handling(self, mock_db):
        """Test error handling in billing summary."""
        mock_db.__getitem__.side_effect = RuntimeError("Database error")

        from services.billing_report_service import get_billing_summary

        now = int(time.time())
        with pytest.raises(RuntimeError):
            await get_billing_summary(now - 2592000, now)
