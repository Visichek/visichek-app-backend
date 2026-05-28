"""Per-tenant agreement service — build, accept, decline, and gate state.

Generalises the former per-tenant DPA service (``services/dpa_service.py``) to
every agreement in ``services.tenant_agreements.config``. For each tenant +
agreement:

* While unaccepted (or accepted at an older master version) the copy is rebuilt
  from the current published master + the tenant's current details on each read.
* On acceptance the resolved body is frozen and the master ``version`` is
  recorded, so later reads return exactly what was agreed.

The gate (``security.auth._enforce_agreement_acceptance``) calls
``agreements_gate_check`` on the hot path; it is backed by a short-lived Redis
cache (``tenant_agreements_state:{tenant_id}``) invalidated on accept / decline
and on master publish.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Dict, List, Optional, Tuple, cast

from bson import ObjectId

from core.redis_cache import cache_db
from repositories import tenant_agreement_repo as repo
from repositories.tenant_repo import get_tenant
from schemas.tenant_agreement_schema import (
    TenantAgreementCreate,
    TenantAgreementOut,
    TenantAgreementUpdate,
)
from services.tenant_agreements import master
from services.tenant_agreements import templating
from services.tenant_agreements.config import (
    ALL_AGREEMENT_KEYS,
    AgreementSpec,
    get_agreement,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gate state cache
# ---------------------------------------------------------------------------

_STATE_KEY_PREFIX = "tenant_agreements_state:"
_STATE_TTL_SECONDS = 300
_OK = "ok"


def _state_key(tenant_id: str) -> str:
    return f"{_STATE_KEY_PREFIX}{tenant_id}"


def invalidate_state(tenant_id: str) -> None:
    """Drop the cached gate state for one tenant (call on accept / decline)."""
    try:
        cache_db.delete(_state_key(tenant_id))
    except Exception:
        pass


def clear_all_states() -> None:
    """Drop every tenant's cached gate state (call when a master is published)."""
    try:
        keys = [
            str(k) for k in cache_db.scan_iter(match=f"{_STATE_KEY_PREFIX}*", count=200)
        ]
        if keys:
            cache_db.delete(*keys)
    except Exception:
        logger.warning("clear_all_states failed", exc_info=True)


# ---------------------------------------------------------------------------
# Per-tenant templating context
# ---------------------------------------------------------------------------


async def _resolve_main_super_admin_email(tenant_id: str) -> Optional[str]:
    """Best-effort lookup of the tenant's main super_admin email. Never raises."""
    if not ObjectId.is_valid(tenant_id):
        return None
    try:
        from repositories.system_user_repo import get_main_super_admin

        main_sa = await get_main_super_admin(tenant_id)
        return getattr(main_sa, "email", None)
    except Exception:
        return None


async def _build_values_for_tenant(tenant_id: str) -> Dict[str, str]:
    """Resolve the fixed allowlist of placeholder values for a tenant."""
    tenant = None
    if ObjectId.is_valid(tenant_id):
        tenant = await get_tenant({"_id": ObjectId(tenant_id)})

    company_name = getattr(tenant, "company_name", None) or ""
    organization_address = getattr(tenant, "organization_address", None) or ""
    dpo_email = getattr(tenant, "dpo_contact_email", None) or ""
    country = getattr(tenant, "country_of_hosting", None) or ""
    retention_days = getattr(tenant, "retention_days", None)
    retention_period = (
        f"{retention_days} days" if retention_days and retention_days > 0 else ""
    )
    contact_email = await _resolve_main_super_admin_email(tenant_id) or dpo_email

    return {
        "company_name": company_name,
        "organization_address": organization_address,
        "contact_email": contact_email,
        "dpo_contact_email": dpo_email,
        "retention_period": retention_period,
        "country_of_hosting": country,
        # effective_date intentionally left unset -> "[To be provided]" unless a
        # future caller threads one in.
        "effective_date": "",
    }


# ---------------------------------------------------------------------------
# Build / read
# ---------------------------------------------------------------------------


async def retrieve_or_build(
    tenant_id: str, agreement_key: str
) -> Optional[TenantAgreementOut]:
    """Return the tenant's agreement copy.

    * Accepted AND at the current master version → frozen snapshot.
    * Otherwise rebuilt (unaccepted) from the current master + tenant details.
    * ``None`` when the agreement key is unknown or the master has no content.
    """
    spec = get_agreement(agreement_key)
    if spec is None:
        return None

    existing = await repo.get_for_tenant(tenant_id, agreement_key)
    built = await master.get_master_for_build(spec.slug)
    if built is None:
        # Master not configured yet — return whatever we have (possibly None).
        return existing

    body_master, version, master_title, master_summary = built

    if existing and existing.accepted and existing.version == version:
        return existing

    values = await _build_values_for_tenant(tenant_id)
    body = templating.resolve_blocks(body_master, values)
    full_text = templating.flatten_blocks(body)
    title = (
        templating.substitute_text(master_title, values) if master_title else spec.title
    )
    summary = (
        templating.substitute_text(master_summary, values) if master_summary else None
    )

    now = int(time.time())
    record = TenantAgreementCreate(
        tenant_id=tenant_id,
        agreement_key=agreement_key,
        master_slug=spec.slug,
        version=version,
        title=title,
        summary=summary,
        full_text=full_text,
        body=body,
        accepted=False,
        date_created=getattr(existing, "created_at", None) or now,
        last_updated=now,
    )
    return await repo.upsert(record)


