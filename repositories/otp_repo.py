from __future__ import annotations

import time

from bson import ObjectId

from core.database import db

COLLECTION = "pending_otp"


async def create_otp_challenge(data: dict) -> dict:
    result = await db[COLLECTION].insert_one(data)
    data["_id"] = result.inserted_id
    return data


async def get_otp_challenge(challenge_id: str) -> dict | None:
    if not ObjectId.is_valid(challenge_id):
        return None
    return await db[COLLECTION].find_one({"_id": ObjectId(challenge_id)})


async def increment_otp_attempts(challenge_id: str) -> None:
    await db[COLLECTION].update_one(
        {"_id": ObjectId(challenge_id)},
        {"$inc": {"attempts": 1}},
    )


async def mark_otp_used(challenge_id: str) -> None:
    await db[COLLECTION].update_one(
        {"_id": ObjectId(challenge_id)},
        {"$set": {"used": True}},
    )


async def delete_expired_challenges() -> int:
    result = await db[COLLECTION].delete_many({"expires_at": {"$lt": int(time.time())}})
    return result.deleted_count
