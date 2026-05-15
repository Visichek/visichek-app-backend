from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status

from core.response_envelope import document_response
from schemas.kyc_schema import (
    KYCInitiateRequestIn,
    KYCInitiateResponseOut,
    KYCSkipRequestIn,
    KYCStatusOut,
)
from security.auth import verify_super_admin_token
from security.principal import AuthPrincipal
from services.kyc_service import (
    get_kyc_status_for_checkin,
    initiate_kyc_for_checkin,
    process_webhook_event,
    replay_stored_kyc_webhook_for_checkin,
    skip_kyc_for_checkin,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kyc", tags=["KYC"])


@router.post("/initiate", status_code=status.HTTP_201_CREATED)
@document_response(
    message="KYC verification initiated",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Public kiosk endpoint. Given a freshly submitted ``checkin_id`` "
        "(returned by ``POST /v1/checkin-configs/{id}/submit`` or "
        "``/checkins``), prepares a KYC session and returns the widget "
        "config the frontend launches Dojah with.\n\n"
        "Idempotent: calling this for the same check-in returns the "
        "existing config rather than spawning a duplicate verification."
    ),
    summary="Initiate KYC verification",
    response_codes={
        402: "KYC not available on the tenant's plan or provider unconfigured",
        404: "Check-in not found",
        409: "Check-in is in a state that doesn't accept KYC",
    },
)
async def initiate_kyc_endpoint(
    payload: KYCInitiateRequestIn,
) -> KYCInitiateResponseOut:
    return await initiate_kyc_for_checkin(checkin_id=payload.checkin_id)


@router.post("/skip", status_code=status.HTTP_200_OK)
@document_response(
    message="KYC skipped",
    description=(
        "Visitor opted not to verify with Dojah. Allowed only when the "
        "tenant's settings have ``kyc_required = False``. Transitions "
        "the check-in from ``PENDING_VERIFICATION`` → ``PENDING_APPROVAL`` and "
        "notifies the receptionist queue."
    ),
    summary="Skip KYC verification",
    response_codes={
        403: "KYC is required for this tenant",
        404: "Check-in not found",
    },
)
async def skip_kyc_endpoint(payload: KYCSkipRequestIn) -> KYCStatusOut:
    return await skip_kyc_for_checkin(
        checkin_id=payload.checkin_id, reason=payload.reason
    )


@router.post("/replay/{checkin_id}", status_code=status.HTTP_200_OK)
@document_response(
    message="KYC webhook replayed",
    description=(
        "Super-admin recovery. Re-applies the most recent stored Dojah "
        "webhook for a check-in by running it back through "
        "``finalize_kyc`` with the stored ``raw_payload``. Bypasses live "
        "signature verification because the event has already been "
        "persisted under audit. Use when the webhook landed but was "
        "rejected at signature time (wrong secret, body mutated by "
        "middleware, etc.) or when an internal exception prevented the "
        "state machine from advancing. Confined to check-ins in the "
        "caller's tenant."
    ),
    summary="Replay latest stored KYC webhook",
    response_codes={
        404: "Check-in not found, or no stored webhook references this check-in",
        422: "Stored webhook payload lacks reference_id / details — nothing to apply",
    },
)
async def replay_kyc_webhook_endpoint(
    checkin_id: str,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> KYCStatusOut:
    return await replay_stored_kyc_webhook_for_checkin(
        checkin_id=checkin_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        tenant_id_scope=principal.tenant_id,
    )


@router.get("/status/{checkin_id}")
@document_response(
    message="KYC status retrieved",
    description=(
        "Polling fallback for the kiosk. Returns the visichek-side KYC "
        "status for a check-in, kept in sync with the provider via the "
        "webhook. Useful when the widget completes after the kiosk lost "
        "its connection (or vice-versa)."
    ),
    summary="Get KYC status",
)
async def kyc_status_endpoint(checkin_id: str) -> KYCStatusOut:
    return await get_kyc_status_for_checkin(checkin_id)


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    include_in_schema=True,
)
async def kyc_webhook_endpoint(request: Request) -> Response:
    """Dojah webhook receiver — public, signature-verified.

    URL: ``POST https://api.visichek.app/v1/kyc/webhook``

    We read the raw body so HMAC computation matches Dojah's
    signature exactly (a ``json``-deserialised reload would re-encode
    keys and break verification). Always returns 200 on a parsable
    request so Dojah doesn't escalate retries; rejected signatures
    show up in the ``kyc_webhook_events`` audit collection with
    ``processing_status='rejected_signature'``.
    """
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        result = await process_webhook_event(body=raw_body, headers=headers)
    except Exception:
        logger.exception("dojah webhook: unexpected error")
        # Still 200 — recording / replay is the audit path.
        return Response(
            content='{"accepted":false,"reason":"internal_error"}',
            media_type="application/json",
            status_code=200,
        )
    if not result.get("accepted"):
        # 200 + body so Dojah marks it delivered. We track rejected
        # ones in kyc_webhook_events for replay.
        return Response(
            content=f'{{"accepted":false,"reason":"{result.get("reason", "")}"}}',
            media_type="application/json",
            status_code=200,
        )
    return Response(
        content='{"accepted":true}',
        media_type="application/json",
        status_code=200,
    )
