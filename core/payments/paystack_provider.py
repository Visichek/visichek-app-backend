from __future__ import annotations

import hashlib
import hmac
import json

import requests  # type: ignore[import-untyped]

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


class PaystackPaymentProvider(PaymentProvider):
    provider_name = PaymentProviderName.PAYSTACK.value

    def __init__(self, *, secret_key: str) -> None:
        self._secret_key = secret_key
        self._base_url = "https://api.paystack.co"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._secret_key}",
            "Content-Type": "application/json",
        }

    def create_intent(self, payload: PaymentIntentRequest) -> PaymentIntentResponse:
        # Paystack amounts are already in the minor unit (kobo/pesewa/cent),
        # so amount_minor is passed through directly — unlike Flutterwave.
        response = requests.post(
            f"{self._base_url}/transaction/initialize",
            json={
                "reference": payload.reference,
                "amount": payload.amount_minor,
                "currency": payload.currency,
                "email": payload.customer_email,
                "callback_url": payload.metadata.get("redirect_url")
                if payload.metadata
                else None,
                "metadata": payload.metadata or {},
            },
            headers=self._headers(),
            timeout=15,
        )
        data = response.json()
        if response.status_code >= 400 or not data.get("status"):
            raise AppException(
                status_code=502,
                code=ErrorCode.PAYMENT_PROVIDER_ERROR,
                message="Paystack intent creation failed",
                details=data,
            )

        checkout_url = data.get("data", {}).get("authorization_url")
        return PaymentIntentResponse(
            provider=PaymentProviderName.PAYSTACK,
            reference=payload.reference,
            status=PaymentStatus.PENDING,
            checkout_url=checkout_url,
            provider_payload=data,
        )

    def verify_webhook(self, *, body: bytes, headers: dict[str, str]) -> WebhookEvent:
        provided = headers.get("x-paystack-signature") or headers.get(
            "X-Paystack-Signature"
        )
        expected = hmac.new(
            self._secret_key.encode("utf-8"), body, hashlib.sha512
        ).hexdigest()
        if not provided or not hmac.compare_digest(provided, expected):
            raise AppException(
                status_code=401,
                code=ErrorCode.PAYMENT_WEBHOOK_INVALID,
                message="Invalid Paystack webhook signature",
            )

        payload = json.loads(body.decode("utf-8"))
        event_data = payload.get("data") or {}
        event_id = str(event_data.get("id") or event_data.get("reference") or "unknown")
        event_type = str(payload.get("event") or "unknown")
        return WebhookEvent(
            provider=PaymentProviderName.PAYSTACK,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
        )

    def fetch_transaction(self, *, reference: str) -> PaymentTransaction:
        response = requests.get(
            f"{self._base_url}/transaction/verify/{reference}",
            headers=self._headers(),
            timeout=15,
        )
        data = response.json()
        if response.status_code >= 400 or not data.get("status"):
            raise AppException(
                status_code=502,
                code=ErrorCode.PAYMENT_PROVIDER_ERROR,
                message="Paystack verify failed",
                details=data,
            )

        status = str(data.get("data", {}).get("status", "")).lower()
        mapped = (
            PaymentStatus.SUCCEEDED if status == "success" else PaymentStatus.PENDING
        )
        return PaymentTransaction(
            provider=PaymentProviderName.PAYSTACK,
            reference=reference,
            status=mapped,
            raw=data,
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
        # Server-side charge of a saved card via the reusable authorization
        # code (instrument_ref) captured on the first payment. No customer
        # interaction. Amount is in the minor unit (kobo), passed through
        # directly. customer_ref is unused — Paystack charges by auth code.
        response = requests.post(
            f"{self._base_url}/transaction/charge_authorization",
            json={
                "authorization_code": instrument_ref,
                "email": email,
                "amount": amount_minor,
                "currency": currency,
                "reference": reference,
                "metadata": metadata or {},
            },
            headers=self._headers(),
            timeout=15,
        )
        data = response.json()
        if response.status_code >= 400 or not data.get("status"):
            raise AppException(
                status_code=502,
                code=ErrorCode.PAYMENT_PROVIDER_ERROR,
                message="Paystack recurring charge failed",
                details=data,
            )

        # data.data.status is the transaction outcome: "success" | "failed" |
        # "send_otp" | "pending" | "abandoned". Only "success" is a real charge.
        status = str(data.get("data", {}).get("status", "")).lower()
        if status == "success":
            mapped = PaymentStatus.SUCCEEDED
        elif status == "failed":
            mapped = PaymentStatus.FAILED
        else:
            mapped = PaymentStatus.PENDING
        return PaymentTransaction(
            provider=PaymentProviderName.PAYSTACK,
            reference=reference,
            status=mapped,
            raw=data,
        )

    def refund(
        self, *, reference: str, amount_minor: int | None = None
    ) -> PaymentTransaction:
        payload: dict[str, object] = {"transaction": reference}
        if amount_minor is not None:
            payload["amount"] = amount_minor

        response = requests.post(
            f"{self._base_url}/refund",
            json=payload,
            headers=self._headers(),
            timeout=15,
        )
        data = response.json()
        if response.status_code >= 400 or not data.get("status"):
            raise AppException(
                status_code=502,
                code=ErrorCode.PAYMENT_PROVIDER_ERROR,
                message="Paystack refund failed",
                details=data,
            )

        return PaymentTransaction(
            provider=PaymentProviderName.PAYSTACK,
            reference=reference,
            status=PaymentStatus.REFUNDED,
            raw=data,
        )
