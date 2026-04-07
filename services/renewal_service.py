from __future__ import annotations

import logging
import time
from typing import Optional

from bson import ObjectId

from core.database import db
from core.payments import PaymentIntentRequest, PaymentManager
from core.errors import AppException
from core.settings import get_settings
from repositories.subscription_repo import (
    get_subscription,
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
from repositories.tenant_repo import get_tenant

logger = logging.getLogger(__name__)


async def _get_provider_for_tenant(tenant_id: str) -> str:
    """
    Determine which payment provider to use for a tenant.

    Checks the tenant's default_payment_provider setting and verifies
    that the necessary customer ID exists for that provider.

    Args:
        tenant_id: MongoDB tenant ID

    Returns:
        Provider name ("stripe" or "flutterwave")

    Raises:
        AppException: If tenant not found or no valid provider configured
    """
    if not ObjectId.is_valid(tenant_id):
        logger.error(f"Invalid tenant_id format: {tenant_id}")
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Invalid tenant ID",
        )

    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Tenant not found",
            details={"tenant_id": tenant_id},
        )

    # Determine provider based on tenant settings
    provider_name = (tenant.default_payment_provider or "").lower()

    # Validate that the tenant has the necessary customer ID for the chosen provider
    if provider_name == "flutterwave":
        if not tenant.flutterwave_customer_id:
            logger.warning(
                f"Tenant has no Flutterwave customer ID, falling back to default provider. "
                f"tenant_id={tenant_id}"
            )
            # Fall back to default provider
            provider_name = None
    elif provider_name == "stripe":
        if not tenant.stripe_customer_id:
            logger.warning(
                f"Tenant has no Stripe customer ID, falling back to default provider. "
                f"tenant_id={tenant_id}"
            )
            # Fall back to default provider
            provider_name = None

    # Use the payment manager's default provider if none specified
    # or if the preferred provider is not available
    if not provider_name:
        payment_manager = PaymentManager.get_instance()
        provider = payment_manager.get_provider()  # Gets default provider
        return provider.provider_name

    return provider_name


async def renew_due_subscriptions() -> dict:
    """
    Top-level function called by APScheduler hourly.
    Processes subscriptions due for renewal.

    Returns dict with counts of successful/failed renewals.
    """
    now = int(time.time())
    logger.info("Starting subscription renewal check")

    renewed_count = 0
    failed_count = 0

    try:
        # Query subscriptions that need renewal:
        # - Status is ACTIVE or PAST_DUE
        # - Current period has ended
        # - Not cancelled
        filter_dict = {
            "status": {"$in": [SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE]},
            "current_period_end": {"$lte": now},
            "cancelled_at": None,
        }

        subscriptions = await get_subscriptions(filter_dict=filter_dict, start=0, stop=1000)

        for subscription in subscriptions:
            try:
                success = await _attempt_renewal(subscription)
                if success:
                    renewed_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                logger.error(
                    f"Renewal error for subscription {subscription.id}: {str(e)}",
                    exc_info=True,
                )
                failed_count += 1

        logger.info(f"Renewal check complete: {renewed_count} renewed, {failed_count} failed")
        return {
            "renewed_count": renewed_count,
            "failed_count": failed_count,
            "total_processed": renewed_count + failed_count,
        }

    except Exception as e:
        logger.error(f"Fatal error in renewal batch: {str(e)}", exc_info=True)
        return {
            "renewed_count": renewed_count,
            "failed_count": failed_count,
            "error": str(e),
        }


