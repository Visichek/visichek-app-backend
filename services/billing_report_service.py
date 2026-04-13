from __future__ import annotations

import logging
import time


from core.database import db

logger = logging.getLogger(__name__)

SUBSCRIPTIONS_COLLECTION = "subscriptions"
INVOICES_COLLECTION = "invoices"
PAYMENTS_COLLECTION = "payment_transactions"


async def get_billing_summary(start_date: int, end_date: int) -> dict:
    """
    Get billing summary across all tenants for a date range.

    Uses MongoDB aggregation pipeline to compute:
    - total_revenue_minor: Sum of all invoice amounts in minor units (cents)
    - invoice_count: Total number of invoices
    - new_subscriptions: Count of subscriptions created in period
    - cancelled_subscriptions: Count of subscriptions cancelled in period
    - active_subscriptions: Count of currently active subscriptions
    - mrr_minor: Estimated monthly recurring revenue in minor units

    Args:
        start_date: Unix timestamp start (inclusive)
        end_date: Unix timestamp end (inclusive)

    Returns:
        Dict with billing metrics
    """
    try:
        logger.info(f"Generating billing summary for {start_date} to {end_date}")

        # Aggregate invoices for total revenue
        revenue_pipeline = [
            {
                "$match": {
                    "created_at": {"$gte": start_date, "$lte": end_date},
                    "status": {"$in": ["paid", "sent"]},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total_revenue_minor": {"$sum": "$total_minor"},
                    "invoice_count": {"$sum": 1},
                }
            },
        ]

        revenue_result = await db[INVOICES_COLLECTION].aggregate(revenue_pipeline).to_list(None)
        revenue_stats = revenue_result[0] if revenue_result else {"total_revenue_minor": 0, "invoice_count": 0}

        # Count new subscriptions in period
        new_subs_pipeline = [
            {
                "$match": {
                    "date_created": {"$gte": start_date, "$lte": end_date},
                }
            },
            {
                "$count": "count",
            },
        ]

        new_subs_result = await db[SUBSCRIPTIONS_COLLECTION].aggregate(new_subs_pipeline).to_list(None)
        new_subscriptions = new_subs_result[0]["count"] if new_subs_result else 0

        # Count cancelled subscriptions in period
        cancelled_subs_pipeline = [
            {
                "$match": {
                    "cancelled_at": {"$gte": start_date, "$lte": end_date},
                    "status": "cancelled",
                }
            },
            {
                "$count": "count",
            },
        ]

        cancelled_subs_result = await db[SUBSCRIPTIONS_COLLECTION].aggregate(cancelled_subs_pipeline).to_list(None)
        cancelled_subscriptions = cancelled_subs_result[0]["count"] if cancelled_subs_result else 0

        # Count active subscriptions (point-in-time at end_date)
        active_subs_pipeline = [
            {
                "$match": {
                    "status": "active",
                    "date_created": {"$lte": end_date},
                    "$or": [
                        {"cancelled_at": None},
                        {"cancelled_at": {"$gt": end_date}},
                    ],
                }
            },
            {
                "$count": "count",
            },
        ]

        active_subs_result = await db[SUBSCRIPTIONS_COLLECTION].aggregate(active_subs_pipeline).to_list(None)
        active_subscriptions = active_subs_result[0]["count"] if active_subs_result else 0

        # Estimate MRR from active monthly subscriptions
        mrr_pipeline = [
            {
                "$match": {
                    "status": "active",
                    "billing_cycle": "monthly",
                    "date_created": {"$lte": end_date},
                    "$or": [
                        {"cancelled_at": None},
                        {"cancelled_at": {"$gt": end_date}},
                    ],
                }
            },
            {
                "$group": {
                    "_id": None,
                    "mrr_minor": {"$sum": {"$multiply": ["$effective_price", 100]}},
                }
            },
        ]

        mrr_result = await db[SUBSCRIPTIONS_COLLECTION].aggregate(mrr_pipeline).to_list(None)
        mrr_minor = int(mrr_result[0]["mrr_minor"]) if mrr_result else 0

        # Add 1/12 of annual subscriptions to MRR estimate
        mrr_annual_pipeline = [
            {
                "$match": {
                    "status": "active",
                    "billing_cycle": "yearly",
                    "date_created": {"$lte": end_date},
                    "$or": [
                        {"cancelled_at": None},
                        {"cancelled_at": {"$gt": end_date}},
                    ],
                }
            },
            {
                "$group": {
                    "_id": None,
                    "annual_minor": {"$sum": {"$multiply": ["$effective_price", 100]}},
                }
            },
        ]

        mrr_annual_result = await db[SUBSCRIPTIONS_COLLECTION].aggregate(mrr_annual_pipeline).to_list(None)
        annual_minor = int(mrr_annual_result[0]["annual_minor"]) if mrr_annual_result else 0
        mrr_minor += int(annual_minor / 12)

        result = {
            "period": {
                "start": start_date,
                "end": end_date,
            },
            "total_revenue_minor": int(revenue_stats.get("total_revenue_minor", 0)),
            "invoice_count": int(revenue_stats.get("invoice_count", 0)),
            "new_subscriptions": new_subscriptions,
            "cancelled_subscriptions": cancelled_subscriptions,
            "active_subscriptions": active_subscriptions,
            "mrr_minor": mrr_minor,
            "generated_at": int(time.time()),
        }

        logger.info(f"Billing summary generated: {result}")
        return result

    except Exception as e:
        logger.error(f"Error generating billing summary: {str(e)}", exc_info=True)
        raise


