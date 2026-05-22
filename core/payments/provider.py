from __future__ import annotations

from typing import Protocol

from core.payments.types import (
    PaymentIntentRequest,
    PaymentIntentResponse,
    PaymentTransaction,
    WebhookEvent,
)


class PaymentProvider(Protocol):
    provider_name: str

    def create_intent(self, payload: PaymentIntentRequest) -> PaymentIntentResponse: ...

    def verify_webhook(
        self, *, body: bytes, headers: dict[str, str]
    ) -> WebhookEvent: ...

    def fetch_transaction(self, *, reference: str) -> PaymentTransaction: ...

    def refund(
        self, *, reference: str, amount_minor: int | None = None
    ) -> PaymentTransaction: ...

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
        """Charge a previously-saved payment instrument server-side (no
        customer interaction).

        ``instrument_ref`` is the provider's saved-instrument token (Paystack
        authorization_code, Stripe PaymentMethod id). ``customer_ref`` is the
        provider customer id where the provider requires it (Stripe). ``email``
        is the billing email (Paystack requires the email the authorization was
        created with; Stripe ignores it).

        Providers that do not support tokenized recurring charges raise
        ``AppException`` with ``PAYMENT_PROVIDER_ERROR``.
        """
        ...
