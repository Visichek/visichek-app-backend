"""Startup bootstrap for the per-tenant Data Processing Agreement.

Wired into the FastAPI lifespan (main.py). On boot it:

1. Loads the DPA template from the ``legal_documents`` collection into the
   in-process cache (the canonical source; the committed asset file is only a
   fallback). On environments without the DPA legal document — e.g. local dev —
   this is a no-op and the feature stays dormant.
2. If a template is available, runs the per-tenant backfill so every existing
   tenant has its DPA copy. Idempotent and skip-fast on subsequent boots.

Best-effort: any failure is logged and swallowed so it never blocks startup.
This mirrors ``services.plan_bootstrap.run_full_bootstrap``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def run_dpa_bootstrap() -> Dict[str, Any]:
    """Load the DPA template from the DB and backfill per-tenant copies."""
    summary: Dict[str, Any] = {"template_loaded": False, "backfill": None}

    try:
        from services.dpa_service import ensure_dpa_template_loaded

        summary["template_loaded"] = await ensure_dpa_template_loaded()
    except Exception:
        logger.warning("DPA template load failed at startup", exc_info=True)

    if not summary["template_loaded"]:
        # No DPA document in this environment yet — nothing to backfill.
        return summary

    try:
        from services.dpa_backfill import backfill_tenant_dpa

        summary["backfill"] = await backfill_tenant_dpa()
    except Exception:
        logger.warning("DPA backfill failed at startup", exc_info=True)

    return summary