async def get_payment_discrepancies() -> list[dict]:
    """
    Cross-reference active subscriptions and invoices to find discrepancies.

    Detects:
    1. Active subscriptions with no invoice in their current billing period
    2. Successful payments with no matching invoice

    Returns:
        List of discrepancy dicts with structure:
        {
            "type": "missing_invoice" | "orphaned_payment",
            "subscription_id": str or None,
            "payment_id": str or None,
            "tenant_id": str,
            "issue_description": str,
            "detected_at": int,
        }
    """
    try:
        logger.info("Checking for payment discrepancies")
        now = int(time.time())
        discrepancies = []

        # 1. Find active subscriptions with missing invoices in current period
        missing_invoice_pipeline = [
            {
                "$match": {
                    "status": "active",
                    "cancelled_at": None,
                }
            },
            {
                "$lookup": {
                    "from": INVOICES_COLLECTION,
                    "let": {
                        "sub_id": "$_id",
                        "period_start": "$current_period_start",
                        "period_end": "$current_period_end",
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$subscription_id", "$$sub_id"]},
                                        {
                                            "$gte": [
                                                "$period_start",
                                                "$$period_start",
                                            ]
                                        },
                                        {
                                            "$lte": [
                                                "$period_start",
                                                "$$period_end",
                                            ]
                                        },
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "invoices",
                }
            },
            {
                "$match": {
                    "invoices": {"$eq": []},
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "tenant_id": 1,
                    "current_period_start": 1,
                    "current_period_end": 1,
                    "effective_price": 1,
                    "last_renewal_attempt_at": 1,
                }
            },
        ]

        missing_invoices = await db[SUBSCRIPTIONS_COLLECTION].aggregate(missing_invoice_pipeline).to_list(None)

        for sub in missing_invoices:
            discrepancies.append(
                {
                    "type": "missing_invoice",
                    "subscription_id": str(sub["_id"]),
                    "payment_id": None,
                    "tenant_id": sub.get("tenant_id"),
                    "issue_description": (
                        f"Active subscription {str(sub['_id'])} has no invoice in period "
                        f"{sub.get('current_period_start')} to {sub.get('current_period_end')}"
                    ),
                    "detected_at": now,
                }
            )

        # 2. Find successful payments with no matching invoice
        orphaned_payment_pipeline = [
            {
                "$match": {
                    "status": "succeeded",
                }
            },
            {
                "$lookup": {
                    "from": INVOICES_COLLECTION,
                    "let": {"payment_id": "$_id"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$eq": ["$payment_transaction_id", "$$payment_id"]
                                }
                            }
                        }
                    ],
                    "as": "invoices",
                }
            },
            {
                "$match": {
                    "invoices": {"$eq": []},
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "owner_id": 1,
                    "amount_minor": 1,
                    "reference": 1,
                    "created_at": 1,
                }
            },
        ]

        orphaned_payments = await db[PAYMENTS_COLLECTION].aggregate(orphaned_payment_pipeline).to_list(None)

        for payment in orphaned_payments:
            discrepancies.append(
                {
                    "type": "orphaned_payment",
                    "subscription_id": None,
                    "payment_id": str(payment["_id"]),
                    "tenant_id": payment.get("owner_id"),
                    "issue_description": (
                        f"Successful payment {str(payment['_id'])} "
                        f"({payment.get('amount_minor')} minor units) has no matching invoice. "
                        f"Reference: {payment.get('reference')}"
                    ),
                    "detected_at": now,
                }
            )

        logger.info(f"Found {len(discrepancies)} payment discrepancies")
        return discrepancies

    except Exception as e:
        logger.error(f"Error checking payment discrepancies: {str(e)}", exc_info=True)
        raise


async def get_subscription_by_period(
    tenant_id: str,
    period_start: int,
    period_end: int,
) -> dict:
    """
    Get subscription stats for a specific tenant and period.

    Args:
        tenant_id: Tenant ID
        period_start: Start timestamp
        period_end: End timestamp

    Returns:
        Dict with subscription metrics for the period
    """
    try:
        now = int(time.time())

        # Count subscriptions created
        new_count = await db[SUBSCRIPTIONS_COLLECTION].count_documents({
            "tenant_id": tenant_id,
            "date_created": {"$gte": period_start, "$lte": period_end},
        })

        # Count subscriptions cancelled
        cancelled_count = await db[SUBSCRIPTIONS_COLLECTION].count_documents({
            "tenant_id": tenant_id,
            "cancelled_at": {"$gte": period_start, "$lte": period_end},
        })

        # Count active subscriptions
        active_count = await db[SUBSCRIPTIONS_COLLECTION].count_documents({
            "tenant_id": tenant_id,
            "status": "active",
            "date_created": {"$lte": now},
            "cancelled_at": None,
        })

        # Sum revenue for period
        revenue_pipeline = [
            {
                "$match": {
                    "tenant_id": tenant_id,
                    "created_at": {"$gte": period_start, "$lte": period_end},
                    "status": {"$in": ["paid", "sent"]},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": "$total_minor"},
                }
            },
        ]

        revenue_result = await db[INVOICES_COLLECTION].aggregate(revenue_pipeline).to_list(None)
        total_revenue = int(revenue_result[0]["total"]) if revenue_result else 0

        return {
            "tenant_id": tenant_id,
            "period": {
                "start": period_start,
                "end": period_end,
            },
            "new_subscriptions": new_count,
            "cancelled_subscriptions": cancelled_count,
            "active_subscriptions": active_count,
            "total_revenue_minor": total_revenue,
            "generated_at": int(time.time()),
        }

    except Exception as e:
        logger.error(f"Error getting subscription period stats: {str(e)}", exc_info=True)
        raise
