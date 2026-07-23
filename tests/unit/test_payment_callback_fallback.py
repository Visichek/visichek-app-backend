"""Post-payment browser callback must always point at the frontend.

Every hosted-checkout initialize call (Paystack ``callback_url``,
Flutterwave ``redirect_url``) must carry a frontend return-page URL even
when the caller never set ``metadata.redirect_url`` and the deployment
never exported ``PAYMENT_CALLBACK_URL``. Otherwise the provider falls
back to the Callback URL configured in its own dashboard — which, when
misconfigured, bounces the customer's browser to the backend.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.payments.flutterwave_provider import FlutterwavePaymentProvider
from core.payments.paystack_provider import PaystackPaymentProvider
from core.payments.types import PaymentIntentRequest
from core.settings import get_settings

DEFAULT_RETURN_URL = "https://client.visichek.app/app/billing/checkout/return"


@pytest.fixture
def clean_settings(monkeypatch):
    """Clear the callback-related env vars and the settings cache."""
    monkeypatch.delenv("PAYMENT_CALLBACK_URL", raising=False)
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def _intent(metadata: dict | None = None) -> PaymentIntentRequest:
    return PaymentIntentRequest(
        amount_minor=500000,
        currency="NGN",
        reference="chk_abc123",
        customer_email="billing@example.com",
        metadata=metadata,
    )


def _paystack_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "status": True,
        "data": {"authorization_url": "https://checkout.paystack.com/xyz"},
    }
    return response


def _flutterwave_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "status": "success",
        "data": {"link": "https://checkout.flutterwave.com/xyz"},
    }
    return response


# ── Settings resolution ────────────────────────────────────────────────


def test_settings_default_callback_is_frontend_return_page(clean_settings):
    assert get_settings().payment_callback_url == DEFAULT_RETURN_URL


def test_settings_callback_derives_from_app_base_url(clean_settings):
    clean_settings.setenv("APP_BASE_URL", "https://staging.visichek.app/")
    get_settings.cache_clear()
    assert (
        get_settings().payment_callback_url
        == "https://staging.visichek.app/app/billing/checkout/return"
    )


def test_settings_explicit_callback_env_wins(clean_settings):
    clean_settings.setenv("APP_BASE_URL", "https://staging.visichek.app")
    clean_settings.setenv("PAYMENT_CALLBACK_URL", "https://custom.example.com/return")
    get_settings.cache_clear()
    assert get_settings().payment_callback_url == "https://custom.example.com/return"


# ── Paystack ───────────────────────────────────────────────────────────


def test_paystack_intent_falls_back_to_settings_callback(clean_settings):
    provider = PaystackPaymentProvider(secret_key="sk_test_x")
    with patch(
        "core.payments.paystack_provider.requests.post",
        return_value=_paystack_response(),
    ) as mock_post:
        provider.create_intent(_intent(metadata={"tenant_id": "t1"}))
    assert mock_post.call_args.kwargs["json"]["callback_url"] == DEFAULT_RETURN_URL


def test_paystack_intent_with_no_metadata_still_sends_callback(clean_settings):
    provider = PaystackPaymentProvider(secret_key="sk_test_x")
    with patch(
        "core.payments.paystack_provider.requests.post",
        return_value=_paystack_response(),
    ) as mock_post:
        provider.create_intent(_intent(metadata=None))
    assert mock_post.call_args.kwargs["json"]["callback_url"] == DEFAULT_RETURN_URL


def test_paystack_intent_metadata_redirect_url_wins(clean_settings):
    provider = PaystackPaymentProvider(secret_key="sk_test_x")
    with patch(
        "core.payments.paystack_provider.requests.post",
        return_value=_paystack_response(),
    ) as mock_post:
        provider.create_intent(
            _intent(metadata={"redirect_url": "https://custom.example.com/done"})
        )
    assert (
        mock_post.call_args.kwargs["json"]["callback_url"]
        == "https://custom.example.com/done"
    )


# ── Flutterwave ────────────────────────────────────────────────────────


def test_flutterwave_intent_falls_back_to_settings_callback(clean_settings):
    provider = FlutterwavePaymentProvider(secret_key="fw_test_x")
    with patch(
        "core.payments.flutterwave_provider.requests.post",
        return_value=_flutterwave_response(),
    ) as mock_post:
        provider.create_intent(_intent(metadata={"tenant_id": "t1"}))
    assert mock_post.call_args.kwargs["json"]["redirect_url"] == DEFAULT_RETURN_URL


def test_flutterwave_intent_metadata_redirect_url_wins(clean_settings):
    provider = FlutterwavePaymentProvider(secret_key="fw_test_x")
    with patch(
        "core.payments.flutterwave_provider.requests.post",
        return_value=_flutterwave_response(),
    ) as mock_post:
        provider.create_intent(
            _intent(metadata={"redirect_url": "https://custom.example.com/done"})
        )
    assert (
        mock_post.call_args.kwargs["json"]["redirect_url"]
        == "https://custom.example.com/done"
    )
