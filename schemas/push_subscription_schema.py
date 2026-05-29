from __future__ import annotations

from schemas.imports import *


# --- Web Push subscription (browser PushSubscription object) ---


class PushSubscriptionKeys(BaseModel):
    """The crypto keys the browser hands back from ``pushManager.subscribe``.

    ``p256dh`` is the client's public key (ECDH P-256); ``auth`` is the
    auth secret. Both are required to encrypt a Web Push payload.
    """

    p256dh: str
    auth: str


class PushSubscriptionCreate(BaseModel):
    """Public registration payload — the raw browser ``PushSubscription``.

    The frontend serialises ``subscription.toJSON()`` and POSTs it. The
    ``user_agent`` is filled server-side from the request header (never
    trusted from the body), so it is not part of this schema.
    """

    endpoint: str
    keys: PushSubscriptionKeys
    expiration_time: Optional[int] = None


class PushUnsubscribeRequest(BaseModel):
    """Body for ``DELETE /v1/push/subscriptions`` — identify by endpoint."""

    endpoint: str


class PushSubscriptionOut(BaseModel):
    """Internal representation of a stored push subscription.

    Carries the crypto keys because the ``push.send`` task needs them to
    encrypt the payload. The route layer NEVER returns this object verbatim
    — it returns a trimmed ``{id, endpoint}`` so the keys stay server-side.
    """

    id: Optional[str] = Field(default=None, alias="_id")
    endpoint: str
    p256dh: Optional[str] = None
    auth: Optional[str] = None
    user_id: Optional[str] = None
    user_type: Optional[UserType] = None
    tenant_id: Optional[str] = None
    expiration_time: Optional[int] = None
    user_agent: Optional[str] = None
    date_created: Optional[int] = None
    last_seen_at: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
