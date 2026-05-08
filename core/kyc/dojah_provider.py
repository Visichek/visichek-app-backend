"""Dojah KYC provider implementation.

Auth: ``AppId: <app_id>`` + ``Authorization: <secret_key>`` (no
``Bearer`` prefix). Sandbox URL is ``https://sandbox.dojah.io``,
production is ``https://api.dojah.io``. Webhooks include either
``x-dojah-signature`` (HMAC-SHA256 of the raw request body keyed on
the secret) or ``x-dojah-signature-v2`` (HMAC-SHA256 of the secret
itself, body-independent — used as a "this came from us" marker).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional

import httpx

from core.kyc.provider import KYCProvider
from core.kyc.types import (
    KYCInitiateRequest,
    KYCInitiateResponse,
    KYCVerificationDetails,
    KYCWebhookEvent,
)

logger = logging.getLogger(__name__)

DOJAH_SANDBOX_BASE = "https://sandbox.dojah.io"
DOJAH_PRODUCTION_BASE = "https://api.dojah.io"


class DojahKYCProvider:
    """Single-instance provider configured from app settings.

    The provider does *not* call any "create verification" REST endpoint
    on initiate — Dojah's widget creates the verification client-side
    when the visitor finishes the flow. ``initiate`` just builds the
    widget config the frontend needs (app_id, public_key, methods,
    metadata) and returns the visichek-side correlator we'll store on
    the check-in. The webhook arrives with the Dojah-issued
    ``reference_id`` plus our ``metadata`` payload, and we use that to
    look the check-in back up.
    """

    provider_name = "dojah"

    def __init__(
        self,
        *,
        app_id: str,
        secret_key: str,
        public_key: Optional[str] = None,
        base_url: str = DOJAH_SANDBOX_BASE,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not app_id or not secret_key:
            raise ValueError(
                "DojahKYCProvider requires both DOJAH_APP_ID and DOJAH_SECRET_KEY"
            )
        self._app_id = app_id
        self._secret_key = secret_key
        self._public_key = public_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    # ── Public surface ───────────────────────────────────────────────

    async def initiate(
        self, request: KYCInitiateRequest
    ) -> KYCInitiateResponse:
        """Build the kiosk widget config.

        We use ``checkin_id`` as the visichek-side correlator. It rides
        in the widget's ``metadata`` payload and re-surfaces in the
        webhook. The Dojah ``reference_id`` is unknown until the widget
        completes, so we don't have it here — fetch it via webhook /
        polling and link it to the check-in afterwards.
        """
        widget_config: dict[str, Any] = {
            "app_id": self._app_id,
            "public_key": self._public_key,
            "type": "custom",
            "config": {
                "widget_id": "kyc_widget",
                # methods is the visitor's allowed verification flow.
                # Empty → use Dojah's default kiosk preset.
                "pages": [
                    {"page": "government-data", "config": {}},
                    {"page": "selfie", "config": {}},
                ],
                "review_process": "automatic",
            },
            "metadata": {
                "tenant_id": request.tenant_id,
                "checkin_id": request.checkin_id,
                # Echo of the visitor identity so the kiosk widget can
                # prefill name / contact rather than asking the visitor
                # to retype them.
                "full_name": request.visitor_full_name,
                "phone": request.visitor_phone,
                "email": request.visitor_email,
                **request.metadata,
            },
            "base_url": self._base_url,
        }
        if request.methods:
            widget_config["config"]["methods"] = list(request.methods)
        if request.callback_url:
            widget_config["config"]["redirect_url"] = request.callback_url

        return KYCInitiateResponse(
            # Visichek correlates on checkin_id until Dojah issues a
            # reference_id during the actual widget completion.
            reference_id=request.checkin_id,
            widget_config=widget_config,
            expires_at=None,
        )

    async def fetch_details(
        self, reference_id: str
    ) -> KYCVerificationDetails:
        """Polling fallback for the webhook.

        Dojah's verification-detail endpoint:
        ``GET /api/v1/kyc/verification?reference_id=<DJ-...>``
        """
        url = f"{self._base_url}/api/v1/kyc/verification"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                url,
                params={"reference_id": reference_id},
                headers=self._auth_headers(),
            )
        response.raise_for_status()
        body = response.json() or {}
        entity = body.get("entity") or body.get("data") or body
        return _entity_to_details(reference_id=reference_id, entity=entity)

    def parse_webhook(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
    ) -> KYCWebhookEvent:
        """Verify signature, decode payload, return a normalised event.

        Dojah supports two signature schemes — we accept either. v1 is
        an HMAC of the raw body (the only scheme that protects against
        replay or payload tampering). v2 is an HMAC of the secret with
        no body input, useful only as a "this came from a Dojah-shaped
        client" marker. We treat v1 as authoritative when present and
        fall back to v2 otherwise; the caller can still reject events
        that arrive without v1 in production via the
        ``DOJAH_REQUIRE_V1_SIGNATURE`` env var.
        """
        signature_valid = self._signature_ok(body=body, headers=headers)
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception as exc:
            logger.warning("dojah webhook: invalid JSON body: %s", exc)
            payload = {}

        event_type = str(payload.get("event") or payload.get("type") or "")
        data = (
            payload.get("data")
            or payload.get("entity")
            or payload.get("verification")
            or payload
        )
        reference_id = str(
            data.get("reference_id")
            or payload.get("reference_id")
            or ""
        )
        event_id = str(
            payload.get("id")
            or payload.get("event_id")
            or _hash_payload(body)
        )

        details = (
            _entity_to_details(reference_id=reference_id, entity=data)
            if reference_id
            else None
        )

        return KYCWebhookEvent(
            provider=self.provider_name,
            event_id=event_id,
            event_type=event_type,
            reference_id=reference_id,
            received_at=int(time.time()),
            raw_payload=payload if isinstance(payload, dict) else {},
            signature_valid=signature_valid,
            details=details,
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        return {
            "AppId": self._app_id,
            "Authorization": self._secret_key,
            "Accept": "application/json",
        }

    def _signature_ok(self, *, body: bytes, headers: dict[str, str]) -> bool:
        # Header lookup is case-insensitive at the HTTP layer; downstream
        # frameworks normalise to lowercase. Try both for safety.
        v1 = headers.get("x-dojah-signature") or headers.get("X-Dojah-Signature")
        v2 = headers.get("x-dojah-signature-v2") or headers.get(
            "X-Dojah-Signature-V2"
        )
        if v1:
            expected = hmac.new(
                self._secret_key.encode("utf-8"),
                msg=body,
                digestmod=hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, v1)
        if v2:
            expected = hmac.new(
                self._secret_key.encode("utf-8"),
                msg=self._secret_key.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, v2)
        return False


def _hash_payload(body: bytes) -> str:
    """Stable fallback event_id when Dojah doesn't include one."""
    return hashlib.sha256(body or b"").hexdigest()[:32]


