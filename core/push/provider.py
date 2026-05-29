from __future__ import annotations

from typing import Protocol

from core.push.types import (
    PushClientConfig,
    PushMessage,
    PushSendResult,
    PushSubscriptionInfo,
)


class PushProvider(Protocol):
    """Push transport contract.

    A provider knows how to (a) tell the frontend what it needs to register
    a device (:meth:`client_config`) and (b) deliver one message to one
    device (:meth:`send`). Everything above this — subscription storage, the
    queued fan-out task, preference gating — is provider-independent, so
    swapping Web Push for FCM/APNs/OneSignal is a new provider class plus a
    ``PUSH_PROVIDER`` env change, nothing more.

    :meth:`send` is synchronous: real providers do a blocking HTTPS call, so
    the caller runs it in a worker thread (``asyncio.to_thread``).
    """

    provider_name: str

    def client_config(self) -> PushClientConfig:
        """What the frontend needs to subscribe a device with this provider."""
        ...

    def send(
        self, *, subscription: PushSubscriptionInfo, message: PushMessage
    ) -> PushSendResult:
        """Deliver one message to one device. Never raises — returns a result."""
        ...
