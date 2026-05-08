"""KYC manager singleton.

Mirrors :class:`core.payments.manager.PaymentManager` — configured at
boot from ``Settings``, exposes ``get_provider(name)`` for callers.
``configure_from_settings`` is tolerant of partial credentials so the
backend can boot even when the secret keys aren't set yet (KYC simply
becomes unavailable).
"""

from __future__ import annotations

import logging
from threading import Lock

from core.kyc.dojah_provider import (
    DOJAH_PRODUCTION_BASE,
    DOJAH_SANDBOX_BASE,
    DojahKYCProvider,
)
from core.kyc.provider import KYCProvider
from core.settings import get_settings

logger = logging.getLogger(__name__)


class KYCManager:
    _instance: "KYCManager | None" = None
    _lock = Lock()

    def __init__(
        self,
        providers: dict[str, KYCProvider],
        default_provider: str = "dojah",
    ) -> None:
        self._providers = providers
        self._default_provider = default_provider

    @classmethod
    def configure_from_settings(cls) -> "KYCManager":
        settings = get_settings()
        providers: dict[str, KYCProvider] = {}

        if settings.dojah_app_id and settings.dojah_secret_key:
            base_url = (
                settings.dojah_base_url
                or (
                    DOJAH_PRODUCTION_BASE
                    if settings.is_production
                    else DOJAH_SANDBOX_BASE
                )
            )
            try:
                providers["dojah"] = DojahKYCProvider(
                    app_id=settings.dojah_app_id,
                    secret_key=settings.dojah_secret_key,
                    public_key=settings.dojah_public_key,
                    base_url=base_url,
                )
            except Exception as exc:
                logger.warning("Dojah provider unavailable: %s", exc)

        with cls._lock:
            cls._instance = cls(
                providers=providers, default_provider="dojah"
            )
            return cls._instance

    @classmethod
    def get_instance(cls) -> "KYCManager":
        if cls._instance is None:
            return cls.configure_from_settings()
        return cls._instance

    def has_provider(self, name: str) -> bool:
        return name.lower() in self._providers

    def get_provider(self, name: str | None = None) -> KYCProvider:
        key = (name or self._default_provider).lower()
        if key not in self._providers:
            raise ValueError(
                f"KYC provider '{key}' is not configured. "
                "Set DOJAH_APP_ID and DOJAH_SECRET_KEY to enable."
            )
        return self._providers[key]

    def available_providers(self) -> list[str]:
        return list(self._providers.keys())

    @property
    def default_provider(self) -> str:
        return self._default_provider
