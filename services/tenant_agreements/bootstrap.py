"""Startup bootstrap for the tenant-agreements subsystem.

Wired into the FastAPI lifespan (main.py). On boot it:

1. Ensures each agreement's master legal document exists. A missing master is
   created as a DRAFT from a committed default (the existing
   ``services/dpa_template_blocks.json`` for the DPA; a built-in template for
   the Visitor Privacy Policy). Drafts are NOT enforced by the gate — the
   application admin reviews and publishes them, which is when tenants start
   being prompted.
2. Backfills a per-tenant agreement row for every existing tenant (idempotent).
   For the DPA, a tenant that previously accepted (legacy ``tenant.dpa_accepted``)
   has its row frozen as accepted, carrying over the original metadata.

Best-effort: any failure is logged and swallowed so it never blocks startup.
Replaces the former ``services/dpa_bootstrap.py`` + ``services/dpa_backfill.py``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)

_DPA_ASSET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dpa_template_blocks.json",
)
_PAGE = 200


def _load_dpa_asset_blocks() -> Optional[List[dict]]:
    """Load committed DPA blocks, tolerating bare-list or {body:[...]} shapes."""
    if not os.path.exists(_DPA_ASSET_PATH):
        return None
    try:
        with open(_DPA_ASSET_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        logger.warning("failed to read DPA asset", exc_info=True)
        return None
    if isinstance(data, dict):
        if isinstance(data.get("data"), dict):
            data = data["data"]
        body = data.get("body")
        return body if isinstance(body, list) else None
    return data if isinstance(data, list) else None


def _default_master_body(key: str) -> Optional[List[dict]]:
    """Default draft body for an agreement master, or ``None`` to skip seeding."""
    if key == "dpa":
        return _load_dpa_asset_blocks()
    if key == "visitor_privacy_policy":
        from services.tenant_agreements.visitor_privacy_policy_default import (
            default_blocks,
        )

        return default_blocks()
    return None


async def ensure_master_documents() -> Dict[str, str]:
    """Create any missing agreement master legal documents as drafts."""
    from legal.repositories.legal_document_repo import get_legal_document_by_slug
    from legal.schemas.imports import LegalDocType
    from legal.schemas.legal_document_schema import LegalDocumentCreate
    from legal.services.legal_document_service import add_legal_document
    from services.tenant_agreements.config import all_agreements

    result: Dict[str, str] = {}
    for spec in all_agreements():
        try:
            existing = await get_legal_document_by_slug(spec.slug)
            if existing is not None:
                result[spec.key] = "exists"
                continue
            body = _default_master_body(spec.key)
            if not body:
                result[spec.key] = "no_default"
                continue
            await add_legal_document(
                LegalDocumentCreate(
                    title=spec.title,
                    doc_type=LegalDocType(spec.doc_type),
                    slug=spec.slug,
                    body=body,
                )
            )
            result[spec.key] = "created_draft"
        except Exception:
            logger.warning(
                "ensure master document failed key=%s", spec.key, exc_info=True
            )
            result[spec.key] = "error"
    return result


async def _iter_all_tenants() -> AsyncIterator[Any]:
    from repositories.tenant_repo import get_tenants

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


async def backfill_tenant_agreements(*, dry_run: bool = False) -> Dict[str, Any]:
    """Ensure every tenant has a row for every agreement. Returns a summary."""
    from repositories import tenant_agreement_repo as repo
    from schemas.tenant_agreement_schema import TenantAgreementUpdate
    from services.tenant_agreement_service import retrieve_or_build
    from services.tenant_agreements.config import ALL_AGREEMENT_KEYS

    summary: Dict[str, Any] = {
        "tenants_scanned": 0,
        "rows_built": 0,
        "rows_accepted": 0,
        "skipped": 0,
        "failed": 0,
    }

    async for tenant in _iter_all_tenants():
        summary["tenants_scanned"] += 1
        tenant_id = getattr(tenant, "id", None) or ""
        if not tenant_id:
            summary["skipped"] += 1
            continue
        legacy_dpa_accepted = bool(getattr(tenant, "dpa_accepted", False))
        for key in ALL_AGREEMENT_KEYS:
            try:
                existing = await repo.get_for_tenant(tenant_id, key)
                if existing is not None:
                    summary["skipped"] += 1
                    continue
                if dry_run:
                    summary["rows_built"] += 1
                    continue
                built = await retrieve_or_build(tenant_id, key)
                if built is None:
                    summary["skipped"] += 1
                    continue
                # Carry over a legacy DPA acceptance so we don't re-prompt
                # tenants who already agreed before the migration.
                if key == "dpa" and legacy_dpa_accepted and not built.accepted:
                    await repo.update(
                        tenant_id,
                        key,
                        TenantAgreementUpdate(
                            accepted=True,
                            accepted_at=getattr(tenant, "dpa_accepted_at", None)
                            or int(time.time()),
                            accepted_by=getattr(tenant, "dpa_accepted_by", None),
                        ),
                    )
                    summary["rows_accepted"] += 1
                else:
                    summary["rows_built"] += 1
            except Exception:
                summary["failed"] += 1
                logger.warning(
                    "agreement backfill failed tenant=%s key=%s",
                    tenant_id,
                    key,
                    exc_info=True,
                )
    return summary


async def run_agreements_bootstrap() -> Dict[str, Any]:
    """Ensure master documents exist, then backfill per-tenant rows."""
    summary: Dict[str, Any] = {"masters": None, "backfill": None}
    try:
        summary["masters"] = await ensure_master_documents()
    except Exception:
        logger.warning("ensure_master_documents failed at startup", exc_info=True)
    try:
        summary["backfill"] = await backfill_tenant_agreements()
    except Exception:
        logger.warning("agreement backfill failed at startup", exc_info=True)
    return summary
