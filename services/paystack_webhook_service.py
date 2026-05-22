from __future__ import annotations

import json
import logging
import time
from typing import Optional

from bson import ObjectId

from core.errors import AppException, ErrorCode
from core.payments import PaymentManager
from repositories.payment_repo import update_payment_transaction_status
from repositories.subscription_repo import update_subscription
from repositories.webhook_event_repo import (
    WebhookEventCreate,
    compute_payload_hash,
    create_webhook_event,
    is_webhook_processed,
    mark_webhook_processed,
)
from schemas.subscription_schema import SubscriptionStatus, SubscriptionUpdate

logger = logging.getLogger(__name__)

PROVIDER = "paystack"


async def process_paystack_webhook(body: bytes, headers: dict[str, str]) -> dict:
    """
    Process a Paystack webhook event.

    Verifies the HMAC-SHA512 signature (via the provider), de-duplicates against
    the webhook_events collection, dispatches by event type, and records the
    outcome for audit/replay.

    Args:
        body: Raw webhook body
        headers: Request headers including the x-paystack-signature header

    Returns:
        Dict with processing result

    Raises:
        AppException: On signature verification or processing failure
    """
    start_time = time.time()

    try:
        payment_manager = PaymentManager.get_instance()
        provider = payment_manager.get_provider(PROVIDER)

        # Verify webhook signature (raises on mismatch)
        event = provider.verify_webhook(body=body, headers=headers)

        payload = json.loads(body.decode("utf-8"))
        payload_hash = compute_payload_hash(payload)

        event_id = event.event_id
        event_type = event.event_type
        logger.info(
            f"Processing Paystack webhook: event_id={event_id}, type={event_type}"
        )

        # Idempotency: skip if we've already processed this event
        if await is_webhook_processed(event_id=event_id, provider=PROVIDER):
            logger.warning(
                f"Webhook already processed: event_id={event_id}, provider={PROVIDER}"
            )
            await _record_webhook_event(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                payload_hash=payload_hash,
                status="skipped",
                error_message="Already processed",
                duration_ms=int((time.time() - start_time) * 1000),
            )
            raise AppException(
                status_code=409,
                code=ErrorCode.PAYMENT_WEBHOOK_INVALID,
                message="Webhook already processed",
                details={"event_id": event_id},
            )

        try:
            if event_type.lower() == "charge.success":
                result = await _handle_charge_success(payload)
            elif event_type.lower() == "charge.failed":
                result = await _handle_charge_failed(payload)
            elif event_type.lower() == "refund.processed":
                result = await _handle_refund_processed(payload)
            elif event_type.lower() in (
                "subscription.disable",
                "subscription.not_renew",
            ):
                result = await _handle_subscription_cancelled(payload)
            else:
                logger.info(f"Ignoring unhandled Paystack event type: {event_type}")
                result = {"handled": False, "event_type": event_type}

            duration_ms = int((time.time() - start_time) * 1000)
            await _record_webhook_event(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                payload_hash=payload_hash,
                status="processed",
                duration_ms=duration_ms,
            )
            await mark_webhook_processed(
                event_id=event_id,
                provider=PROVIDER,
                status="processed",
                duration_ms=duration_ms,
            )

            logger.info(
                f"Webhook processed successfully: event_id={event_id}, "
                f"type={event_type}, duration_ms={duration_ms}"
            )
            return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = str(e)
            logger.error(
                f"Webhook processing failed: event_id={event_id}, type={event_type}, "
                f"error={error_msg}",
                exc_info=True,
            )
            await _record_webhook_event(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                payload_hash=payload_hash,
                status="failed",
                error_message=error_msg,
                duration_ms=duration_ms,
            )
            await mark_webhook_processed(
                event_id=event_id,
                provider=PROVIDER,
                status="failed",
                error_message=error_msg,
                duration_ms=duration_ms,
            )
            raise

    except AppException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error processing Paystack webhook: {str(e)}", exc_info=True
        )
        raise AppException(
            status_code=502,
            code=ErrorCode.PAYMENT_WEBHOOK_INVALID,
            message="Failed to process webhook",
            details=str(e),
        ) from e


async def _handle_charge_success(payload: dict) -> dict:
    """Handle charge.success event from Paystack."""
    logger.debug("Handling charge.success event")

    data = payload.get("data", {}) or {}
    reference = data.get("reference")
    metadata = data.get("metadata") or {}
    if not reference:
        logger.warning("Webhook missing reference, cannot update payment status")
        return {"handled": False, "reason": "missing_reference"}

    # Always try to capture the reusable card authorization for recurring
    # billing. Best-effort: a failure here never blocks payment processing.
    tenant_id = metadata.get("tenant_id")
    if tenant_id:
        try:
            from services.paystack_billing_service import store_tenant_authorization

            await store_tenant_authorization(
                tenant_id=tenant_id,
                authorization=data.get("authorization") or {},
                email=(data.get("customer") or {}).get("email"),
            )
        except Exception:
            logger.warning(
                "Failed to capture Paystack authorization from webhook",
                exc_info=True,
            )

    # Trial card-capture charges are tiny tokenization charges: refund them and
    # start the trial subscription instead of running the normal payment path.
    if metadata.get("purpose") == "trial_tokenization":
        from services.paystack_billing_service import complete_trial_tokenization

        return await complete_trial_tokenization(payload)

    try:
        updated_tx = await update_payment_transaction_status(
            reference=reference,
            status="succeeded",
            response_payload=payload,
        )
        if updated_tx:
            logger.info(
                f"Updated payment transaction: reference={reference}, status=succeeded"
            )
            # Bridge to checkout sessions: activate the subscription if this
            # reference corresponds to a pending checkout.
            try:
                from services.checkout_service import (
                    maybe_complete_checkout_from_reference,
                )

                await maybe_complete_checkout_from_reference(reference, "success")
            except Exception:
                logger.exception(
                    "Failed to complete checkout session from Paystack webhook"
                )
            return {
                "handled": True,
                "action": "payment_updated",
                "reference": reference,
                "status": "succeeded",
            }
        logger.warning(f"Payment transaction not found: reference={reference}")
        return {"handled": False, "reason": "payment_not_found"}
    except Exception as e:
        logger.error(f"Failed to update payment transaction: {str(e)}", exc_info=True)
        raise


