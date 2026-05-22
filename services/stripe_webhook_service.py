"""Stripe webhook processing.

Mirrors the Paystack/Flutterwave webhook services: verifies the signature (via
the provider), de-duplicates against ``webhook_events``, dispatches by event
type, and records the outcome for audit/replay.

Beyond updating payment/checkout state, ``payment_intent.succeeded`` captures
the saved PaymentMethod + customer for off-session recurring billing, so the
renewal scheduler can charge the tenant later without the cardholder present.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from bson import ObjectId

from core.errors import AppException, ErrorCode
from core.payments import PaymentManager
from repositories.payment_repo import update_payment_transaction_status
from repositories.tenant_repo import update_tenant
from repositories.webhook_event_repo import (
    WebhookEventCreate,
    compute_payload_hash,
    create_webhook_event,
    is_webhook_processed,
    mark_webhook_processed,
)
from schemas.tenant_schema import TenantUpdate

logger = logging.getLogger(__name__)

PROVIDER = "stripe"


async def process_stripe_webhook(body: bytes, headers: dict[str, str]) -> dict:
    """Process a Stripe webhook event end to end."""
    start_time = time.time()

    try:
        provider = PaymentManager.get_instance().get_provider(PROVIDER)
        event = provider.verify_webhook(body=body, headers=headers)

        payload = json.loads(body.decode("utf-8"))
        payload_hash = compute_payload_hash(payload)
        event_id = event.event_id
        event_type = event.event_type
        logger.info(
            f"Processing Stripe webhook: event_id={event_id}, type={event_type}"
        )

        if await is_webhook_processed(event_id=event_id, provider=PROVIDER):
            logger.warning(f"Webhook already processed: event_id={event_id}")
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
            if event_type == "payment_intent.succeeded":
                result = await _handle_payment_succeeded(payload)
            elif event_type == "payment_intent.payment_failed":
                result = await _handle_payment_failed(payload)
            else:
                logger.info(f"Ignoring unhandled Stripe event type: {event_type}")
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
            return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = str(e)
            logger.error(
                f"Webhook processing failed: event_id={event_id}, error={error_msg}",
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
            f"Unexpected error processing Stripe webhook: {str(e)}", exc_info=True
        )
        raise AppException(
            status_code=502,
            code=ErrorCode.PAYMENT_WEBHOOK_INVALID,
            message="Failed to process webhook",
            details=str(e),
        ) from e


def _payment_intent_obj(payload: dict) -> dict:
    return (payload.get("data", {}) or {}).get("object", {}) or {}


async def _capture_payment_method(obj: dict) -> None:
    """Persist the saved PaymentMethod + customer for off-session recurring."""
    metadata = obj.get("metadata") or {}
    tenant_id = metadata.get("tenant_id")
    payment_method = obj.get("payment_method")
    customer = obj.get("customer")
    if not (tenant_id and payment_method and ObjectId.is_valid(tenant_id)):
        return
    try:
        await update_tenant(
            filter_dict={"_id": ObjectId(tenant_id)},
            tenant_data=TenantUpdate(
                stripe_payment_method_id=payment_method,
                **({"stripe_customer_id": customer} if customer else {}),
            ),
        )
        logger.info(
            "Stored Stripe payment method for tenant %s (pm=%s)",
            tenant_id,
            payment_method,
        )
    except Exception:
        logger.warning(
            "Failed to store Stripe payment method for tenant %s",
            tenant_id,
            exc_info=True,
        )


async def _handle_payment_succeeded(payload: dict) -> dict:
    obj = _payment_intent_obj(payload)
    reference = (obj.get("metadata") or {}).get("reference")

    # Capture the reusable card for recurring billing (best-effort).
    await _capture_payment_method(obj)

    if not reference:
        return {"handled": False, "reason": "missing_reference"}

    try:
        await update_payment_transaction_status(
            reference=reference, status="succeeded", response_payload=payload
        )
        try:
            from services.checkout_service import (
                maybe_complete_checkout_from_reference,
            )

            await maybe_complete_checkout_from_reference(reference, "success")
        except Exception:
            logger.exception("Failed to complete checkout from Stripe webhook")
        return {
            "handled": True,
            "action": "payment_updated",
            "reference": reference,
            "status": "succeeded",
        }
    except Exception as e:
        logger.error(f"Failed to update payment transaction: {str(e)}", exc_info=True)
        raise


async def _handle_payment_failed(payload: dict) -> dict:
    obj = _payment_intent_obj(payload)
    reference = (obj.get("metadata") or {}).get("reference")
    if not reference:
        return {"handled": False, "reason": "missing_reference"}

    try:
        await update_payment_transaction_status(
            reference=reference, status="failed", response_payload=payload
        )
        try:
            from services.checkout_service import (
                maybe_complete_checkout_from_reference,
            )

            await maybe_complete_checkout_from_reference(reference, "failure")
        except Exception:
            logger.exception("Failed to mark checkout failed from Stripe webhook")
        return {
            "handled": True,
            "action": "payment_updated",
            "reference": reference,
            "status": "failed",
        }
    except Exception as e:
        logger.error(f"Failed to update payment transaction: {str(e)}", exc_info=True)
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
    try:
        await create_webhook_event(
            WebhookEventCreate(
                provider=PROVIDER,
                event_id=event_id,
                event_type=event_type,
                raw_payload=payload,
                payload_hash=payload_hash,
                processing_status=status,
                error_message=error_message,
                processing_duration_ms=duration_ms,
            )
        )
    except Exception as e:
        logger.error(f"Failed to record webhook event: {str(e)}", exc_info=True)
