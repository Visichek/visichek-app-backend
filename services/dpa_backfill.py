"""One-off backfill: create a per-tenant Data Processing Agreement copy for
every existing tenant.

Idempotent and safe to re-run:

* tenant already has a DPA record       -> skipped
* tenant.dpa_accepted is True            -> build the copy and freeze it as
                                            accepted, preserving the tenant's
                                            existing accepted_at / accepted_by /
                                            dpa_version
* otherwise                              -> build an unaccepted copy

Requires the DPA template asset (services/dpa_template_blocks.json) to be
present — run scripts/dump_dpa_template.py against production first. If the
asset is missing the backfill aborts immediately with a clear message rather
than writing empty records.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict

from repositories.dpa_repo import get_dpa_for_tenant, update_dpa
from repositories.tenant_repo import get_tenants
from schemas.dpa_schema import DpaAgreementUpdate
from services.dpa_defaults import template_is_available
from services.dpa_service import retrieve_or_build_tenant_dpa

logger = logging.getLogger(__name__)

_PAGE = 200


async def _iter_all_tenants() -> AsyncIterator[Any]:
    start = 0
    while True:
        batch = await get_tenants(filter_dict={}, start=start, stop=start + _PAGE)
        if not batch:
            return
        for tenant in batch:
            yield tenant
        if len(batch) < _PAGE:
            return
        start += _PAGE


async def backfill_tenant_dpa(*, dry_run: bool = False) -> Dict[str, Any]:
    """Create a DPA copy for every tenant that lacks one. Returns a summary."""
    summary: Dict[str, Any] = {
        "tenants_scanned": 0,
        "created": 0,
        "created_accepted": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    if not template_is_available():
        summary["errors"].append(
            {
                "tenant_id": None,
                "error": (
                    "DPA template asset not found (services/dpa_template_blocks.json). "
                    "Run scripts/dump_dpa_template.py against production first."
                ),
            }
        )
        return summary

    async for tenant in _iter_all_tenants():
        summary["tenants_scanned"] += 1
        tenant_id = getattr(tenant, "id", None) or ""
        if not tenant_id:
            summary["skipped"] += 1
            continue
        try:
            existing = await get_dpa_for_tenant(tenant_id)
            if existing is not None:
                summary["skipped"] += 1
                continue

            tenant_accepted = bool(getattr(tenant, "dpa_accepted", False))

            if dry_run:
                summary["created_accepted" if tenant_accepted else "created"] += 1
                continue

            built = await retrieve_or_build_tenant_dpa(tenant_id)
            if built is None:
                summary["failed"] += 1
                summary["errors"].append(
                    {"tenant_id": tenant_id, "error": "build returned None"}
                )
                continue

            if tenant_accepted:
                # Freeze as accepted, carrying over the tenant's recorded
                # acceptance metadata so we don't lose the original timestamp.
                await update_dpa(
                    tenant_id,
                    DpaAgreementUpdate(
                        accepted=True,
                        accepted_at=getattr(tenant, "dpa_accepted_at", None),
                        accepted_by=getattr(tenant, "dpa_accepted_by", None),
                        version=getattr(tenant, "dpa_version", None) or built.version,
                    ),
                )
                summary["created_accepted"] += 1
            else:
                summary["created"] += 1
        except Exception as exc:  # noqa: BLE001 - best-effort per tenant
            summary["failed"] += 1
            summary["errors"].append({"tenant_id": tenant_id, "error": str(exc)})
            logger.warning(
                "DPA backfill failed for tenant=%s", tenant_id, exc_info=True
            )

    return summary
