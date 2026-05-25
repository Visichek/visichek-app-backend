from __future__ import annotations

import logging
from threading import Lock

from core.payments.app_provider import AppCheckoutPaymentProvider
from core.payments.flutterwave_provider import FlutterwavePaymentProvider
from core.payments.paystack_provider import PaystackPaymentProvider
from core.payments.provider import PaymentProvider
from core.payments.stripe_provider import StripePaymentProvider
from core.settings import get_settings

logger = logging.getLogger(__name__)


class PaymentManager:
    _instance: "PaymentManager | None" = None
    _lock = Lock()

    def __init__(
        self, providers: dict[str, PaymentProvider], default_provider: str
    ) -> None:
        self._providers = providers
        self._default_provider = default_provider

    @classmethod
    def configure_from_settings(cls) -> "PaymentManager":
        settings = get_settings()
        providers: dict[str, PaymentProvider] = {}

        if settings.flutterwave_secret_key:
            # In production a Flutterwave provider with no webhook secret
            # can't verify webhooks (verify_webhook fails closed), so a
            # forged webhook would otherwise be the only way to "confirm"
            # a payment. Refuse to register it at all. The startup posture
            # check (core/security_posture.py) turns this into a hard boot
            # failure so the misconfiguration is loud, not silent.
            if settings.is_production and not settings.flutterwave_webhook_secret_hash:
                logger.error(
                    "Flutterwave secret key is set but FLW_WEBHOOK_SECRET_HASH "
                    "is missing; refusing to register Flutterwave in production."
                )
            else:
                try:
                    providers["flutterwave"] = FlutterwavePaymentProvider(
                        secret_key=settings.flutterwave_secret_key,
                        webhook_secret_hash=settings.flutterwave_webhook_secret_hash,
                    )
                except Exception as err:
                    logger.warning("Flutterwave provider unavailable: %s", err)

        if settings.stripe_secret_key:
            try:
                providers["stripe"] = StripePaymentProvider(
                    secret_key=settings.stripe_secret_key,
                    webhook_secret=settings.stripe_webhook_secret,
                )
            except Exception as err:
                logger.warning("Stripe provider unavailable: %s", err)

        if settings.paystack_secret_key:
            try:
                providers["paystack"] = PaystackPaymentProvider(
                    secret_key=settings.paystack_secret_key,
                )
            except Exception as err:
                logger.warning("Paystack provider unavailable: %s", err)

        # The ``app`` provider is a SIMULATOR — it confirms payments with a
        # button click and proves nothing about settlement. It is registered
        # automatically outside production so local/dev/test can issue a
        # checkout without external keys. In production it is registered ONLY
        # when PAYMENT_APP_MODE_ENABLED is explicitly set (an audited
        # break-glass), so a real deployment can never silently fall back to
        # "click to mark paid".
        if (not settings.is_production) or settings.payment_app_mode_enabled:
            providers["app"] = AppCheckoutPaymentProvider(
                base_url=(getattr(settings, "app_base_url", "") or "").strip()
            )
        elif settings.is_production:
            logger.warning(
                "App checkout simulator is disabled in production "
                "(set PAYMENT_APP_MODE_ENABLED=true to force-enable)."
            )

        default_provider = settings.payment_default_provider
        if default_provider not in providers:
            # Prefer any real provider; fall back to app mode only if it is
            # registered (i.e. outside production or break-glass enabled).
            for preferred in ("stripe", "flutterwave", "paystack", "app"):
                if preferred in providers:
                    default_provider = preferred
                    break

        with cls._lock:
            cls._instance = cls(providers=providers, default_provider=default_provider)
            return cls._instance

    @classmethod
    def get_instance(cls) -> "PaymentManager":
        if cls._instance is None:
            return cls.configure_from_settings()
        return cls._instance

    def get_provider(self, provider: str | None = None) -> PaymentProvider:
        key = (provider or self._default_provider).lower()
        if key not in self._providers:
            raise ValueError(f"Unsupported payment provider '{provider}'")
        return self._providers[key]

    def has_provider(self, provider: str) -> bool:
        return provider.lower() in self._providers

    def available_providers(self) -> list[str]:
        return list(self._providers.keys())

    @property
    def default_provider(self) -> str:
        return self._default_provider
