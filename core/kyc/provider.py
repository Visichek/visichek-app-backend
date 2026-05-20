from __future__ import annotations

from typing import Protocol

from core.kyc.types import (
    KYCInitiateRequest,
    KYCInitiateResponse,
    KYCVerificationDetails,
    KYCWebhookEvent,
)


class KYCProvider(Protocol):
    """Behaviour every KYC provider must expose.

    Methods are async because the underlying clients all hit HTTP. The
    signatures are deliberately small — providers expose a ton of knobs
    but visichek only needs the kiosk-flow surface (start a session,
    poll a session, parse a webhook).
    """

    provider_name: str

    async def initiate(self, request: KYCInitiateRequest) -> KYCInitiateResponse: ...

    async def fetch_details(self, reference_id: str) -> KYCVerificationDetails: ...

    def parse_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> KYCWebhookEvent: ...
