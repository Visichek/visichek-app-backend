from __future__ import annotations

import logging

from core.push.types import (
    PushClientConfig,
    PushMessage,
    PushSendResult,
    PushSubscriptionInfo,
)

logger = logging.getLogger(__name__)


class WebPushProvider:
    """Web Push (VAPID) transport via ``pywebpush``.

    Encrypts each notification to the device's ECDH keys and POSTs it to the
    browser's push service (FCM/Mozilla/Apple endpoints). The VAPID key pair
    identifies this application server; the public key is what the frontend
    passes to ``pushManager.subscribe`` as ``applicationServerKey``.
    """

    provider_name = "webpush"

    def __init__(
        self, *, public_key: str, private_key: str, subject: str, ttl_seconds: int
    ) -> None:
        self._public_key = public_key
        self._private_key = private_key
        self._subject = subject
        self._ttl_seconds = ttl_seconds
        self._vapid = None  # lazily built; see _get_vapid()

    # ── client-facing config ────────────────────────────────────────

    def client_config(self) -> PushClientConfig:
        return PushClientConfig(
            provider=self.provider_name, public_key=self._public_key
        )

    # ── VAPID signer (lazy) ──────────────────────────────────────────

    def _get_vapid(self):
        """Cache a ``Vapid01`` built from the raw base64url private key.

        ``py_vapid`` derives the public key from the private one, so only
        the private key is needed here. Imported lazily so a node that never
        sends (e.g. a web replica) doesn't require ``pywebpush`` installed.
        """
        if self._vapid is None:
            from py_vapid import Vapid01  # type: ignore[import-not-found]

            self._vapid = Vapid01.from_raw(
                private_raw=self._private_key.encode("utf-8")
            )
        return self._vapid

    # ── send ─────────────────────────────────────────────────────────

    def send(
        self, *, subscription: PushSubscriptionInfo, message: PushMessage
    ) -> PushSendResult:
        import json

        from pywebpush import WebPushException, webpush  # type: ignore[import-not-found]

        if (
            not subscription.endpoint
            or not subscription.p256dh
            or not subscription.auth
        ):
            return PushSendResult(
                ok=False, gone=False, detail="incomplete_subscription"
            )

        subscription_info = {
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        }
        payload = json.dumps(
            {
                "title": message.title,
                "body": message.body,
                "url": message.url,
                "id": message.notification_id,
                "type": message.type,
            }
        )

        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=self._get_vapid(),
                vapid_claims={"sub": self._subject},
                ttl=self._ttl_seconds,
            )
            return PushSendResult(ok=True)
        except WebPushException as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            gone = status_code in (404, 410)
            logger.warning(
                "webpush: send failed status=%s endpoint=%s: %s",
                status_code,
                subscription.endpoint,
                exc,
            )
            return PushSendResult(ok=False, gone=gone, detail=str(status_code))
        except Exception:
            logger.warning("webpush: unexpected send error", exc_info=True)
            return PushSendResult(ok=False, gone=False, detail="error")
