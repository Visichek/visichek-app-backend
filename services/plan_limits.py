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

import fnmatch
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status

from services.plan_cache_service import resolve_tenant_plan

# Shared user-facing message for a 429 raised by enforce_branch_visitor_cap
# on kiosk / public self-registration flows. Hoisted here so
# checkin_service.py and public_registration_service.py don't drift.
KIOSK_CAP_MESSAGE = (
    "This location can't accept new visitor registrations right now — "
    "please see the front desk."
)


async def get_plan_data_safe(tenant_id: str) -> Optional[dict]:
    """Resolve the tenant's plan, swallowing any resolution failure to None.

    Public name for what was previously the module-private ``_get_plan_data``.
    Callers across services import this directly, so it's promoted to a
    proper public helper; ``_get_plan_data`` is kept as an alias for any
    caller still importing the old name.
    """
    try:
        return await resolve_tenant_plan(tenant_id)
    except Exception:
        return None


# Backwards-compatible alias — prefer ``get_plan_data_safe`` in new code.
_get_plan_data = get_plan_data_safe


async def is_feature_enabled(
    tenant_id: str,
    endpoint_pattern: str,
    method: str = "POST",
) -> bool:
    """Return True if a feature is enabled on the tenant's resolved plan.

    Mirrors the fnmatch-based gate inside ``PlanEnforcementMiddleware``
    but lives in the service layer so internal callers (workers, queued
    write handlers, cross-service flows) can short-circuit paid-only
    side effects without bouncing the request back through HTTP.

    Fails OPEN when no plan can be resolved — the middleware is the
    primary gate at the front door, and a Redis hiccup here should not
    silently block paying customers from issuing badges.

    Lookup rules: walks the plan's ``feature_rules`` in order, returning
    the first rule whose ``endpoint_pattern`` matches and whose method
    list contains ``method``. If no rule matches, the feature is allowed
    by default (the middleware applies the same convention).
    """
    plan_data = await _get_plan_data(tenant_id)
    if not plan_data:
        return True
    method_upper = method.upper()
    for rule in plan_data.get("feature_rules", []):
        pattern = rule.get("endpoint_pattern", "")
        methods = rule.get("methods") or [
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ]
        if fnmatch.fnmatch(endpoint_pattern, pattern) and method_upper in methods:
            return bool(rule.get("enabled", True))
    return True


async def enforce_feature_enabled(
    tenant_id: str,
    endpoint_pattern: str,
    method: str = "POST",
    *,
    friendly_name: str = "this feature",
) -> None:
    """Raise HTTP 403 if the feature is denied on the tenant's plan.

    Use at the top of a service function whenever the call has a
    paid-only side effect (issuing a badge, creating an appointment,
    enabling KYC, etc.). The route-layer middleware already gates the
    public URL surface — this is defense-in-depth for code paths that
    reach the side effect via a different URL (e.g. the implicit badge
    issuance inside ``confirm_check_in``).
    """
    if not await is_feature_enabled(tenant_id, endpoint_pattern, method):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your plan does not include {friendly_name}.",
        )


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


async def enforce_branch_visitor_cap(
    tenant_id: str,
    branch_id: Optional[str],
    resolved_plan: Optional[dict],
    *,
    is_new_visitor: bool,
    friendly_message: Optional[str] = None,
) -> None:
    """Raise 429 if a NEW visitor would push this branch (or the tenant,
    when no per-branch cap is configured) over its monthly new-visitor cap.

    ``resolved_plan`` is the caller's already-resolved plan snapshot (see
    ``resolve_tenant_plan`` / ``_get_plan_data``) — passed in rather than
    re-resolved here so call sites that already need the snapshot for
    other checks don't pay for a second lookup.

    Returning visitors (``is_new_visitor=False``) never block — repeat
    check-ins never count against a cap. Fails OPEN when the plan can't be
    resolved (mirrors ``enforce_entity_cap``).
    """
    if not is_new_visitor:
        return
    if not resolved_plan:
        return  # Fail open — middleware handles missing-subscription case

    from repositories.visitor_branch_first_repo import count_new_for_month

    caps = resolved_plan.get("tenant_caps") or {}
    month_start, month_end = get_month_bounds()

    # NOTE: when a per-branch cap is configured AND the branch resolves,
    # this clause returns unconditionally (see the ``return`` a few lines
    # below) — the tenant-wide ``max_visitors_per_month`` check further
    # down never runs for that request. So on a plan like Premium that
    # sets both a per-branch cap and a tenant-wide cap, the tenant-wide
    # cap only actually applies when the branch can't be resolved (e.g.
    # legacy check-ins with no branch_id).
    per_branch_limit = caps.get("visitors_per_branch_per_month")
    if per_branch_limit is not None and branch_id:
        count = await count_new_for_month(
            tenant_id, month_start, month_end, branch_id=branch_id
        )
        if count >= per_branch_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=friendly_message
                or (
                    f"This location's monthly new-visitor limit has been reached "
                    f"({per_branch_limit}). Upgrade your plan for more capacity."
                ),
            )
        return

    # No per-branch cap configured — fall back to the tenant-wide monthly
    # cap (plus any addon top-up folded into the snapshot by Task 1).
    max_visitors = caps.get("max_visitors_per_month")
    if max_visitors is None:
        return  # Unlimited
    extra_visitors = resolved_plan.get("extra_visitors_per_month") or 0
    effective_limit = max_visitors + extra_visitors
    count = await count_new_for_month(tenant_id, month_start, month_end)
    if count >= effective_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=friendly_message
            or (
                f"Monthly visitor limit reached ({effective_limit}). "
                f"Upgrade your plan for more visitors."
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
