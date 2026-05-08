from __future__ import annotations

from typing import List, Optional

from pymongo import ReturnDocument

from core.database import db
from schemas.imports import TenantEnumKind
from schemas.tenant_enum_schema import (
    TenantEnumCreate,
    TenantEnumOut,
    TenantEnumUpdate,
)

COLLECTION = "tenant_enums"


async def create_tenant_enum(payload: TenantEnumCreate) -> TenantEnumOut:
    doc = payload.model_dump()
    doc["kind"] = payload.kind.value
    result = await db[COLLECTION].insert_one(doc)
    fetched = await db[COLLECTION].find_one({"_id": result.inserted_id})
    return TenantEnumOut(**fetched)


async def get_tenant_enum(
    tenant_id: str, kind: TenantEnumKind
) -> Optional[TenantEnumOut]:
    doc = await db[COLLECTION].find_one(
        {"tenant_id": tenant_id, "kind": kind.value}
    )
    if doc is None:
        return None
    return TenantEnumOut(**doc)


async def list_tenant_enums(tenant_id: str) -> List[TenantEnumOut]:
    cursor = db[COLLECTION].find({"tenant_id": tenant_id})
    out: list[TenantEnumOut] = []
    async for doc in cursor:
        out.append(TenantEnumOut(**doc))
    return out


async def update_tenant_enum(
    tenant_id: str, kind: TenantEnumKind, data: TenantEnumUpdate
) -> Optional[TenantEnumOut]:
    update_dict = {
        k: v for k, v in data.model_dump(exclude_none=True).items() if v is not None
    }
    if "options" in update_dict:
        update_dict["options"] = [
            opt if isinstance(opt, dict) else opt.model_dump()
            for opt in update_dict["options"]
        ]
    doc = await db[COLLECTION].find_one_and_update(
        {"tenant_id": tenant_id, "kind": kind.value},
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        return None
    return TenantEnumOut(**doc)


async def upsert_tenant_enum(payload: TenantEnumCreate) -> TenantEnumOut:
    """Create the enum row or replace it if one already exists.

    Used by the bootstrap path so that re-running the seed for a tenant
    is idempotent (no-op when the row already matches the defaults).
    """
    doc = payload.model_dump()
    doc["kind"] = payload.kind.value
    fetched = await db[COLLECTION].find_one_and_update(
        {"tenant_id": payload.tenant_id, "kind": payload.kind.value},
        {"$setOnInsert": doc},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return TenantEnumOut(**fetched)


async def delete_tenant_enum(tenant_id: str, kind: TenantEnumKind) -> int:
    result = await db[COLLECTION].delete_one(
        {"tenant_id": tenant_id, "kind": kind.value}
    )
    return result.deleted_count
