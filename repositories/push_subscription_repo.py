from __future__ import annotations

from typing import List, Optional

from core.database import db
from schemas.imports import UserType
from schemas.push_subscription_schema import PushSubscriptionOut

COLLECTION = "push_subscriptions"


async def upsert_push_subscription(
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_id: str,
    user_type: UserType,
    tenant_id: Optional[str],
    expiration_time: Optional[int],
    user_agent: Optional[str],
    now: int,
) -> PushSubscriptionOut:
    """Create or refresh a subscription, keyed by its (globally unique) endpoint.

    The endpoint is the push service URL the browser minted; re-subscribing
    on the same browser yields the same endpoint, so an upsert keeps exactly
    one row per device and re-points it at the current user (e.g. after a
    shared-kiosk login switch). ``date_created`` is preserved across
    re-registration via ``$setOnInsert``.
    """
    await db[COLLECTION].update_one(
        {"endpoint": endpoint},
        {
            "$set": {
                "p256dh": p256dh,
                "auth": auth,
                "user_id": user_id,
                "user_type": user_type.value,
                "tenant_id": tenant_id,
                "expiration_time": expiration_time,
                "user_agent": user_agent,
                "last_seen_at": now,
            },
            "$setOnInsert": {"endpoint": endpoint, "date_created": now},
        },
        upsert=True,
    )
    saved = await db[COLLECTION].find_one({"endpoint": endpoint})
    return PushSubscriptionOut(**saved)  # type: ignore[arg-type]


async def get_push_subscriptions_for_user(
    user_id: str, user_type: UserType
) -> List[PushSubscriptionOut]:
    cursor = db[COLLECTION].find(
        {"user_id": user_id, "user_type": user_type.value}
    )
    items: List[PushSubscriptionOut] = []
    async for doc in cursor:
        items.append(PushSubscriptionOut(**doc))
    return items


async def delete_push_subscription_for_user(
    *, endpoint: str, user_id: str, user_type: UserType
) -> int:
    """Remove a subscription the owning user explicitly unsubscribed.

    Scoped to the owner so one user can't delete another's row.
    """
    result = await db[COLLECTION].delete_one(
        {"endpoint": endpoint, "user_id": user_id, "user_type": user_type.value}
    )
    return result.deleted_count


async def delete_push_subscription_by_endpoint(endpoint: str) -> int:
    """Prune a dead subscription (push service returned 404/410 Gone).

    Endpoint-only, no owner scope — used by the ``push.send`` task when the
    push service reports the subscription no longer exists.
    """
    result = await db[COLLECTION].delete_one({"endpoint": endpoint})
    return result.deleted_count
