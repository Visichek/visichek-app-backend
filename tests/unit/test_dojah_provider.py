from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from core.kyc.dojah_provider import DojahKYCProvider


SECRET = "test-dojah-secret"


def _provider() -> DojahKYCProvider:
    return DojahKYCProvider(app_id="test-app-id", secret_key=SECRET)


def _hmac_sha256(payload: bytes) -> str:
    return hmac.new(
        SECRET.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()


def _payload() -> dict[str, Any]:
    return {
        "event": "verification.completed",
        "data": {
            "reference_id": "DJ-123456",
            "verification_status": "Completed",
            "metadata": {"checkin_id": "665f0c7dd43c2b3ef0871111"},
        },
    }


def test_dojah_v1_accepts_raw_body_signature() -> None:
    body = json.dumps(_payload(), separators=(",", ":")).encode("utf-8")
    event = _provider().parse_webhook(
        body=body,
        headers={"x-dojah-signature": _hmac_sha256(body)},
    )

    assert event.signature_valid is True
    assert event.reference_id == "DJ-123456"
    assert event.details is not None
    assert event.details.status == "success"


def test_dojah_v1_accepts_json_stringify_signature_for_pretty_body() -> None:
    payload = _payload()
    body = json.dumps(payload, indent=2).encode("utf-8")
    signed_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    event = _provider().parse_webhook(
        body=body,
        headers={"x-dojah-signature": _hmac_sha256(signed_payload)},
    )

    assert event.signature_valid is True
    assert event.reference_id == "DJ-123456"


def test_dojah_invalid_v1_does_not_fall_back_to_v2() -> None:
    body = json.dumps(_payload(), separators=(",", ":")).encode("utf-8")
    v2 = hashlib.sha256(SECRET.encode("utf-8")).hexdigest()

    event = _provider().parse_webhook(
        body=body,
        headers={
            "x-dojah-signature": "0" * 64,
            "x-dojah-signature-v2": v2,
        },
    )

    assert event.signature_valid is False
