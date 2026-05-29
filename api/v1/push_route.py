from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status

from core.response_envelope import document_response
from schemas.imports import UserType
from schemas.push_subscription_schema import (
    PushSubscriptionCreate,
    PushUnsubscribeRequest,
)
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.push_service import (
    get_push_client_config,
    register_push_subscription,
    unregister_push_subscription,
)

router = APIRouter(prefix="/push", tags=["Push Notifications"])


def _user_type(principal: AuthPrincipal) -> UserType:
    if principal.role == "admin":
        return UserType.ADMIN
    if principal.role == "user":
        return UserType.USER
    return UserType.SYSTEM_USER


# ─── Push config (runtime, provider-agnostic) ──────────────────────


@router.get("/config")
@document_response(
    message="Push config fetched successfully",
    success_example={
        "provider": "webpush",
        "publicKey": "BBM8Qbdy-SqWp_EAjXUWPtEVZJtHKJJ4Qds3wYyORAnrpnRXmCXv6cbTP_0WwA3pm9H8-TdfZhrWtrV3zK7tVcY",
    },
    description=(
        "Returns everything the frontend needs to register a device, "
        "provider-agnostic. Branch on `provider`:\n\n"
        "- `webpush` — use `publicKey` as the base64url "
        "`applicationServerKey` in "
        "`registration.pushManager.subscribe({ userVisibleOnly: true, "
        "applicationServerKey })`.\n"
        "- a future provider (e.g. `fcm`) returns its own init data under "
        "`params` instead.\n\n"
        "Fetched once before subscribing so no key is hardcoded in the "
        "client bundle and the provider can be swapped server-side via the "
        "`PUSH_PROVIDER` env var."
    ),
    summary="Get push registration config",
    response_codes={401: "Unauthorized - invalid or missing token"},
)
async def get_push_config_endpoint(
    principal: AuthPrincipal = Depends(verify_any_token),
) -> Any:
    return get_push_client_config()


# ─── Register a browser push subscription ──────────────────────────


@router.post("/subscriptions")
@document_response(
    message="Push subscription registered successfully",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Persists the browser `PushSubscription` (the JSON from "
        "`subscription.toJSON()`) for the authenticated user. Idempotent: "
        "re-posting the same endpoint refreshes the row instead of creating "
        "a duplicate, so it is safe to call on every app load. The crypto "
        "keys are stored server-side and never returned."
    ),
    summary="Register push subscription",
    response_codes={401: "Unauthorized", 422: "Validation error"},
)
async def register_subscription(
    payload: PushSubscriptionCreate,
    request: Request,
    principal: AuthPrincipal = Depends(verify_any_token),
) -> Any:
    saved = await register_push_subscription(
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        user_id=principal.user_id,
        user_type=_user_type(principal),
        tenant_id=principal.tenant_id,
        expiration_time=payload.expiration_time,
        user_agent=request.headers.get("user-agent"),
    )
    return {"id": saved.id, "endpoint": saved.endpoint}


# ─── Unregister a push subscription ────────────────────────────────


@router.delete("/subscriptions")
@document_response(
    message="Push subscription removed successfully",
    description=(
        "Removes the given endpoint for the authenticated user. Call this "
        "after `subscription.unsubscribe()` on the client, or when the user "
        "turns push off. Scoped to the caller — one user cannot delete "
        "another user's subscription."
    ),
    summary="Unregister push subscription",
    response_codes={401: "Unauthorized", 422: "Validation error"},
)
async def unregister_subscription(
    payload: PushUnsubscribeRequest,
    principal: AuthPrincipal = Depends(verify_any_token),
) -> Any:
    removed = await unregister_push_subscription(
        endpoint=payload.endpoint,
        user_id=principal.user_id,
        user_type=_user_type(principal),
    )
    return {"removed": removed}
