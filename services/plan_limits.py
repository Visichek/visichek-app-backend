from __future__ import annotations

"""
Plan limit enforcement helpers.

Centralized helpers that services call at entity-creation time to enforce
the ``tenant_caps`` and ``storage_limits`` fields on the tenant's resolved
plan. These complement the rate-based ``crud_limits`` enforcement that the
``PlanEnforcementMiddleware`` already handles.

Design principle: enforcement lives at the service layer (like
``branch_service._enforce_branch_cap``) so each ``add_*`` function calls
into one of the helpers before creating the entity.

If plan resolution fails (no subscription, Redis outage, etc.) the helpers
return silently instead of blocking. The middleware is the primary gate for
subscription presence/status; these helpers only enforce the numeric caps.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status

from services.plan_cache_service import resolve_tenant_plan


async def _get_plan_data(tenant_id: str) -> Optional[dict]:
    try:
        return await resolve_tenant_plan(tenant_id)
    except Exception:
        return None


async def enforce_entity_cap(
    tenant_id: str,
    cap_key: str,
    current_count: int,
    friendly_name: str,
) -> None:
    """Raise 429 if the tenant is at/above their plan's hard cap.

    ``cap_key`` is the field name on ``TenantCapLimit`` (e.g. ``max_departments``).
    ``current_count`` is the caller's pre-computed count of existing entities.
    """
    plan_data = await _get_plan_data(tenant_id)
    if not plan_data:
        return  # Middleware handles missing-subscription case
    caps = plan_data.get("tenant_caps") or {}
    limit = caps.get(cap_key)
    if limit is None:
        return  # Unlimited
    if current_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"{friendly_name} limit reached ({limit}). "
                f"Upgrade your plan for more {friendly_name.lower()}."
            ),
        )


def get_month_bounds(now: Optional[datetime] = None) -> tuple[int, int]:
    """Return (start_of_month_epoch, start_of_next_month_epoch) in UTC seconds.

    Used for monthly caps like ``max_visitors_per_month``. The window is the
    current calendar month in UTC.
    """
    now = now or datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


async def enforce_storage_limits(
    tenant_id: str,
    current_document_count: int,
    current_total_bytes: int,
    new_file_bytes: int,
) -> None:
    """Enforce ``StorageLimit`` fields (max_documents, max_storage_mb, max_file_size_mb).

    ``current_document_count`` / ``current_total_bytes`` are the caller's
    counts of already-stored documents belonging to the tenant.
    ``new_file_bytes`` is the size of the file about to be uploaded.
    """
    plan_data = await _get_plan_data(tenant_id)
    if not plan_data:
        return
    limits = plan_data.get("storage_limits") or {}

    max_file_size_mb = limits.get("max_file_size_mb")
    if max_file_size_mb is not None:
        max_bytes = int(max_file_size_mb) * 1024 * 1024
        if new_file_bytes > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"File exceeds per-file size limit "
                    f"({max_file_size_mb} MB) on your plan."
                ),
            )

    max_documents = limits.get("max_documents")
    if max_documents is not None and current_document_count >= max_documents:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Document count limit reached ({max_documents}). "
                f"Upgrade your plan for more storage."
            ),
        )

    max_storage_mb = limits.get("max_storage_mb")
    if max_storage_mb is not None:
        projected_bytes = current_total_bytes + new_file_bytes
        max_bytes_total = int(max_storage_mb) * 1024 * 1024
        if projected_bytes > max_bytes_total:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Storage limit reached ({max_storage_mb} MB). "
                    f"Upgrade your plan for more storage."
                ),
            )
