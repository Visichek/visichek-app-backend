from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from core.errors import auth_permission_denied, resource_not_found
from core.response_envelope import document_response
from schemas.payment_schema import PaymentIntentIn, RefundIn
from security.account_status_check import check_admin_account_status_and_permissions
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.payment_service import (
    create_payment_intent,
    get_payment_transaction,
    process_webhook,
    refund_payment,
)
from repositories.webhook_event_repo import (
    get_webhook_event,
    get_webhook_events,
    count_webhook_events,
    WebhookEventOut,
)

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("/intents")
@document_response(
    message="Payment intent created",
    status_code=201,
    description="Create a payment intent to initiate a payment transaction. Returns payment details and authorization URL.",
    summary="Create payment intent",
    response_codes={
        201: "Payment intent created successfully",
        401: "Unauthorized - invalid or missing authentication token",
        409: "Conflict - duplicate reference ID",
        422: "Unprocessable entity - invalid payment parameters",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        409: {
            "success": False,
            "message": "Duplicate reference ID already exists",
            "code": "RESOURCE_CONFLICT",
        },
        422: {
            "success": False,
            "message": "Invalid payment amount or currency",
            "code": "VALIDATION_FAILED",
        },
    },
    success_example={
        "id": "66f5678901234567890abcde",
        "owner_id": "user-123",
        "provider": "stripe",
        "reference": "INV-2026-001-PAY",
        "status": "pending",
        "amount_minor": 50000,
        "currency": "USD",
        "idempotency_key": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "response_payload": {
            "authorization_url": "https://checkout.stripe.com/pay/cs_live_abc123...",
            "client_secret": "pi_1234567890_secret_1234567890"
        },
        "created_at": 1712520000,
        "updated_at": 1712520000
    },
)
async def create_intent(payload: PaymentIntentIn, principal: AuthPrincipal = Depends(verify_any_token)):
    return await create_payment_intent(owner_id=principal.user_id, payload=payload)


@router.post("/webhooks/{provider}")
@document_response(
    message="Webhook processed",
    description="Receive and process payment webhooks from external payment providers (Stripe, Flutterwave). Updates payment status based on webhook events.",
    summary="Process payment webhook",
    response_codes={
        200: "Webhook processed successfully",
        400: "Invalid webhook signature or payload",
    },
    error_examples={
        400: {
            "success": False,
            "message": "Invalid webhook signature",
            "code": "VALIDATION_FAILED",
        },
    },
    success_example={
        "received": True
    },
)
async def payment_webhook(provider: str, request: Request):
    """
    Receive payment webhooks for a specific provider.

    Accepted `provider` path values:
    - `stripe`
    - `flutterwave`
    """
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    return await process_webhook(provider_name=provider, body=body, headers=headers)


@router.get("/{payment_id}")
@document_response(
    message="Payment transaction fetched",
    description="Retrieve a payment transaction by ID. Returns transaction details including status, amount, and timestamps.",
    summary="Fetch payment transaction",
    response_codes={
        200: "Payment transaction retrieved successfully",
        401: "Unauthorized - invalid or missing authentication token",
        403: "Forbidden - user lacks permission to access this transaction",
        404: "Payment transaction not found",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Permission denied",
            "code": "AUTH_PERMISSION_DENIED",
        },
        404: {
            "success": False,
            "message": "Payment transaction not found",
            "code": "RESOURCE_NOT_FOUND",
        },
    },
    success_example={
        "id": "66f5678901234567890abcde",
        "owner_id": "user-123",
        "provider": "stripe",
        "reference": "INV-2026-001-PAY",
        "status": "completed",
        "amount_minor": 50000,
        "currency": "USD",
        "idempotency_key": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "response_payload": {
            "charge_id": "ch_1234567890",
            "status": "succeeded",
            "receipt_url": "https://receipts.stripe.com/..."
        },
        "created_at": 1712520000,
        "updated_at": 1712521000
    },
)
async def fetch_transaction(payment_id: str, principal: AuthPrincipal = Depends(verify_any_token)):
    tx = await get_payment_transaction(payment_id=payment_id)
    if tx.owner_id != principal.user_id and not principal.is_admin:
        raise auth_permission_denied("GET:/v1/payments/{payment_id}")
    return tx


