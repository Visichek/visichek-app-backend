from __future__ import annotations

import hashlib
import json
import time
from typing import Optional

from bson import ObjectId
from pydantic import BaseModel, Field, model_validator
from pymongo import ReturnDocument

from core.database import db
from schemas.imports import *

COLLECTION = "webhook_events"


class WebhookEventCreate(BaseModel):
    """Schema for creating a webhook event record."""

    provider: str  # "stripe", "flutterwave", etc.
    event_id: str  # Provider-specific event ID
    event_type: str  # "payment.success", "payment.failed", etc.
    raw_payload: dict  # Raw webhook payload
    payload_hash: str  # SHA256 hash of payload for idempotency
    processing_status: str = "pending"  # "processed", "failed", "skipped"
    error_message: Optional[str] = None  # Error details if failed
    processing_duration_ms: Optional[int] = None  # How long processing took
    created_at: int = Field(default_factory=lambda: int(time.time()))

    @model_validator(mode="after")
    def validate_on_create(self):
        if not self.provider:
            raise ValueError("provider is required")
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.event_type:
            raise ValueError("event_type is required")
        if not self.payload_hash:
            raise ValueError("payload_hash is required")
        return self


class WebhookEventOut(WebhookEventCreate):
    """Schema for webhook event response."""

    id: Optional[str] = Field(default=None, alias="_id")

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


async def create_webhook_event(event_data: WebhookEventCreate) -> WebhookEventOut:
    """
    Insert a new webhook event record.

    Args:
        event_data: WebhookEventCreate schema

    Returns:
        WebhookEventOut with generated ID
    """
    event_dict = event_data.model_dump(mode="json")
    result = await db[COLLECTION].insert_one(event_dict)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return WebhookEventOut(**doc)


async def get_webhook_event(filter_dict: dict) -> Optional[WebhookEventOut]:
    """
    Find a single webhook event by filter.

    Args:
        filter_dict: MongoDB filter criteria

    Returns:
        WebhookEventOut or None
    """
    try:
        doc = await db[COLLECTION].find_one(filter_dict)
        if doc is None:
            return None
        return WebhookEventOut(**doc)
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"Error fetching webhook event: {str(e)}")
        raise


async def get_webhook_events(
    filter_dict: dict = {},
    skip: int = 0,
    limit: int = 100,
) -> list[WebhookEventOut]:
    """
    Find multiple webhook events sorted by creation date (newest first).

    Args:
        filter_dict: MongoDB filter criteria
        skip: Number of records to skip
        limit: Maximum number of records to return

    Returns:
        List of WebhookEventOut
    """
    try:
        cursor = (
            db[COLLECTION]
            .find(filter_dict)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        events = []
        async for doc in cursor:
            events.append(WebhookEventOut(**doc))
        return events
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"Error fetching webhook events: {str(e)}")
        raise


async def count_webhook_events(filter_dict: dict = {}) -> int:
    """
    Count webhook events matching filter.

    Args:
        filter_dict: MongoDB filter criteria

    Returns:
        Number of matching documents
    """
    try:
        count = await db[COLLECTION].count_documents(filter_dict)
        return count
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.error(f"Error counting webhook events: {str(e)}")
        raise


async def mark_webhook_processed(
    event_id: str,
    provider: str,
    status: str = "processed",
    error_message: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> Optional[WebhookEventOut]:
    """
    Mark a webhook as processed with final status.

    Args:
        event_id: Provider event ID
        provider: Payment provider name
        status: "processed", "failed", or "skipped"
        error_message: Error details if failed
        duration_ms: Processing duration

    Returns:
        Updated WebhookEventOut
    """
    update_dict = {
        "processing_status": status,
        "processing_duration_ms": duration_ms,
        "updated_at": int(time.time()),
    }

    if error_message:
        update_dict["error_message"] = error_message

    result = await db[COLLECTION].find_one_and_update(
        {"event_id": event_id, "provider": provider},
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )

    if result is None:
        return None

    return WebhookEventOut(**result)


async def is_webhook_processed(event_id: str, provider: str) -> bool:
    """
    Check if a webhook event has already been processed.

    Args:
        event_id: Provider event ID
        provider: Payment provider name

    Returns:
        True if already processed, False otherwise
    """
    doc = await db[COLLECTION].find_one(
        {
            "event_id": event_id,
            "provider": provider,
            "processing_status": {"$in": ["processed", "skipped"]},
        }
    )
    return doc is not None


def compute_payload_hash(payload: dict | bytes) -> str:
    """
    Compute SHA256 hash of payload for idempotency checks.

    Args:
        payload: Raw webhook payload (dict or bytes)

    Returns:
        Hex digest of SHA256 hash
    """
    if isinstance(payload, dict):
        payload_bytes = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    else:
        payload_bytes = payload

    return hashlib.sha256(payload_bytes).hexdigest()
