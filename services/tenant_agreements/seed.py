"""Provision per-tenant agreement rows on tenant creation.

Called from the tenant-provisioning paths (``bootstrap_tenant`` / ``add_tenant``
in ``services/tenant_service.py``). Best-effort: a missing master template or a
transient error must never block tenant creation.
"""

from __future__ import annotations

import logging

from services.tenant_agreement_service import retrieve_or_build
from services.tenant_agreements.config import ALL_AGREEMENT_KEYS

logger = logging.getLogger(__name__)


async def seed_tenant_agreements(tenant_id: str) -> None:
    """Build an (unaccepted) copy of every registered agreement for a tenant."""
    if not tenant_id:
        return
    for key in ALL_AGREEMENT_KEYS:
        try:
            await retrieve_or_build(tenant_id, key)
        except Exception:
            logger.warning(
                "agreement seed failed tenant=%s key=%s", tenant_id, key, exc_info=True
            )
