from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class PushSubscriptionInfo:
    """A single device's push channel — provider-agnostic.

    For Web Push these map to the browser ``PushSubscription`` (``endpoint``
    + ECDH keys). For a token-based provider (FCM / APNs / OneSignal) a
    future provider can ignore the ECDH keys and read the device token out
    of ``endpoint`` (or extend this type). Keeping one shape here means the
    rest of the stack — repository, service, send task — never changes when
    the provider does.
    """

    endpoint: str
    p256dh: Optional[str] = None
    auth: Optional[str] = None


@dataclass(frozen=True)
class PushMessage:
    """The notification to deliver, independent of how it's transported."""

    title: str
    body: str
    url: str = "/"
    notification_id: Optional[str] = None
    type: str = "info"


@dataclass(frozen=True)
class PushSendResult:
    """Outcome of a single send.

    ``gone`` is True when the provider reports the subscription is dead
    (e.g. Web Push 404/410) and the caller should prune the stored row.
    """

    ok: bool
    gone: bool = False
    detail: Optional[str] = None


@dataclass(frozen=True)
class PushClientConfig:
    """Provider-agnostic config the frontend needs to subscribe.

    ``provider`` lets the client branch (``webpush`` → use
    ``applicationServerKey``; ``fcm`` → init the Firebase SDK with
    ``params``). ``public_key`` is the Web Push application server key;
    other providers leave it ``None`` and put their bits in ``params``.
    """

    provider: str
    public_key: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)

    def to_response(self) -> Dict[str, Any]:
        """Flatten for the API response (case middleware camel-cases keys)."""
        payload: Dict[str, Any] = {"provider": self.provider}
        if self.public_key is not None:
            payload["public_key"] = self.public_key
        if self.params:
            payload["params"] = self.params
        return payload
