from __future__ import annotations

import logging
import time
from typing import Optional

from bson import ObjectId

from core.database import db
from core.payments import PaymentIntentRequest, PaymentManager
from core.queue import QueueManager
from core.settings import get_settings
from repositories.subscription_repo import (
    get_subscriptions,
    update_subscription,
)
from repositories.plan_repo import get_plan
from schemas.subscription_schema import (
    SubscriptionStatus,
    SubscriptionUpdate,
    SubscriptionOut,
)
from services.invoice_service import generate_invoice
from services.plan_cache_service import invalidate_tenant_plan_cache

logger = logging.getLogger(__name__)


async def process_dunning() -> dict:
    """
    Called by APScheduler every 6 hours.
    Processes subscriptions in PAST_DUE status for retry or suspension.

    Returns dict with counts of retried, suspended, and failed subscriptions.
    """
    now = int(time.time())
    logger.info("Starting dunning process")

    settings = get_settings()
    retried_count = 0
    suspended_count = 0
    failed_count = 0

    try:
        # Query subscriptions due for dunning retry
        filter_dict = {
            "status": SubscriptionStatus.PAST_DUE,
            "next_retry_at": {"$lte": now},
        }

        subscriptions = await get_subscriptions(filter_dict=filter_dict, start=0, stop=1000)

        for subscription in subscriptions:
            try:
                # Check if max dunning attempts reached
                if subscription.renewal_attempts >= settings.max_dunning_attempts:
                    # Suspend the subscription
                    await _suspend_subscription(subscription, now)
                    await _queue_suspension_email(subscription)
                    suspended_count += 1
                else:
                    # Retry payment
                    success = await _retry_payment(subscription, now)
                    if success:
                        retried_count += 1
                    else:
                        failed_count += 1

            except Exception as e:
                logger.error(
                    f"Dunning error for subscription {subscription.id}: {str(e)}",
                    exc_info=True,
                )
                failed_count += 1

        logger.info(
            f"Dunning process complete: {retried_count} retried, "
            f"{suspended_count} suspended, {failed_count} failed"
        )
        return {
            "retried_count": retried_count,
            "suspended_count": suspended_count,
            "failed_count": failed_count,
            "total_processed": retried_count + suspended_count + failed_count,
        }

    except Exception as e:
        logger.error(f"Fatal error in dunning batch: {str(e)}", exc_info=True)
        return {
            "retried_count": retried_count,
            "suspended_count": suspended_count,
            "failed_count": failed_count,
            "error": str(e),
        }


async def _retry_payment(subscription: SubscriptionOut, now: int) -> bool:
    """
    Internal function to retry payment for a PAST_DUE subscription.

    On success: resets to ACTIVE, generates invoice.
    On failure: increments attempts and schedules next retry.

    Returns True on success, False on failure.
    """
    logger.info(f"Retrying payment for subscription {subscription.id}")

    try:
        # Fetch plan
        if not ObjectId.is_valid(subscription.plan_id):
            logger.error(f"Invalid plan_id {subscription.plan_id}")
            return False

        plan = await get_plan({"_id": ObjectId(subscription.plan_id)})
        if not plan:
            logger.error(f"Plan not found: {subscription.plan_id}")
            return False

        # Get payment manager
        try:
            payment_manager = PaymentManager.get_instance()
        except RuntimeError as e:
            logger.error(f"Payment manager not configured: {str(e)}")
            return False

        provider = payment_manager.get_provider()

        # Create payment intent
        amount_minor = int(subscription.effective_price * 100)
        reference = f"dunning-{subscription.id}-{now}"

        try:
            intent = provider.create_intent(
                PaymentIntentRequest(
                    amount_minor=amount_minor,
                    currency=subscription.currency,
                    reference=reference,
                    customer_email="",
                    metadata={
                        "subscription_id": subscription.id,
                        "tenant_id": subscription.tenant_id,
                        "dunning_attempt": subscription.renewal_attempts + 1,
                    },
                )
            )
        except Exception as e:
            logger.warning(f"Payment intent creation failed for {subscription.id}: {str(e)}")
            # Mark failure and schedule next retry
            await _increment_dunning_attempt(subscription, now)
            return False

        # Payment succeeded, reset to ACTIVE
        settings = get_settings()
        update = SubscriptionUpdate(
            status=SubscriptionStatus.ACTIVE,
            renewal_attempts=0,
            last_renewal_attempt_at=now,
            next_retry_at=None,
            last_updated=now,
        )

        updated_sub = await update_subscription(
            filter_dict={"_id": ObjectId(subscription.id)},
            sub_data=update,
        )

        if not updated_sub:
            logger.error(f"Failed to update subscription {subscription.id} after dunning success")
            return False

        # Generate invoice for the recovered payment
        try:
            await generate_invoice(
                tenant_id=subscription.tenant_id,
                subscription_id=subscription.id,
                plan_name=plan.name,
                billing_cycle=subscription.billing_cycle.value,
                currency=subscription.currency,
                base_price_minor=amount_minor,
                discount_minor=0,
                period_start=subscription.current_period_start,
                period_end=subscription.current_period_end,
                payment_transaction_id=intent.provider_payload.get("id") if intent.provider_payload else None,
            )
        except Exception as e:
            logger.warning(f"Invoice generation failed after dunning success: {str(e)}")

        # Invalidate plan cache
        try:
            await invalidate_tenant_plan_cache(subscription.tenant_id)
        except Exception as e:
            logger.warning(f"Plan cache invalidation failed: {str(e)}")

        logger.info(f"Dunning payment successful for subscription {subscription.id}")
        return True

    except Exception as e:
        logger.error(f"Dunning retry error for {subscription.id}: {str(e)}", exc_info=True)
        await _increment_dunning_attempt(subscription, now)
        return False


