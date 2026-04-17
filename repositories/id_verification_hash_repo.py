from __future__ import annotations

from typing import Optional

from core.database import db
from schemas.id_verification_hash_schema import (
    IDVerificationHashCreate,
    IDVerificationHashOut,
)

COLLECTION = "id_verification_hashes"


async def find_by_hash(
    tenant_id: str, sha256: str
) -> Optional[IDVerificationHashOut]:
    doc = await db[COLLECTION].find_one(
        {"tenant_id": tenant_id, "sha256": sha256}
    )
    if doc is None:
        return None
    return IDVerificationHashOut(**doc)


async def create_hash(
    payload: IDVerificationHashCreate,
) -> IDVerificationHashOut:
    data = payload.model_dump()
    result = await db[COLLECTION].insert_one(data)
    doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return IDVerificationHashOut(**doc)


async def ensure_indexes() -> None:
    """Compound unique index on (tenant_id, sha256) — call at app startup."""
    await db[COLLECTION].create_index(
        [("tenant_id", 1), ("sha256", 1)],
        unique=True,
        name="tenant_sha256_unique",
    )
