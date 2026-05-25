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


def _flutterwave_transaction_verified(tx_ref: str) -> bool:
    """Independently confirm a transaction succeeded via the Flutterwave API.

    A valid webhook signature proves the body came from Flutterwave, but for
    high-value billing state changes (activating a paid subscription) we
    re-query the transaction directly so a replayed/spoofed-then-signed body
    can't grant benefits. Fail CLOSED: if the provider is configured but the
    transaction does not verify as successful (or the verify call errors), we
    return False and the subscription is NOT activated — the event is stored
    and can be replayed once reconciled. When no Flutterwave provider is
    configured (e.g. app/dev mode) there is nothing to verify against, so we
    fall back to trusting the signature that was already checked.
    """
    try:
        from core.payments.types import PaymentStatus

        manager = PaymentManager.get_instance()
        if not manager.has_provider("flutterwave"):
            return True
        provider = manager.get_provider("flutterwave")
        tx = provider.fetch_transaction(reference=tx_ref)
        return tx.status == PaymentStatus.SUCCEEDED
    except Exception:
        logger.warning(
            "Flutterwave transaction verification failed for tx_ref=%s; "
            "refusing to activate benefits on an unverified event",
            tx_ref,
            exc_info=True,
        )
        return False


async def process_flutterwave_webhook(body: bytes, headers: dict[str, str]) -> dict:
    """
    Process a Flutterwave webhook event.

    Verifies the webhook signature, handles various event types,
    and updates payment/subscription status accordingly.

    Args:
        body: Raw webhook body
        headers: Request headers including verification hash

    Returns:
        Dict with processing result

    Raises:
        AppException: On signature verification or processing failure
    """
    start_time = time.time()

    try:
        # Get payment manager and Flutterwave provider
        payment_manager = PaymentManager.get_instance()
        provider = payment_manager.get_provider("flutterwave")

        # Verify webhook signature
        event = provider.verify_webhook(body=body, headers=headers)

        # Parse payload
        payload = json.loads(body.decode("utf-8"))
        payload_hash = compute_payload_hash(payload)

        # Extract event details
        event_id = event.event_id
        event_type = event.event_type
        logger.info(
            f"Processing Flutterwave webhook: event_id={event_id}, type={event_type}"
        )

        # Check for duplicate processing
        if await is_webhook_processed(event_id=event_id, provider="flutterwave"):
            logger.warning(
                f"Webhook already processed: event_id={event_id}, provider=flutterwave"
            )
            await _record_webhook_event(
                provider="flutterwave",
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

        # Route to appropriate handler based on event type
        try:
            if event_type.lower() == "charge.completed":
                result = await _handle_charge_completed(payload)
            elif event_type.lower() == "charge.failed":
                result = await _handle_charge_failed(payload)
            elif event_type.lower() == "transfer.completed":
                result = await _handle_transfer_completed(payload)
            elif event_type.lower() == "subscription.cancelled":
                result = await _handle_subscription_cancelled(payload)
            else:
                logger.info(f"Ignoring unhandled Flutterwave event type: {event_type}")
                result = {"handled": False, "event_type": event_type}

            # Record successful webhook processing
            duration_ms = int((time.time() - start_time) * 1000)
            await _record_webhook_event(
                provider="flutterwave",
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                payload_hash=payload_hash,
                status="processed",
                duration_ms=duration_ms,
            )
            await mark_webhook_processed(
                event_id=event_id,
                provider="flutterwave",
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
                provider="flutterwave",
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
                provider="flutterwave",
                status="failed",
                error_message=error_msg,
                duration_ms=duration_ms,
            )
            raise

    except AppException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error processing Flutterwave webhook: {str(e)}", exc_info=True
        )
        raise AppException(
            status_code=502,
            code=ErrorCode.PAYMENT_WEBHOOK_INVALID,
            message="Failed to process webhook",
            details=str(e),
        ) from e