async def _increment_dunning_attempt(subscription: SubscriptionOut, now: int) -> None:
    """
    Helper to increment dunning attempt and schedule next retry.
    """
    settings = get_settings()
    next_attempt_index = min(subscription.renewal_attempts + 1, len(settings.dunning_retry_days) - 1)
    next_retry_days = settings.dunning_retry_days[next_attempt_index]
    next_retry_at = now + (next_retry_days * 86400)

    # Queue dunning email for this attempt
    await _queue_dunning_email(subscription, subscription.renewal_attempts + 1)

    # Update subscription
    update = SubscriptionUpdate(
        renewal_attempts=subscription.renewal_attempts + 1,
        last_renewal_attempt_at=now,
        next_retry_at=next_retry_at,
        last_updated=now,
    )

    await update_subscription(
        filter_dict={"_id": ObjectId(subscription.id)},
        sub_data=update,
    )


async def _suspend_subscription(subscription: SubscriptionOut, now: int) -> None:
    """
    Helper to suspend a subscription after max dunning attempts reached.
    """
    logger.info(f"Suspending subscription {subscription.id} after max dunning attempts")

    update = SubscriptionUpdate(
        status=SubscriptionStatus.SUSPENDED,
        last_renewal_attempt_at=now,
        last_updated=now,
    )

    await update_subscription(
        filter_dict={"_id": ObjectId(subscription.id)},
        sub_data=update,
    )


async def _queue_dunning_email(subscription: SubscriptionOut, attempt_number: int | None = None) -> None:
    """
    Queue appropriate dunning email based on attempt number.

    Email templates:
    - "payment_failed" for attempt 1
    - "payment_action_required" for attempts 2-3
    - "payment_last_chance" for attempt 4
    - "subscription_suspended" when max reached
    """
    settings = get_settings()
    max_attempts = settings.max_dunning_attempts

    try:
        queue = QueueManager.get_instance()
    except RuntimeError:
        logger.warning("QueueManager not available, skipping dunning email queue")
        return

    template_name: str
    if attempt_number is None or attempt_number >= max_attempts:
        template_name = "subscription_suspended"
    elif attempt_number == 1:
        template_name = "payment_failed"
    elif 2 <= attempt_number <= 3:
        template_name = "payment_action_required"
    elif attempt_number == 4:
        template_name = "payment_last_chance"
    else:
        template_name = "payment_action_required"

    payload = {
        "tenant_id": subscription.tenant_id,
        "subscription_id": subscription.id,
        "template_name": template_name,
        "attempt_number": attempt_number,
        "max_attempts": max_attempts,
    }

    try:
        queue.enqueue("services.email_service:send_dunning_email", payload)
        logger.info(f"Queued {template_name} email for subscription {subscription.id}")
    except Exception as e:
        logger.error(f"Failed to queue dunning email: {str(e)}", exc_info=True)


async def _queue_suspension_email(subscription: SubscriptionOut) -> None:
    """
    Queue suspension notification email.
    """
    await _queue_dunning_email(subscription, attempt_number=None)


def _get_next_retry_timestamp(attempts: int, settings) -> int:
    """
    Calculate next retry timestamp based on attempt number and dunning schedule.

    Uses settings.dunning_retry_days tuple to determine delay.
    """
    now = int(time.time())
    index = min(attempts, len(settings.dunning_retry_days) - 1)
    retry_days = settings.dunning_retry_days[index]
    return now + (retry_days * 86400)
