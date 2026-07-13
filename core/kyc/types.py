"""Provider-agnostic KYC value types.

These dataclasses are the shape providers return — Dojah today, others
tomorrow. Routes / services only ever import from this module so a
provider swap doesn't ripple into business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class KYCInitiateRequest:
    """Inputs to an ``initiate`` call.

    ``checkin_id`` is the visichek-side correlator we'll write back into
    the provider's metadata so the webhook can find the originating
    check-in. ``methods`` is the ordered list of verification types the
    kiosk will run (e.g. ``["nin", "selfie"]``); empty means the
    provider picks defaults.
    """

    tenant_id: str
    checkin_id: str
    visitor_full_name: str
    visitor_phone: str
    visitor_email: Optional[str] = None
    methods: tuple[str, ...] = ()
    callback_url: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KYCInitiateResponse:
    """What the provider gives back after we ask to start a session.

    ``reference_id`` is the canonical correlator we persist on the
    visit_session / checkin record. ``widget_config`` is whatever extra
    glue the frontend needs to launch the provider's widget — for Dojah
    that's ``{ app_id, public_key, reference_id, methods, ... }``.
    """

    reference_id: str
    widget_config: dict[str, Any]
    expires_at: Optional[int] = None


@dataclass(frozen=True)
class KYCVerificationDetails:
    """Result of a polled / webhooked verification.

    ``status`` is the provider-normalised state: ``ongoing``,
    ``success``, ``failed``, ``expired``. ``confidence`` is 0-100 when
    the provider exposes it. ``data`` is the raw provider payload kept
    for audit; nothing in our code looks at it past ``status`` and
    ``extracted_*`` fields.
    """

    reference_id: str
    status: str
    # Dojah's face-match score (0-100) between the selfie and the photo on the
    # government ID. This is the ONLY signal that ties the document to the
    # person physically present — a stolen-but-genuine ID passes every other
    # check. Thresholded in ``services/identity_match``.
    confidence: Optional[float] = None
    # Dojah's own boolean verdict on the selfie, when it sends one.
    selfie_match: Optional[bool] = None
    extracted_full_name: Optional[str] = None
    extracted_dob: Optional[str] = None
    extracted_id_number: Optional[str] = None
    extracted_id_type: Optional[str] = None
    selfie_url: Optional[str] = None
    id_image_url: Optional[str] = None
    failure_reason: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KYCWebhookEvent:
    """Normalised webhook payload after provider-specific parsing.

    ``metadata`` is the provider's echo of the ``metadata`` block we
    sent in ``initiate`` (Dojah forwards it verbatim on the webhook).
    Carries the visichek-side correlators — most importantly
    ``checkin_id`` — that let ``finalize_kyc`` link a webhook to the
    originating check-in even when the provider-issued ``reference_id``
    doesn't match the one we persisted on initiate.
    """

    provider: str
    event_id: str
    event_type: str
    reference_id: str
    received_at: int
    raw_payload: dict[str, Any]
    signature_valid: bool
    details: Optional[KYCVerificationDetails] = None
    metadata: dict[str, Any] = field(default_factory=dict)