@router.post("/{payment_id}/refund")
@document_response(
    message="Payment refunded",
    description="Refund a payment transaction by ID. Processes a full or partial refund with the payment provider.",
    summary="Refund payment transaction",
    response_codes={
        200: "Payment refunded successfully",
        401: "Unauthorized - invalid or missing authentication token",
        403: "Forbidden - user lacks permission to refund this transaction",
        404: "Payment transaction not found",
        409: "Conflict - payment cannot be refunded in current state",
    },
    error_examples={
        401: {
            "success": False,
            "message": "Invalid or expired token",
            "code": "AUTH_INVALID_TOKEN",
        },
        403: {
            "success": False,
            "message": "Permission denied",
            "code": "AUTH_PERMISSION_DENIED",
        },
        404: {
            "success": False,
            "message": "Payment transaction not found",
            "code": "RESOURCE_NOT_FOUND",
        },
        409: {
            "success": False,
            "message": "Cannot refund payment in current state",
            "code": "RESOURCE_CONFLICT",
        },
    },
    success_example={
        "id": "66f5678901234567890abcde",
        "owner_id": "user-123",
        "provider": "stripe",
        "reference": "INV-2026-001-PAY",
        "status": "refunded",
        "amount_minor": 50000,
        "currency": "USD",
        "idempotency_key": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "response_payload": {
            "refund_id": "re_1234567890",
            "status": "succeeded",
            "amount_refunded": 50000
        },
        "created_at": 1712520000,
        "updated_at": 1712522000
    },
)
async def refund_transaction(
    payment_id: str,
    payload: RefundIn,
    principal: AuthPrincipal = Depends(verify_any_token),
):
    tx = await get_payment_transaction(payment_id=payment_id)
    if tx.owner_id != principal.user_id and not principal.is_admin:
        raise auth_permission_denied("POST:/v1/payments/{payment_id}/refund")
    return await refund_payment(payment_id=payment_id, amount_minor=payload.amount_minor)


@router.get("/webhooks/events")
@document_response(
    message="Webhook events retrieved",
    include_meta=True,
    description="List stored webhook events with optional filtering by provider or status.",
    summary="List webhook events",
    response_codes={
        401: "Unauthorized - invalid or missing token",
        403: "Forbidden - admin only",
    },
)
async def list_webhook_events(
    provider: str | None = Query(default=None, description="Filter by provider (stripe, flutterwave)"),
    status_filter: str | None = Query(default=None, alias="processing_status"),
    start: int = Query(default=0, ge=0),
    stop: int = Query(default=50, ge=1, le=200),
    admin=Depends(check_admin_account_status_and_permissions),
):
    """List webhook events (application admin only)."""
    filter_dict: dict = {}
    if provider:
        filter_dict["provider"] = provider
    if status_filter:
        filter_dict["processing_status"] = status_filter

    events = await get_webhook_events(filter_dict=filter_dict, skip=start, limit=stop)
    total = await count_webhook_events(filter_dict=filter_dict)
    return {"items": events, "meta": {"total": total, "start": start, "stop": stop}}


@router.post("/webhooks/replay/{event_id}")
@document_response(
    message="Webhook replayed",
    description="Re-process a previously stored webhook event. Useful for recovering from processing failures.",
    summary="Replay webhook event",
    response_codes={
        200: "Webhook re-processed successfully",
        404: "Webhook event not found",
        401: "Unauthorized",
        403: "Forbidden - admin only",
    },
)
async def replay_webhook_event(
    event_id: str,
    admin=Depends(check_admin_account_status_and_permissions),
):
    """Replay a stored webhook event by its database ID (application admin only)."""
    from bson import ObjectId

    if not ObjectId.is_valid(event_id):
        raise resource_not_found(resource="WebhookEvent", resource_id=event_id)

    event = await get_webhook_event({"_id": ObjectId(event_id)})
    if not event:
        raise resource_not_found(resource="WebhookEvent", resource_id=event_id)

    # Re-process through the normal webhook handler
    import json

    body = json.dumps(event.raw_payload).encode("utf-8")
    headers = {"x-replay": "true"}

    result = await process_webhook(
        provider_name=event.provider,
        body=body,
        headers=headers,
    )
    return {"replayed_event_id": event_id, "provider": event.provider, "result": result}
