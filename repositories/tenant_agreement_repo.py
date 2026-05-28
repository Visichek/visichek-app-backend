"""Per-tenant agreement repository — pure Mongo access, no business logic.

One record per ``(tenant_id, agreement_key)`` in ``tenant_agreements``
(upsert semantics on that compound key).
"""

from __future__ import annotations

from typing import List, Optional

from pymongo import ReturnDocument

from core.database import db
from schemas.tenant_agreement_schema import (
    TenantAgreementCreate,
    TenantAgreementOut,
    TenantAgreementUpdate,
)

COLLECTION = "tenant_agreements"


async def get_for_tenant(
    tenant_id: str, agreement_key: str
) -> Optional[TenantAgreementOut]:
    found = await db[COLLECTION].find_one(
        {"tenant_id": tenant_id, "agreement_key": agreement_key}
    )
    if found is None:
        return None
    return TenantAgreementOut(**found)


async def list_for_tenant(tenant_id: str) -> List[TenantAgreementOut]:
    cursor = db[COLLECTION].find({"tenant_id": tenant_id})
    rows: List[TenantAgreementOut] = []
    async for found in cursor:
        rows.append(TenantAgreementOut(**found))
    return rows


async def upsert(data: TenantAgreementCreate) -> TenantAgreementOut:
    """Create or replace the (tenant, agreement) record."""
    doc = data.model_dump()
    result = await db[COLLECTION].find_one_and_update(
        {"tenant_id": data.tenant_id, "agreement_key": data.agreement_key},
        {"$set": doc},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return TenantAgreementOut(**result)


async def update(
    tenant_id: str, agreement_key: str, data: TenantAgreementUpdate
) -> Optional[TenantAgreementOut]:
    update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
    result = await db[COLLECTION].find_one_and_update(
        {"tenant_id": tenant_id, "agreement_key": agreement_key},
        {"$set": update_dict},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        return None
    return TenantAgreementOut(**result)


async def list_tenant_ids() -> List[str]:
    """Distinct tenant_ids that already have at least one agreement row."""
    return await db[COLLECTION].distinct("tenant_id")