async def list_for_tenant(tenant_id: str) -> List[TenantAgreementOut]:
    """Resolved copy of every registered agreement for a tenant (ordered)."""
    out: List[TenantAgreementOut] = []
    for key in ALL_AGREEMENT_KEYS:
        built = await retrieve_or_build(tenant_id, key)
        if built is not None:
            out.append(built)
    return out


# ---------------------------------------------------------------------------
# Accept / decline
# ---------------------------------------------------------------------------


async def mark_accepted(
    tenant_id: str,
    agreement_key: str,
    *,
    actor_id: str,
    actor_role: str = "super_admin",
    accepted_at: Optional[int] = None,
    request_id: Optional[str] = None,
) -> Optional[TenantAgreementOut]:
    """Freeze and mark a tenant's agreement copy accepted at the current version.

    Builds/refreshes the copy first (so the frozen snapshot reflects the
    tenant's current details and the latest master), then stamps acceptance.
    Returns ``None`` when the master is not configured. Best-effort audit.
    """
    current = await retrieve_or_build(tenant_id, agreement_key)
    if current is None:
        return None
    if current.accepted:
        invalidate_state(tenant_id)
        return current

    ts = accepted_at or int(time.time())
    updated = await repo.update(
        tenant_id,
        agreement_key,
        TenantAgreementUpdate(
            accepted=True,
            accepted_at=ts,
            accepted_by=actor_id,
        ),
    )
    invalidate_state(tenant_id)

    if updated is not None:
        await _audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action="tenant_agreement.accepted",
            resource_id=updated.id or "",
            tenant_id=tenant_id,
            details={"agreement_key": agreement_key, "version": updated.version},
            request_id=request_id,
        )
    return updated


async def mark_declined(
    tenant_id: str,
    agreement_key: str,
    *,
    actor_id: str,
    actor_role: str = "super_admin",
    request_id: Optional[str] = None,
) -> Optional[TenantAgreementOut]:
    """Record that the tenant declined the current version.

    The gate stays active (the tenant remains blocked) — declining is a logged
    signal, not an account action. Returns the updated row or ``None``.
    """
    current = await retrieve_or_build(tenant_id, agreement_key)
    if current is None:
        return None

    ts = int(time.time())
    updated = await repo.update(
        tenant_id,
        agreement_key,
        TenantAgreementUpdate(declined_at=ts),
    )
    invalidate_state(tenant_id)

    target = updated or current
    await _audit(
        actor_id=actor_id,
        actor_role=actor_role,
        action="tenant_agreement.declined",
        resource_id=target.id or "",
        tenant_id=tenant_id,
        details={"agreement_key": agreement_key, "version": target.version},
        request_id=request_id,
    )
    return updated


async def _audit(
    *,
    actor_id: str,
    actor_role: str,
    action: str,
    resource_id: str,
    tenant_id: str,
    details: dict,
    request_id: Optional[str],
) -> None:
    try:
        from services.audit_service import record_audit_event

        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            resource_type="tenant_agreement",
            resource_id=resource_id,
            tenant_id=tenant_id,
            details=details,
            request_id=request_id,
        )
    except Exception:  # pragma: no cover - audit is fire-and-forget
        logger.warning("tenant_agreement audit failed (%s)", action, exc_info=True)


# ---------------------------------------------------------------------------
# Status + gate
# ---------------------------------------------------------------------------


async def agreements_status(tenant_id: str) -> Tuple[bool, List[str]]:
    """Compute ``(all_accepted, pending_keys)`` against current master versions.

    An agreement whose master is unpublished (``current_version`` is ``None``)
    is NOT required — so no tenant is blocked before the admin publishes the
    masters.
    """
    pending: List[str] = []
    for key in ALL_AGREEMENT_KEYS:
        spec: Optional[AgreementSpec] = get_agreement(key)
        if spec is None:
            continue
        required_version = await master.current_master_version(spec.slug)
        if required_version is None:
            continue  # not published yet -> not required
        row = await repo.get_for_tenant(tenant_id, key)
        accepted_version = row.version if (row and row.accepted) else None
        if accepted_version is None or accepted_version < required_version:
            pending.append(key)
    return (len(pending) == 0, pending)


async def agreements_gate_check(tenant_id: str) -> Tuple[bool, List[str]]:
    """Hot-path gate check, backed by the Redis state cache.

    Returns ``(ok, pending_keys)``. Raises nothing it can avoid — but the gate
    caller (``security.auth``) still wraps it for fail-open safety.
    """
    if not tenant_id:
        return (True, [])

    try:
        cached = cast(Optional[str], cache_db.get(_state_key(tenant_id)))
    except Exception:
        cached = None
    if cached == _OK:
        return (True, [])
    if cached:
        try:
            pending = json.loads(cached)
            if isinstance(pending, list):
                return (False, [str(p) for p in pending])
        except Exception:
            pass

    ok, pending = await agreements_status(tenant_id)
    try:
        cache_db.setex(
            _state_key(tenant_id),
            _STATE_TTL_SECONDS,
            _OK if ok else json.dumps(pending),
        )
    except Exception:
        pass
    return (ok, pending)


async def pending_agreements(tenant_id: str) -> List[str]:
    _, pending = await agreements_status(tenant_id)
    return pending
