from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from core.errors import auth_permission_denied
from core.response_envelope import document_response
from schemas.payment_schema import PaymentIntentIn, RefundIn
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.payment_service import (
    create_payment_intent,
    get_payment_transaction,
    process_webhook,
    refund_payment,
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
