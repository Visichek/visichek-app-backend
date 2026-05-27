"""Per-tenant DPA repository — pure Mongo access, no business logic.

One record per tenant in ``tenant_dpa_agreements`` (upsert semantics on
``tenant_id``).
"""

from __future__ import annotations

from typing import List, Optional

from pymongo import ReturnDocument

from core.database import db
from schemas.dpa_schema import (
    DpaAgreementCreate,
    DpaAgreementOut,
    DpaAgreementUpdate,
)

COLLECTION = "tenant_dpa_agreements"


async def get_dpa_for_tenant(tenant_id: str) -> Optional[DpaAgreementOut]:
    found = await db[COLLECTION].find_one({"tenant_id": tenant_id})
    if found is None:
        return None
    return DpaAgreementOut(**found)


async def upsert_dpa(data: DpaAgreementCreate) -> DpaAgreementOut:
    """Create or replace the tenant's DPA record (keyed by tenant_id)."""
    doc = data.model_dump()
    result = await db[COLLECTION].find_one_and_update(
        {"tenant_id": data.tenant_id},
        {"$set": doc},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return DpaAgreementOut(**result)


async def update_dpa(
    tenant_id: str, data: DpaAgreementUpdate
) -> Optional[DpaAgreementOut]:
    update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
    result = await db[COLLECTION].find_one_and_update(
        {"tenant_id": tenant_id},
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return DpaAgreementOut(**result)


async def count_dpa_agreements(filter_dict: Optional[dict] = None) -> int:
    return await db[COLLECTION].count_documents(filter_dict or {})


async def list_tenant_ids_with_dpa() -> List[str]:
    """Distinct tenant_ids that already have a DPA record (for backfill skip)."""
    return await db[COLLECTION].distinct("tenant_id")