async def _handle_charge_failed(payload: dict) -> dict:
    """Handle charge.failed event from Paystack."""
    logger.debug("Handling charge.failed event")

    reference = payload.get("data", {}).get("reference")
    if not reference:
        logger.warning("Webhook missing reference for failed charge")
        return {"handled": False, "reason": "missing_reference"}

    try:
        updated_tx = await update_payment_transaction_status(
            reference=reference,
            status="failed",
            response_payload=payload,
        )
        if updated_tx:
            logger.info(
                f"Updated payment transaction: reference={reference}, status=failed"
            )
            try:
                from services.checkout_service import (
                    maybe_complete_checkout_from_reference,
                )

                await maybe_complete_checkout_from_reference(reference, "failure")
            except Exception:
                logger.exception(
                    "Failed to mark checkout session failed from Paystack webhook"
                )
            return {
                "handled": True,
                "action": "payment_updated",
                "reference": reference,
                "status": "failed",
            }
        logger.warning(f"Payment transaction not found: reference={reference}")
        return {"handled": False, "reason": "payment_not_found"}
    except Exception as e:
        logger.error(f"Failed to update payment transaction: {str(e)}", exc_info=True)
        raise


async def _handle_refund_processed(payload: dict) -> dict:
    """Handle refund.processed event from Paystack."""
    logger.debug("Handling refund.processed event")

    # Paystack puts the originating transaction reference either directly on the
    # refund (transaction_reference) or nested under data.transaction.reference.
    data = payload.get("data", {})
    transaction = data.get("transaction")
    reference = data.get("transaction_reference")
    if not reference and isinstance(transaction, dict):
        reference = transaction.get("reference")
    if not reference:
        logger.warning("Refund webhook missing transaction reference")
        return {"handled": False, "reason": "missing_reference"}

    try:
        updated_tx = await update_payment_transaction_status(
            reference=reference,
            status="refunded",
            response_payload=payload,
        )
        if updated_tx:
            logger.info(
                f"Updated payment transaction: reference={reference}, status=refunded"
            )
            return {
                "handled": True,
                "action": "payment_updated",
                "reference": reference,
                "status": "refunded",
            }
        logger.warning(f"Payment transaction not found: reference={reference}")
        return {"handled": False, "reason": "payment_not_found"}
    except Exception as e:
        logger.error(f"Failed to update refunded transaction: {str(e)}", exc_info=True)
        raise


async def _handle_subscription_cancelled(payload: dict) -> dict:
    """Handle subscription.disable / subscription.not_renew events from Paystack."""
    logger.debug("Handling subscription cancellation event")

    data = payload.get("data", {})
    subscription_ref = (
        data.get("metadata", {}).get("subscription_id")
        if isinstance(data.get("metadata"), dict)
        else None
    ) or data.get("subscription_code")

    if not subscription_ref:
        logger.warning("Webhook missing subscription identifier for cancellation")
        return {"handled": False, "reason": "missing_subscription_id"}

    try:
        if ObjectId.is_valid(subscription_ref):
            filter_dict = {"_id": ObjectId(subscription_ref)}
            update = SubscriptionUpdate(
                status=SubscriptionStatus.CANCELLED,
                cancelled_at=int(time.time()),
                last_updated=int(time.time()),
            )
            updated_sub = await update_subscription(
                filter_dict=filter_dict,
                sub_data=update,
            )
            if updated_sub:
                logger.info(f"Cancelled subscription: {subscription_ref}")
                return {
                    "handled": True,
                    "action": "subscription_cancelled",
                    "subscription_id": subscription_ref,
                }

        logger.warning(f"Subscription not found: {subscription_ref}")
        return {"handled": False, "reason": "subscription_not_found"}
    except Exception as e:
        logger.error(f"Failed to cancel subscription: {str(e)}", exc_info=True)
        raise


async def _record_webhook_event(
    event_id: str,
    event_type: str,
    payload: dict,
    payload_hash: str,
    status: str = "pending",
    error_message: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """Record webhook event for audit/replay purposes."""
    try:
        event_create = WebhookEventCreate(
            provider=PROVIDER,
            event_id=event_id,
            event_type=event_type,
            raw_payload=payload,
            payload_hash=payload_hash,
            processing_status=status,
            error_message=error_message,
            processing_duration_ms=duration_ms,
        )
        await create_webhook_event(event_create)
        logger.debug(f"Recorded webhook event: {event_id}")
    except Exception as e:
        logger.error(f"Failed to record webhook event: {str(e)}", exc_info=True)
        # Don't fail the webhook processing if we can't record it