async def _handle_charge_completed(payload: dict) -> dict:
    """Handle charge.completed event from Flutterwave."""
    logger.debug("Handling charge.completed event")

    # Extract transaction reference
    tx_ref = payload.get("data", {}).get("tx_ref")
    if not tx_ref:
        logger.warning("Webhook missing tx_ref, cannot update payment status")
        return {"handled": False, "reason": "missing_tx_ref"}

    # Update payment transaction status
    try:
        updated_tx = await update_payment_transaction_status(
            reference=tx_ref,
            status="succeeded",
            response_payload=payload,
        )
        if updated_tx:
            logger.info(
                f"Updated payment transaction: reference={tx_ref}, status=succeeded"
            )
            # High-value gate: only activate the subscription after an
            # independent provider-side confirmation that the charge really
            # succeeded. A signed-but-spoofed/replayed body therefore can't
            # grant plan benefits.
            if not _flutterwave_transaction_verified(tx_ref):
                logger.error(
                    "Flutterwave charge.completed for tx_ref=%s could not be "
                    "independently verified; payment recorded but subscription "
                    "NOT activated (event is stored for replay).",
                    tx_ref,
                )
                return {
                    "handled": True,
                    "action": "payment_recorded_unverified",
                    "reference": tx_ref,
                    "status": "succeeded",
                    "activated": False,
                }
            # Bridge to checkout sessions: activate the subscription if this
            # reference corresponds to a pending checkout.
            try:
                from services.checkout_service import (
                    maybe_complete_checkout_from_reference,
                )

                await maybe_complete_checkout_from_reference(tx_ref, "success")
            except Exception:
                logger.exception(
                    "Failed to complete checkout session from Flutterwave webhook"
                )
            return {
                "handled": True,
                "action": "payment_updated",
                "reference": tx_ref,
                "status": "succeeded",
                "activated": True,
            }
        else:
            logger.warning(f"Payment transaction not found: reference={tx_ref}")
            return {"handled": False, "reason": "payment_not_found"}
    except Exception as e:
        logger.error(f"Failed to update payment transaction: {str(e)}", exc_info=True)
        raise


async def _handle_charge_failed(payload: dict) -> dict:
    """Handle charge.failed event from Flutterwave."""
    logger.debug("Handling charge.failed event")

    # Extract transaction reference
    tx_ref = payload.get("data", {}).get("tx_ref")
    if not tx_ref:
        logger.warning("Webhook missing tx_ref for failed charge")
        return {"handled": False, "reason": "missing_tx_ref"}

    # Update payment transaction status
    try:
        updated_tx = await update_payment_transaction_status(
            reference=tx_ref,
            status="failed",
            response_payload=payload,
        )
        if updated_tx:
            logger.info(
                f"Updated payment transaction: reference={tx_ref}, status=failed"
            )
            try:
                from services.checkout_service import (
                    maybe_complete_checkout_from_reference,
                )

                await maybe_complete_checkout_from_reference(tx_ref, "failure")
            except Exception:
                logger.exception(
                    "Failed to mark checkout session failed from Flutterwave webhook"
                )
            return {
                "handled": True,
                "action": "payment_updated",
                "reference": tx_ref,
                "status": "failed",
            }
        else:
            logger.warning(f"Payment transaction not found: reference={tx_ref}")
            return {"handled": False, "reason": "payment_not_found"}
    except Exception as e:
        logger.error(f"Failed to update payment transaction: {str(e)}", exc_info=True)
        raise


async def _handle_transfer_completed(payload: dict) -> dict:
    """Handle transfer.completed event from Flutterwave (payout)."""
    logger.debug("Handling transfer.completed event")
    # This is typically for payouts, log for audit purposes
    transfer_id = payload.get("data", {}).get("id")
    logger.info(f"Transfer completed: transfer_id={transfer_id}")
    return {
        "handled": True,
        "action": "transfer_logged",
        "transfer_id": transfer_id,
    }


async def _handle_subscription_cancelled(payload: dict) -> dict:
    """Handle subscription.cancelled event from Flutterwave."""
    logger.debug("Handling subscription.cancelled event")

    # Extract subscription reference from metadata or payload
    subscription_ref = (
        payload.get("data", {}).get("subscription_id")
        or payload.get("meta", {}).get("subscription_id")
        or payload.get("subscription_id")
    )

    if not subscription_ref:
        logger.warning("Webhook missing subscription_id for cancellation")
        return {"handled": False, "reason": "missing_subscription_id"}

    # Try to find subscription by reference - subscription_ref could be the MongoDB ID
    try:
        # Assume the reference is a MongoDB ObjectId
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
    provider: str,
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
            provider=provider,
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