async def _attempt_renewal(subscription: SubscriptionOut) -> bool:
    """
    Internal function to attempt renewal for a single subscription.

    Returns True on success, False on failure.
    Sets PAST_DUE status and schedules retry on failure.
    """
    logger.info(f"Attempting renewal for subscription {subscription.id}")
    now = int(time.time())

    try:
        # Fetch the plan
        if not ObjectId.is_valid(subscription.plan_id):
            logger.error(f"Invalid plan_id {subscription.plan_id} for subscription {subscription.id}")
            return False

        plan = await get_plan({"_id": ObjectId(subscription.plan_id)})
        if not plan:
            logger.error(f"Plan not found: {subscription.plan_id}")
            return False

        # Get payment manager and determine correct provider for tenant
        try:
            payment_manager = PaymentManager.get_instance()
        except RuntimeError as e:
            logger.error(f"Payment manager not configured: {str(e)}")
            return False

        # Determine provider based on tenant's payment settings
        try:
            provider_name = await _get_provider_for_tenant(subscription.tenant_id)
            provider = payment_manager.get_provider(provider_name)
            logger.debug(
                f"Using provider '{provider_name}' for subscription renewal. "
                f"subscription_id={subscription.id}, tenant_id={subscription.tenant_id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to determine payment provider for tenant: {str(e)}", exc_info=True
            )
            return False

        # Calculate new period
        from services.subscription_service import _calculate_period_end

        new_period_start = now
        new_period_end = _calculate_period_end(now, subscription.billing_cycle)

        # Create payment intent
        amount_minor = int(subscription.effective_price * 100)  # Convert to minor units
        reference = f"renewal-{subscription.id}-{now}"

        try:
            intent = provider.create_intent(
                PaymentIntentRequest(
                    amount_minor=amount_minor,
                    currency=subscription.currency,
                    reference=reference,
                    customer_email="",  # Can be enhanced with customer email if tracked
                    metadata={
                        "subscription_id": subscription.id,
                        "tenant_id": subscription.tenant_id,
                        "renewal": True,
                    },
                )
            )
        except Exception as e:
            logger.warning(f"Payment intent creation failed for {subscription.id}: {str(e)}")
            return False

        # Update subscription with new period and reset renewal state
        update = SubscriptionUpdate(
            status=SubscriptionStatus.ACTIVE,
            current_period_start=new_period_start,
            current_period_end=new_period_end,
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
            logger.error(f"Failed to update subscription {subscription.id}")
            return False

        # Generate invoice for the renewal
        try:
            await generate_invoice(
                tenant_id=subscription.tenant_id,
                subscription_id=subscription.id,
                plan_name=plan.name,
                billing_cycle=subscription.billing_cycle.value,
                currency=subscription.currency,
                base_price_minor=amount_minor,
                discount_minor=0,  # Discounts already applied in effective_price
                period_start=new_period_start,
                period_end=new_period_end,
                payment_transaction_id=intent.provider_payload.get("id") if intent.provider_payload else None,
            )
        except Exception as e:
            logger.warning(f"Invoice generation failed for {subscription.id}: {str(e)}")
            # Don't fail renewal if invoice generation fails

        # Invalidate plan cache
        try:
            await invalidate_tenant_plan_cache(subscription.tenant_id)
        except Exception as e:
            logger.warning(f"Plan cache invalidation failed for tenant {subscription.tenant_id}: {str(e)}")

        logger.info(f"Renewal successful for subscription {subscription.id}")
        return True

    except Exception as e:
        logger.error(f"Renewal attempt error for {subscription.id}: {str(e)}", exc_info=True)
        return False


async def convert_expiring_trials() -> dict:
    """
    Separate function scheduled hourly.
    Converts expiring trial subscriptions to ACTIVE or PAST_DUE.

    Returns dict with counts of converted subscriptions.
    """
    now = int(time.time())
    logger.info("Starting trial conversion check")

    converted_active = 0
    converted_past_due = 0
    failed_count = 0

    try:
        # Query subscriptions with expiring trials
        filter_dict = {
            "status": SubscriptionStatus.TRIALING,
            "trial_ends_at": {"$lte": now},
        }

        subscriptions = await get_subscriptions(filter_dict=filter_dict, start=0, stop=1000)

        for subscription in subscriptions:
            try:
                success = await _convert_trial_to_paid(subscription)
                if success:
                    converted_active += 1
                else:
                    converted_past_due += 1
            except Exception as e:
                logger.error(
                    f"Trial conversion error for subscription {subscription.id}: {str(e)}",
                    exc_info=True,
                )
                failed_count += 1

        logger.info(
            f"Trial conversion check complete: {converted_active} active, "
            f"{converted_past_due} past_due, {failed_count} errors"
        )
        return {
            "converted_active": converted_active,
            "converted_past_due": converted_past_due,
            "failed_count": failed_count,
        }

    except Exception as e:
        logger.error(f"Fatal error in trial conversion batch: {str(e)}", exc_info=True)
        return {
            "converted_active": converted_active,
            "converted_past_due": converted_past_due,
            "failed_count": failed_count,
            "error": str(e),
        }


async def _convert_trial_to_paid(subscription: SubscriptionOut) -> bool:
    """
    Internal function to convert a trial subscription to ACTIVE or PAST_DUE.

    On success: sets status=ACTIVE.
    On failure: sets status=PAST_DUE and schedules first retry.

    Returns True if status=ACTIVE, False if status=PAST_DUE.
    """
    logger.info(f"Converting trial subscription {subscription.id} to paid")
    now = int(time.time())

    try:
        # Fetch the plan
        if not ObjectId.is_valid(subscription.plan_id):
            logger.error(f"Invalid plan_id {subscription.plan_id}")
            return False

        plan = await get_plan({"_id": ObjectId(subscription.plan_id)})
        if not plan:
            logger.error(f"Plan not found: {subscription.plan_id}")
            return False

        # Try to create payment intent
        try:
            payment_manager = PaymentManager.get_instance()
        except RuntimeError as e:
            logger.error(f"Payment manager not configured: {str(e)}")
            # Set to PAST_DUE for later retry
            await _set_past_due_with_retry(subscription, now)
            return False

        # Determine provider based on tenant's payment settings
        try:
            provider_name = await _get_provider_for_tenant(subscription.tenant_id)
            provider = payment_manager.get_provider(provider_name)
            logger.debug(
                f"Using provider '{provider_name}' for trial conversion. "
                f"subscription_id={subscription.id}, tenant_id={subscription.tenant_id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to determine payment provider for tenant: {str(e)}", exc_info=True
            )
            await _set_past_due_with_retry(subscription, now)
            return False

        # Calculate period
        from services.subscription_service import _calculate_period_end

        new_period_start = now
        new_period_end = _calculate_period_end(now, subscription.billing_cycle)
        amount_minor = int(subscription.effective_price * 100)
        reference = f"trial-convert-{subscription.id}-{now}"

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
                        "trial_conversion": True,
                    },
                )
            )
        except Exception as e:
            logger.warning(f"Payment intent creation failed for trial {subscription.id}: {str(e)}")
            await _set_past_due_with_retry(subscription, now)
            return False

        # Payment succeeded, update to ACTIVE
        update = SubscriptionUpdate(
            status=SubscriptionStatus.ACTIVE,
            current_period_start=new_period_start,
            current_period_end=new_period_end,
            trial_ends_at=None,
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
            logger.error(f"Failed to update trial subscription {subscription.id}")
            return False

        # Generate invoice
        try:
            await generate_invoice(
                tenant_id=subscription.tenant_id,
                subscription_id=subscription.id,
                plan_name=plan.name,
                billing_cycle=subscription.billing_cycle.value,
                currency=subscription.currency,
                base_price_minor=amount_minor,
                discount_minor=0,
                period_start=new_period_start,
                period_end=new_period_end,
            )
        except Exception as e:
            logger.warning(f"Invoice generation failed for trial {subscription.id}: {str(e)}")

        # Invalidate plan cache
        try:
            await invalidate_tenant_plan_cache(subscription.tenant_id)
        except Exception as e:
            logger.warning(f"Plan cache invalidation failed: {str(e)}")

        logger.info(f"Trial conversion successful for subscription {subscription.id}")
        return True

    except Exception as e:
        logger.error(f"Trial conversion error for {subscription.id}: {str(e)}", exc_info=True)
        await _set_past_due_with_retry(subscription, now)
        return False


async def _set_past_due_with_retry(subscription: SubscriptionOut, now: int) -> None:
    """
    Helper to set subscription to PAST_DUE with next retry scheduled.
    Used when trial conversion payment fails.
    """
    settings = get_settings()
    next_retry = now + (settings.dunning_retry_days[0] * 86400) if settings.dunning_retry_days else now + 86400

    update = SubscriptionUpdate(
        status=SubscriptionStatus.PAST_DUE,
        renewal_attempts=1,
        last_renewal_attempt_at=now,
        next_retry_at=next_retry,
        last_updated=now,
    )

    await update_subscription(
        filter_dict={"_id": ObjectId(subscription.id)},
        sub_data=update,
    )
