"""Storage quota resolution + enforcement (plan + active addons).

The legacy ``services.plan_limits.enforce_storage_limits`` only knows
about the plan's static ``StorageLimit``. This service layers
purchased ``storage_extension`` addons on top so a tenant can buy
extra GBs without changing plan tier.

Total budget = ``plan.storage_limits.max_storage_mb`` +
              Σ active storage_extension addon
                  ``quantity * benefit_snapshot["storage_mb"]``

When the plan grants unlimited storage (``max_storage_mb=None``) the
total stays unlimited regardless of addons.

Document count + per-file size are NOT extended by addons today — the
``max_documents`` and ``max_file_size_mb`` caps remain plan-only.
Add a new addon ``kind`` later if those need extending too.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, status

from repositories.document_repo import (
    count_documents_for_tenant,
    sum_document_bytes_for_tenant,
)
from repositories.tenant_addon_repo import list_active_for_tenant
from schemas.addon_schema import AddonKind, StorageQuotaOut, TenantAddonStatus
from services.plan_cache_service import resolve_tenant_plan

logger = logging.getLogger(__name__)


async def _resolve_plan_storage(tenant_id: str) -> dict:
    """Pull the StorageLimit block from the tenant's resolved plan.

    Returns an empty dict on plan-resolution failure so callers can
    keep going (the per-call enforcement decides the right default).
    """
    try:
        plan = await resolve_tenant_plan(tenant_id)
    except Exception:
        return {}
    if not plan:
        return {}
    return plan.get("storage_limits") or {}


async def _sum_active_storage_addon_mb(tenant_id: str) -> tuple[int, int]:
    """Return ``(total_addon_mb, active_addon_count)`` for the tenant.

    Iterates active ``storage_extension`` addons whose
    ``expires_at`` is null or in the future. Each row contributes
    ``quantity * benefit_snapshot.storage_mb`` (default 0 if the
    snapshot is malformed — addons authored under a future SKU that
    forgot the key are silently zero rather than fatal).
    """
    rows = await list_active_for_tenant(
        tenant_id, addon_kind=AddonKind.STORAGE_EXTENSION.value
    )
    total = 0
    count = 0
    for row in rows:
        # Defensive: only count rows that are explicitly active.
        if row.status != TenantAddonStatus.ACTIVE:
            continue
        benefit_mb = int((row.benefit_snapshot or {}).get("storage_mb") or 0)
        if benefit_mb <= 0:
            continue
        total += benefit_mb * row.quantity
        count += 1
    return total, count


async def get_storage_quota(tenant_id: str) -> StorageQuotaOut:
    """Compute the storage budget surfaced via GET /v1/storage/quota.

    Used both by the API endpoint and internally by
    ``enforce_storage_quota`` so a single computation path exists.
    """
    plan_storage = await _resolve_plan_storage(tenant_id)
    plan_storage_mb: Optional[int] = plan_storage.get("max_storage_mb")
    max_documents: Optional[int] = plan_storage.get("max_documents")
    max_file_size_mb: int = int(plan_storage.get("max_file_size_mb", 10) or 10)

    addon_mb, addon_count = await _sum_active_storage_addon_mb(tenant_id)

    if plan_storage_mb is None:
        total_mb: Optional[int] = None  # unlimited stays unlimited
    else:
        total_mb = int(plan_storage_mb) + addon_mb

    used_bytes = await sum_document_bytes_for_tenant(tenant_id)
    document_count = await count_documents_for_tenant(tenant_id)
    used_mb = round(used_bytes / (1024 * 1024), 4)
    remaining_mb: Optional[float]
    if total_mb is None:
        remaining_mb = None
    else:
        remaining_mb = max(round(total_mb - used_mb, 4), 0.0)

    return StorageQuotaOut(
        tenant_id=tenant_id,
        plan_storage_mb=plan_storage_mb,
        addon_storage_mb=addon_mb,
        total_storage_mb=total_mb,
        used_bytes=used_bytes,
        used_mb=used_mb,
        remaining_mb=remaining_mb,
        document_count=document_count,
        max_documents=max_documents,
        max_file_size_mb=max_file_size_mb,
        active_addons=addon_count,
    )


async def enforce_storage_quota(
    tenant_id: Optional[str], new_file_bytes: int
) -> None:
    """Reject a pending upload if it would breach plan + addon budget.

    Mirrors the legacy ``plan_limits.enforce_storage_limits`` shape
    so callers can swap in without behavior surprises, but layers
    addon storage on top. ``tenant_id=None`` (app admins / users
    without a tenant) is a no-op — those uploads bypass tenant
    storage caps entirely.
    """
    if not tenant_id:
        return

    quota = await get_storage_quota(tenant_id)

    # Per-file cap (plan-only — addons don't currently extend it).
    max_bytes = quota.max_file_size_mb * 1024 * 1024
    if new_file_bytes > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File exceeds per-file size limit ({quota.max_file_size_mb} MB) "
                "on your plan."
            ),
        )

    # Document count cap (plan-only).
    if quota.max_documents is not None and quota.document_count >= quota.max_documents:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Document count limit reached ({quota.max_documents}). "
                "Upgrade your plan or purchase a storage addon for more files."
            ),
        )

    # Total bytes — plan + active storage addons.
    if quota.total_storage_mb is not None:
        projected_bytes = quota.used_bytes + new_file_bytes
        max_total_bytes = quota.total_storage_mb * 1024 * 1024
        if projected_bytes > max_total_bytes:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Storage limit reached ({quota.total_storage_mb} MB total — "
                    f"plan {quota.plan_storage_mb or 0} MB + addons "
                    f"{quota.addon_storage_mb} MB). Purchase a storage addon "
                    "(POST /v1/addons/purchase) or upgrade your plan."
                ),
            )