def _entity_to_details(
    *, reference_id: str, entity: dict[str, Any]
) -> KYCVerificationDetails:
    """Map a Dojah verification document into our normalised shape.

    Dojah groups results under ``selfie``, ``government_data``, etc.;
    we collapse the bits the kiosk flow cares about (name, dob, id
    number, image links) to top-level fields. The full document is
    preserved under ``data`` so an auditor can replay anything we
    glossed over.
    """
    status_raw = (
        str(
            entity.get("status")
            or entity.get("verification_status")
            or entity.get("overall_status")
            or ""
        )
        .lower()
        .strip()
    )
    status = _normalise_status(status_raw)

    selfie = entity.get("selfie") or {}
    gov = entity.get("government_data") or entity.get("government") or {}

    return KYCVerificationDetails(
        reference_id=reference_id,
        status=status,
        confidence=_safe_float(selfie.get("confidence_value"))
        or _safe_float(entity.get("confidence")),
        extracted_full_name=_first_truthy(
            gov.get("full_name"),
            gov.get("first_name") and gov.get("last_name")
            and f"{gov.get('first_name')} {gov.get('last_name')}",
            entity.get("full_name"),
        ),
        extracted_dob=_first_truthy(gov.get("date_of_birth"), entity.get("dob")),
        extracted_id_number=_first_truthy(
            gov.get("id_number"), gov.get("nin"), entity.get("id_number")
        ),
        extracted_id_type=_first_truthy(
            gov.get("id_type"), entity.get("id_type")
        ),
        selfie_url=_first_truthy(selfie.get("image_url"), selfie.get("url")),
        id_image_url=_first_truthy(gov.get("image_url"), gov.get("url")),
        failure_reason=_first_truthy(
            entity.get("reason"), entity.get("failure_reason")
        ),
        data=entity if isinstance(entity, dict) else {},
    )


def _normalise_status(raw: str) -> str:
    if raw in ("completed", "success", "approved", "passed"):
        return "success"
    if raw in ("failed", "rejected", "denied"):
        return "failed"
    if raw in ("expired", "abandoned"):
        return "expired"
    if raw in ("ongoing", "pending", "in_progress"):
        return "ongoing"
    return raw or "ongoing"


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_truthy(*values: Any) -> Optional[str]:
    for v in values:
        if v:
            return str(v)
    return None


# Public re-exports — make sure ``KYCProvider`` import is retained
# even if a linter deems it unused (the Protocol structurally validates
# this class).
_PROTOCOL_GUARD: type[KYCProvider] = DojahKYCProvider  # type: ignore[assignment]
