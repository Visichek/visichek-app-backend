from __future__ import annotations

import logging
from typing import Optional

from core.queue.precompute import delete_precompute, mark_scope_dirty

logger = logging.getLogger(__name__)

_TENANT_DASHBOARD_RESOURCES = (
    "dashboard.stats",
    "dashboard.visitors_active",
    "dashboard.visitors_page1",
)


def invalidate_tenant_dashboard_cache(tenant_id: Optional[str]) -> None:
    """Drop tenant dashboard read models after direct MongoDB mutations.

    Several check-in and visit-session flows still write synchronously rather
    than through the queue write pipeline, so they do not get the automatic
    dashboard invalidation cascade. This helper gives those paths the same
    freshness behavior.
    """
    if not tenant_id:
        return

    for resource in _TENANT_DASHBOARD_RESOURCES:
        try:
            delete_precompute(resource, tenant_id=tenant_id)
        except Exception:
            logger.debug(
                "dashboard cache delete failed resource=%s tenant=%s",
                resource,
                tenant_id,
                exc_info=True,
            )

    try:
        mark_scope_dirty(f"t:{tenant_id}")
    except Exception:
        logger.debug(
            "dashboard cache dirty marker failed tenant=%s",
            tenant_id,
            exc_info=True,
        )
