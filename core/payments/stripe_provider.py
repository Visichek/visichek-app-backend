from __future__ import annotations

import json

from core.errors import AppException, ErrorCode
from core.payments.provider import PaymentProvider
from core.payments.types import (
    PaymentIntentRequest,
    PaymentIntentResponse,
    PaymentProviderName,
    PaymentStatus,
    PaymentTransaction,
    WebhookEvent,
)


class StripePaymentProvider(PaymentProvider):
    provider_name = PaymentProviderName.STRIPE.value

    def __init__(self, *, secret_key: str, webhook_secret: str | None = None) -> None:
        try:
            import stripe
        except ModuleNotFoundError as err:
            raise RuntimeError(
                "stripe package is required for StripePaymentProvider"
            ) from err

        self._stripe = stripe
        self._stripe.api_key = secret_key
        self._webhook_secret = webhook_secret

    def create_intent(self, payload: PaymentIntentRequest) -> PaymentIntentResponse:
        meta = payload.metadata or {}
        create_kwargs: dict = {
            "amount": payload.amount_minor,
            "currency": payload.currency.lower(),
            "metadata": {"reference": payload.reference, **meta},
            "receipt_email": payload.customer_email or "",
            "automatic_payment_methods": {"enabled": True},
        }
        # When a Stripe customer id is supplied (recurring-capable checkout),
        # attach the customer and save the card off-session so the renewal
        # scheduler can charge it later without the cardholder present.
        stripe_customer_id = meta.get("stripe_customer_id")
        if stripe_customer_id:
            create_kwargs["customer"] = stripe_customer_id
            create_kwargs["setup_future_usage"] = "off_session"
        try:
            intent = self._stripe.PaymentIntent.create(**create_kwargs)
        except Exception as err:
            raise AppException(
                status_code=502,
                code=ErrorCode.PAYMENT_PROVIDER_ERROR,
                message="Stripe intent creation failed",
                details=str(err),
            ) from err

        return PaymentIntentResponse(
            provider=PaymentProviderName.STRIPE,
            reference=payload.reference,
            status=PaymentStatus.PENDING,
            checkout_url=None,
            provider_payload={"client_secret": intent.client_secret, "id": intent.id},
        )

    def verify_webhook(self, *, body: bytes, headers: dict[str, str]) -> WebhookEvent:
        signature = headers.get("stripe-signature") or headers.get("Stripe-Signature")
        if not signature or not self._webhook_secret:
            raise AppException(
                status_code=401,
                code=ErrorCode.PAYMENT_WEBHOOK_INVALID,
                message="Missing Stripe webhook signature",
            )

        try:
            event = self._stripe.Webhook.construct_event(
                payload=body,
                sig_header=signature,
                secret=self._webhook_secret,
            )
        except Exception as err:
            raise AppException(
                status_code=401,
                code=ErrorCode.PAYMENT_WEBHOOK_INVALID,
                message="Invalid Stripe webhook signature",
                details=str(err),
            ) from err

        return WebhookEvent(
            provider=PaymentProviderName.STRIPE,
            event_id=event["id"],
            event_type=event["type"],
            payload=json.loads(json.dumps(event, default=str)),
        )

    def fetch_transaction(self, *, reference: str) -> PaymentTransaction:
        intents = self._stripe.PaymentIntent.search(
            query=f"metadata['reference']:'{reference}'", limit=1
        )
        if not intents.data:
            raise AppException(
                status_code=404,
                code=ErrorCode.RESOURCE_NOT_FOUND,
                message="Stripe transaction not found",
                details={"reference": reference},
            )

        intent = intents.data[0]
        status = (
            PaymentStatus.SUCCEEDED
            if getattr(intent, "status", None) == "succeeded"
            else PaymentStatus.PENDING
        )
        return PaymentTransaction(
            provider=PaymentProviderName.STRIPE,
            reference=reference,
            status=status,
            raw=json.loads(json.dumps(intent, default=str)),
        )

    def refund(
        self, *, reference: str, amount_minor: int | None = None
    ) -> PaymentTransaction:
        tx = self.fetch_transaction(reference=reference)
        payment_intent_id = tx.raw.get("id")
        if not payment_intent_id:
            raise AppException(
                status_code=404,
                code=ErrorCode.RESOURCE_NOT_FOUND,
                message="Stripe payment intent not found for refund",
                details={"reference": reference},
            )

        refund_payload = {"payment_intent": payment_intent_id}
        if amount_minor is not None:
            refund_payload["amount"] = amount_minor

        refund = self._stripe.Refund.create(**refund_payload)
        return PaymentTransaction(
            provider=PaymentProviderName.STRIPE,
            reference=reference,
            status=PaymentStatus.REFUNDED,
            raw=json.loads(json.dumps(refund, default=str)),
        )

    def charge_recurring(
        self,
        *,
        instrument_ref: str,
        email: str,
        amount_minor: int,
        currency: str,
        reference: str,
        customer_ref: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> PaymentTransaction:
        # Off-session charge of a saved card. instrument_ref is the
        # PaymentMethod id; customer_ref is the Stripe customer it's attached to.
        if not customer_ref:
            raise AppException(
                status_code=400,
                code=ErrorCode.PAYMENT_PROVIDER_ERROR,
                message="Stripe recurring charge requires a customer id",
            )
        intent_metadata: dict = {"reference": reference, **(metadata or {})}
        try:
            intent = self._stripe.PaymentIntent.create(
                amount=amount_minor,
                currency=currency.lower(),
                customer=customer_ref,
                payment_method=instrument_ref,
                off_session=True,
                confirm=True,
                metadata=intent_metadata,
            )
        except Exception as err:
            # CardError (declines) and other charge failures are reported as a
            # FAILED transaction so the renewal scheduler can route to dunning,
            # not as a 5xx. The raw error is preserved for the audit trail.
            return PaymentTransaction(
                provider=PaymentProviderName.STRIPE,
                reference=reference,
                status=PaymentStatus.FAILED,
                raw={"error": str(err)},
            )

        # "succeeded" is a real charge. "requires_action"/"requires_payment_
        # method" mean the off-session charge needs the cardholder (3DS) or the
        # card was declined → FAILED. Anything else is still in flight → PENDING.
        status = getattr(intent, "status", None)
        if status == "succeeded":
            mapped = PaymentStatus.SUCCEEDED
        elif status in ("requires_action", "requires_payment_method"):
            mapped = PaymentStatus.FAILED
        else:
            mapped = PaymentStatus.PENDING
        return PaymentTransaction(
            provider=PaymentProviderName.STRIPE,
            reference=reference,
            status=mapped,
            raw=json.loads(json.dumps(intent, default=str)),
        )
