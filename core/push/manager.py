from __future__ import annotations

from threading import Lock

from core.push.provider import PushProvider
from core.push.types import PushClientConfig, PushMessage, PushSendResult, PushSubscriptionInfo
from core.settings import get_settings


class PushManager:
    """Singleton facade over the active :class:`PushProvider`.

    Mirrors ``EmailManager`` / ``DocumentStorageManager`` / ``PaymentManager``:
    one process-wide instance, configured from settings on first use. Callers
    (the ``push.send`` task, the config route) talk to the manager and never
    import a concrete provider, so the provider is swappable via
    ``PUSH_PROVIDER`` without touching any call site.
    """

    _instance: "PushManager | None" = None
    _lock = Lock()

    def __init__(self, provider: PushProvider) -> None:
        self._provider = provider

    @classmethod
    def configure(cls, provider: PushProvider) -> "PushManager":
        with cls._lock:
            cls._instance = cls(provider)
            return cls._instance

    @classmethod
    def configure_from_settings(cls) -> "PushManager":
        settings = get_settings()
        backend = (getattr(settings, "push_provider", "webpush") or "webpush").lower()

        if backend == "webpush":
            from core.push.webpush_provider import WebPushProvider

            provider: PushProvider = WebPushProvider(
                public_key=settings.vapid_public_key,
                private_key=settings.vapid_private_key,
                subject=settings.vapid_subject,
                ttl_seconds=settings.push_ttl_seconds,
            )
        else:
            raise RuntimeError(
                f"Unknown PUSH_PROVIDER '{backend}'. Supported: webpush."
            )

        return cls.configure(provider)

    @classmethod
    def get_instance(cls) -> "PushManager":
        if cls._instance is None:
            return cls.configure_from_settings()
        return cls._instance

    @property
    def provider(self) -> PushProvider:
        return self._provider

    # ── facade ───────────────────────────────────────────────────────

    def client_config(self) -> PushClientConfig:
        """Provider-agnostic config the frontend needs to register a device."""
        return self._provider.client_config()

    def send(
        self, *, subscription: PushSubscriptionInfo, message: PushMessage
    ) -> PushSendResult:
        """Deliver one message to one device (synchronous — run in a thread)."""
        return self._provider.send(subscription=subscription, message=message)
