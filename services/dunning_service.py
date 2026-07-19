from __future__ import annotations

import logging
import time

from bson import ObjectId

from core.payments import PaymentIntentRequest, PaymentManager
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

        subscriptions = await get_subscriptions(
            filter_dict=filter_dict, start=0, stop=1000
        )

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
            logger.warning(
                f"Payment intent creation failed for {subscription.id}: {str(e)}"
            )
            # Mark failure and schedule next retry
            await _increment_dunning_attempt(subscription, now)
            return False

        # Payment succeeded, reset to ACTIVE
        get_settings()
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
            logger.error(
                f"Failed to update subscription {subscription.id} after dunning success"
            )
            return False

        # Generate invoice for the recovered payment
        try:
            await generate_invoice(
                tenant_id=subscription.tenant_id,
                subscription_id=subscription.id or "",
                plan_name=plan.name,
                billing_cycle=subscription.billing_cycle.value,
                currency=subscription.currency,
                base_price_minor=amount_minor,
                discount_minor=0,
                period_start=subscription.current_period_start,
                period_end=subscription.current_period_end or 0,
                payment_transaction_id=intent.provider_payload.get("id")
                if intent.provider_payload
                else None,
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
        logger.error(
            f"Dunning retry error for {subscription.id}: {str(e)}", exc_info=True
        )
        await _increment_dunning_attempt(subscription, now)
        return False


async def _increment_dunning_attempt(subscription: SubscriptionOut, now: int) -> None:
    """
    Helper to increment dunning attempt and schedule next retry.
    """
    settings = get_settings()
    next_attempt_index = min(
        subscription.renewal_attempts + 1, len(settings.dunning_retry_days) - 1
    )
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
    Drop a tenant onto the Free plan after max dunning attempts.

    Previous behaviour set ``status=SUSPENDED`` and left the tenant
    with no usable subscription, which caused every API request to
    return 402 SUBSCRIPTION_REQUIRED. We now downgrade to Free instead
    so visitor logging keeps working — the tenant loses access to the
    paid features they stopped paying for, but core operations stay
    available. The ledger row is preserved (status flips to EXPIRED
    inside ``transition_tenant_to_free_plan``) for billing reporting.
    """
    logger.info(
        "Downgrading subscription %s to Free after max dunning attempts",
        subscription.id,
    )

    # Stamp the failed renewal attempt timestamp so the dunning log
    # reflects the final attempt before drop-to-free.
    await update_subscription(
        filter_dict={"_id": ObjectId(subscription.id)},
        sub_data=SubscriptionUpdate(
            last_renewal_attempt_at=now,
            last_updated=now,
        ),
    )

    try:
        from services.subscription_service import transition_tenant_to_free_plan

        await transition_tenant_to_free_plan(
            tenant_id=subscription.tenant_id,
            reason="Max dunning attempts reached",
            actor_id="system",
            actor_role="system",
        )
    except Exception:
        logger.exception(
            "Drop-to-free failed for subscription %s; leaving SUSPENDED",
            subscription.id,
        )
        # Fall back to the legacy SUSPENDED status if the transition
        # blows up so we at least mark the sub as no longer active.
        await update_subscription(
            filter_dict={"_id": ObjectId(subscription.id)},
            sub_data=SubscriptionUpdate(
                status=SubscriptionStatus.SUSPENDED,
                last_updated=now,
            ),
        )


def _dunning_stage_copy(
    attempt_number: int | None, max_attempts: int
) -> tuple[str, str, str, str]:
    """Return ``(stage, subject_line, title, body)`` for a dunning email."""
    if attempt_number is None or attempt_number >= max_attempts:
        return (
            "subscription_downgraded",
            "Your subscription has been moved to the Free plan",
            "Subscription moved to Free",
            "We were unable to collect payment after several attempts, so "
            "your workspace has been moved to the Free plan. Your data is "
            "safe — upgrade again at any time to restore your previous "
            "plan's features.",
        )
    if attempt_number == 1:
        return (
            "payment_failed",
            "We couldn't process your subscription payment",
            "Payment failed",
            "Your latest subscription payment didn't go through. We'll retry "
            "automatically — no action is needed if your payment method is "
            "up to date.",
        )
    if attempt_number == 4:
        return (
            "payment_last_chance",
            "Final notice: your subscription payment is still failing",
            "Final payment notice",
            "This is the last retry before your workspace is moved to the "
            "Free plan. Please update your payment method now to keep your "
            "current plan.",
        )
    return (
        "payment_action_required",
        "Action required: your subscription payment is failing",
        "Action required",
        "We still can't collect your subscription payment. Please review "
        "your payment method to avoid losing access to your plan's "
        "features.",
    )


async def _resolve_billing_recipient(
    tenant_id: str,
) -> tuple[str | None, str | None]:
    """Best-effort ``(email, name)`` of the tenant's billing contact.

    Prefers the main super_admin; falls back to any active super_admin.
    """
    try:
        from repositories.system_user_repo import get_system_users

        rows = await get_system_users(
            {
                "tenant_id": tenant_id,
                "role": "super_admin",
                "is_active": True,
            },
            start=0,
            stop=10,
        )
        if not rows:
            return None, None
        main = next(
            (r for r in rows if getattr(r, "is_main_super_admin", False)), rows[0]
        )
        return (
            str(main.email) if getattr(main, "email", None) else None,
            getattr(main, "full_name", None),
        )
    except Exception:
        logger.warning(
            "dunning: billing recipient lookup failed for tenant %s",
            tenant_id,
            exc_info=True,
        )
        return None, None


async def _queue_dunning_email(
    subscription: SubscriptionOut, attempt_number: int | None = None
) -> None:
    """Send the appropriate dunning email for the attempt number.

    Stages: ``payment_failed`` (attempt 1), ``payment_action_required``
    (attempts 2-3), ``payment_last_chance`` (attempt 4), and the Free
    downgrade notice once max attempts are exhausted. All stages render
    through the mounted ``billing_dunning`` template via the EmailManager
    (queue-aware) — the old path enqueued a
    ``services.email_service:send_dunning_email`` task that never
    existed, so no dunning email was ever delivered.
    """
    settings = get_settings()
    max_attempts = settings.max_dunning_attempts
    stage, subject_line, title, body = _dunning_stage_copy(
        attempt_number, max_attempts
    )

    recipient_email, recipient_name = await _resolve_billing_recipient(
        subscription.tenant_id
    )
    if not recipient_email:
        logger.warning(
            "dunning: no billing recipient for tenant %s — %s email skipped",
            subscription.tenant_id,
            stage,
        )
        return

    organization_name = ""
    try:
        from services.tenant_service import retrieve_tenant_by_id

        tenant = await retrieve_tenant_by_id(subscription.tenant_id)
        organization_name = getattr(tenant, "company_name", "") or ""
    except Exception:
        pass

    billing_url = (settings.app_base_url or "").rstrip("/")
    if billing_url:
        billing_url = f"{billing_url}/app/billing"

    try:
        from core.email.manager import EmailManager
        from core.email.types import EmailDispatchRequest

        await EmailManager.get_instance().send_template(
            EmailDispatchRequest(
                to_email=recipient_email,
                template_key="billing_dunning",
                context={
                    "subject_line": subject_line,
                    "title": title,
                    "body": body,
                    "recipient_name": recipient_name or recipient_email,
                    "platform_name": settings.email_sender_name or "VisiChek",
                    "organization_name": organization_name,
                    "billing_url": billing_url,
                    "stage": stage,
                    "attempt_number": attempt_number,
                    "max_attempts": max_attempts,
                },
                dispatch="auto",
            )
        )
        logger.info(
            "Queued %s email for subscription %s", stage, subscription.id
        )
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
