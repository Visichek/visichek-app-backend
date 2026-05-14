"""Database access for tenant forms.

The tenant_forms collection is multi-row per ``form_id``. At most one
row per (tenant_id, target_type) has ``status in {active, draft}``;
older published versions linger as ``status=superseded`` so historical
submissions remain interpretable. Archived forms keep their last
published shape so existing submissions still resolve against them.
"""

from __future__ import annotations

from typing import List, Optional

from bson import ObjectId

from core.database import db
from schemas.tenant_form_schema import (
    TenantFormCreate,
    TenantFormOut,
    TenantFormUpdate,
)

COLLECTION = "tenant_forms"

# Statuses that count as "the head row" for a form_id — i.e. the row the
# super_admin is currently editing or has live. Used by lookups that
# need the draft / active state, never history.
HEAD_STATUSES = ("active", "draft")


async def create_tenant_form(
    form_data: TenantFormCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> TenantFormOut:
    doc = form_data.model_dump()
    if preassigned_id:
        doc["_id"] = ObjectId(preassigned_id)
    result = await db[COLLECTION].insert_one(doc)
    new_doc = await db[COLLECTION].find_one({"_id": result.inserted_id})
    assert new_doc is not None
    return TenantFormOut(**new_doc)


async def get_tenant_form(filter_dict: dict) -> Optional[TenantFormOut]:
    doc = await db[COLLECTION].find_one(filter_dict)
    if doc:
        return TenantFormOut(**doc)
    return None


async def get_tenant_form_by_row_id(row_id: str) -> Optional[TenantFormOut]:
    if not ObjectId.is_valid(row_id):
        return None
    return await get_tenant_form({"_id": ObjectId(row_id)})


async def get_head_for_form_id(
    tenant_id: str, form_id: str
) -> Optional[TenantFormOut]:
    """Return the draft/active row for a form_id, or None."""
    return await get_tenant_form(
        {
            "tenant_id": tenant_id,
            "form_id": form_id,
            "status": {"$in": list(HEAD_STATUSES)},
        }
    )


async def get_head_by_target(
    tenant_id: str, target_type: str
) -> Optional[TenantFormOut]:
    """Return the draft/active row for a (tenant, target_type), or None."""
    return await get_tenant_form(
        {
            "tenant_id": tenant_id,
            "target_type": target_type,
            "status": {"$in": list(HEAD_STATUSES)},
        }
    )


async def get_active_by_target(
    tenant_id: str, target_type: str
) -> Optional[TenantFormOut]:
    """Return the published (active) row for a (tenant, target_type)."""
    return await get_tenant_form(
        {
            "tenant_id": tenant_id,
            "target_type": target_type,
            "status": "active",
        }
    )


async def list_tenant_forms(
    filter_dict: dict,
    start: int = 0,
    stop: int = 100,
) -> List[TenantFormOut]:
    cursor = db[COLLECTION].find(filter_dict).skip(start).limit(stop)
    return [TenantFormOut(**doc) async for doc in cursor]


async def update_tenant_form(
    filter_dict: dict,
    update_data: TenantFormUpdate,
) -> Optional[TenantFormOut]:
    """Partial update. Returns the post-update document or None on miss."""
    update_fields = update_data.model_dump(exclude_unset=True)
    if not update_fields:
        return await get_tenant_form(filter_dict)
    result = await db[COLLECTION].update_one(filter_dict, {"$set": update_fields})
    if result.matched_count == 0:
        return None
    return await get_tenant_form(filter_dict)


async def unset_draft(filter_dict: dict) -> Optional[TenantFormOut]:
    """Clear all draft_* columns on a row (used on publish / discard)."""
    await db[COLLECTION].update_one(
        filter_dict,
        {
            "$unset": {
                "draft_name": "",
                "draft_description": "",
                "draft_fields": "",
                "draft_updated_at": "",
                "draft_updated_by": "",
            }
        },
    )
    return await get_tenant_form(filter_dict)


async def supersede_existing_active(
    tenant_id: str, target_type: str, form_id: str
) -> int:
    """Flip every ACTIVE row for (tenant, target_type) that does not
    match ``form_id`` to ``superseded``.

    Returns the count of rows updated. Called during publish so the
    newly-promoted row becomes the single active version. The matching
    ``form_id`` row is left alone — the caller is responsible for
    upgrading it from ``draft`` to ``active``.
    """
    result = await db[COLLECTION].update_many(
        {
            "tenant_id": tenant_id,
            "target_type": target_type,
            "status": "active",
            "form_id": {"$ne": form_id},
        },
        {"$set": {"status": "superseded"}},
    )
    return getattr(result, "modified_count", 0) or 0


async def count_tenant_forms(filter_dict: dict) -> int:
    return await db[COLLECTION].count_documents(filter_dict)


async def delete_tenant_form(filter_dict: dict) -> int:
    result = await db[COLLECTION].delete_one(filter_dict)
    return getattr(result, "deleted_count", 0) or 0
