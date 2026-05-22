"""App-mode fallback payment provider — simulates a hosted checkout when
Stripe / Flutterwave credentials are absent or invalid.

State is held in the ``checkout_sessions`` collection, not in an external
system, so most PaymentProvider operations are intentionally minimal.
"""

from __future__ import annotations

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


class AppCheckoutPaymentProvider(PaymentProvider):
    """Issue checkout URLs pointing back at the app's own simulator page."""

    provider_name = PaymentProviderName.APP.value

    def __init__(self, *, base_url: str = "") -> None:
        self._base_url = base_url.rstrip("/")

    def _url_for(self, reference: str) -> str:
        path = f"/payments/app-checkout/{reference}"
        return f"{self._base_url}{path}" if self._base_url else path

    def create_intent(self, payload: PaymentIntentRequest) -> PaymentIntentResponse:
        return PaymentIntentResponse(
            provider=PaymentProviderName.APP,
            reference=payload.reference,
            status=PaymentStatus.PENDING,
            checkout_url=self._url_for(payload.reference),
            provider_payload={
                "mode": "app_test",
                "amount_minor": payload.amount_minor,
                "currency": payload.currency,
            },
        )

    def verify_webhook(self, *, body: bytes, headers: dict[str, str]) -> WebhookEvent:
        # App mode has no external webhook — the simulator page POSTs directly
        # to an authenticated internal endpoint. Treat accidental calls as invalid.
        raise AppException(
            status_code=400,
            code=ErrorCode.PAYMENT_WEBHOOK_INVALID,
            message="App provider does not accept external webhooks",
        )

    def fetch_transaction(self, *, reference: str) -> PaymentTransaction:
        # The canonical state lives in the CheckoutSession record; callers
        # that need status should read that instead of going through the
        # provider abstraction.
        raise AppException(
            status_code=400,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="App provider has no external transaction to fetch",
            details={"reference": reference},
        )

    def refund(
        self, *, reference: str, amount_minor: int | None = None
    ) -> PaymentTransaction:
        return PaymentTransaction(
            provider=PaymentProviderName.APP,
            reference=reference,
            status=PaymentStatus.REFUNDED,
            raw={"mode": "app_test", "refunded_amount_minor": amount_minor},
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
        # App mode has no saved instrument; the simulator drives completion.
        raise AppException(
            status_code=400,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="App provider does not support recurring charges",
        )
