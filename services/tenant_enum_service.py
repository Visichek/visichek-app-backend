from __future__ import annotations

import logging
from typing import List, Optional

from bson import ObjectId

from core.errors import AppException, ErrorCode, resource_not_found
from repositories.tenant_enum_repo import (
    create_tenant_enum,
    delete_tenant_enum,
    get_tenant_enum,
    list_tenant_enums,
    update_tenant_enum,
    upsert_tenant_enum,
)
from repositories.tenant_repo import get_tenant
from schemas.imports import TenantEnumKind
from schemas.tenant_enum_schema import (
    TenantEnumBundleOut,
    TenantEnumCreate,
    TenantEnumOption,
    TenantEnumOut,
    TenantEnumPublicOut,
    TenantEnumUpdate,
)
from services.audit_service import record_audit_event

logger = logging.getLogger(__name__)


# Defaults shipped with every tenant. Tenants can edit, deactivate, or
# extend any of these through ``PATCH /v1/tenants/{tenant_id}/enums/{kind}``.
# Keep the seed sets short — anything tenant-specific (e.g. department-
# specific purposes) belongs in the tenant's own list.
DEFAULT_PURPOSES_OF_VISIT: List[TenantEnumOption] = [
    TenantEnumOption(value="meeting", label="Meeting", sort_order=10),
    TenantEnumOption(value="interview", label="Interview", sort_order=20),
    TenantEnumOption(value="delivery", label="Delivery", sort_order=30),
    TenantEnumOption(value="contractor", label="Contractor / Maintenance", sort_order=40),
    TenantEnumOption(value="event", label="Event Attendance", sort_order=50),
    TenantEnumOption(value="tour", label="Office Tour", sort_order=60),
    TenantEnumOption(value="personal", label="Personal Visit", sort_order=70),
    TenantEnumOption(value="other", label="Other", sort_order=999),
]

DEFAULT_ID_TYPES: List[TenantEnumOption] = [
    TenantEnumOption(value="national_id", label="National ID (NIN)", sort_order=10),
    TenantEnumOption(value="drivers_license", label="Driver's License", sort_order=20),
    TenantEnumOption(value="passport", label="International Passport", sort_order=30),
    TenantEnumOption(value="voters_card", label="Voter's Card", sort_order=40),
    TenantEnumOption(value="employee_id", label="Employee ID", sort_order=50, active=False),
]

DEFAULT_VISITOR_CATEGORIES: List[TenantEnumOption] = [
    TenantEnumOption(value="guest", label="Guest", sort_order=10),
    TenantEnumOption(value="vendor", label="Vendor", sort_order=20),
    TenantEnumOption(value="contractor", label="Contractor", sort_order=30),
    TenantEnumOption(value="vip", label="VIP", sort_order=40),
    TenantEnumOption(value="staff", label="Staff", sort_order=50),
]

_DEFAULTS: dict[TenantEnumKind, tuple[List[TenantEnumOption], bool]] = {
    # purpose-of-visit is the most heterogeneous — let tenants free-type so
    # we don't block a check-in over a missing seed value.
    TenantEnumKind.PURPOSE_OF_VISIT: (DEFAULT_PURPOSES_OF_VISIT, True),
    # id_type must match an OCR-supported type, no free-typing.
    TenantEnumKind.ID_TYPE: (DEFAULT_ID_TYPES, False),
    TenantEnumKind.VISITOR_CATEGORY: (DEFAULT_VISITOR_CATEGORIES, True),
}


def get_default_options(kind: TenantEnumKind) -> List[TenantEnumOption]:
    options, _ = _DEFAULTS[kind]
    return [opt.model_copy() for opt in options]


def get_default_allow_custom(kind: TenantEnumKind) -> bool:
    _, allow_custom = _DEFAULTS[kind]
    return allow_custom


async def seed_tenant_enums(tenant_id: str) -> List[TenantEnumOut]:
    """Insert the default enum rows for a tenant if they don't exist.

    Idempotent — re-running on the same tenant is a no-op for kinds that
    already have a row (handled by ``upsert_tenant_enum`` with
    ``$setOnInsert``). Designed to be invoked from
    :func:`services.tenant_service.bootstrap_tenant`.
    """
    out: list[TenantEnumOut] = []
    for kind in TenantEnumKind:
        payload = TenantEnumCreate(
            tenant_id=tenant_id,
            kind=kind,
            options=get_default_options(kind),
            allow_custom=get_default_allow_custom(kind),
        )
        out.append(await upsert_tenant_enum(payload))
    return out


async def get_or_seed_enum(
    tenant_id: str, kind: TenantEnumKind
) -> TenantEnumOut:
    """Return the row for one kind, seeding the default if absent.

    The kiosk reads through this helper so a tenant that hasn't yet
    customised their enums still gets a usable picker.
    """
    existing = await get_tenant_enum(tenant_id, kind)
    if existing is not None:
        return existing
    payload = TenantEnumCreate(
        tenant_id=tenant_id,
        kind=kind,
        options=get_default_options(kind),
        allow_custom=get_default_allow_custom(kind),
    )
    return await upsert_tenant_enum(payload)


async def list_enums_for_tenant(tenant_id: str) -> List[TenantEnumOut]:
    """Authenticated super_admin list — every kind, every option."""
    rows = await list_tenant_enums(tenant_id)
    seen = {r.kind for r in rows}
    # Auto-seed any kind the tenant hasn't customised yet so the
    # configuration UI never shows an empty list to the super_admin.
    for kind in TenantEnumKind:
        if kind not in seen:
            payload = TenantEnumCreate(
                tenant_id=tenant_id,
                kind=kind,
                options=get_default_options(kind),
                allow_custom=get_default_allow_custom(kind),
            )
            rows.append(await upsert_tenant_enum(payload))
    return rows


async def update_enum_options(
    *,
    tenant_id: str,
    kind: TenantEnumKind,
    data: TenantEnumUpdate,
    actor_id: str,
    actor_role: str,
    request_id: Optional[str] = None,
) -> TenantEnumOut:
    """Replace the option list and/or ``allow_custom`` flag for one enum."""
    if not ObjectId.is_valid(tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)

    # Ensure the row exists (auto-seed on first write so tenants who
    # never read the defaults still get a sane base to PATCH onto).
    existing = await get_or_seed_enum(tenant_id, kind)

    before_options = [opt.model_dump() for opt in existing.options]
    before_allow_custom = existing.allow_custom

    updated = await update_tenant_enum(tenant_id, kind, data)
    if updated is None:
        # Row vanished between get_or_seed_enum and update — extremely
        # rare but treat as a 409 so the client retries.
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Enum update raced with a concurrent write — please retry",
        )

    after_options = [opt.model_dump() for opt in updated.options]
    changes: dict = {}
    if before_options != after_options:
        changes["options"] = {"before": before_options, "after": after_options}
    if data.allow_custom is not None and before_allow_custom != updated.allow_custom:
        changes["allow_custom"] = {
            "before": before_allow_custom,
            "after": updated.allow_custom,
        }

    if changes:
        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            action="tenant_enum.updated",
            resource_type="tenant_enum",
            resource_id=str(updated.id or ""),
            tenant_id=tenant_id,
            details={"kind": kind.value, "changes": changes},
            request_id=request_id,
        )

    return updated


async def reset_enum_to_defaults(
    *,
    tenant_id: str,
    kind: TenantEnumKind,
    actor_id: str,
    actor_role: str,
    request_id: Optional[str] = None,
) -> TenantEnumOut:
    """Replace the tenant's option list with the system defaults.

    Used by the "reset" button on the super_admin enum editor.
    """
    if not ObjectId.is_valid(tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)

    # Wipe and re-seed atomically by deleting then upserting.
    await delete_tenant_enum(tenant_id, kind)
    payload = TenantEnumCreate(
        tenant_id=tenant_id,
        kind=kind,
        options=get_default_options(kind),
        allow_custom=get_default_allow_custom(kind),
    )
    seeded = await create_tenant_enum(payload)

    await record_audit_event(
        actor_id=actor_id,
        actor_role=actor_role,
        action="tenant_enum.reset",
        resource_type="tenant_enum",
        resource_id=str(seeded.id or ""),
        tenant_id=tenant_id,
        details={"kind": kind.value},
        request_id=request_id,
    )
    return seeded


def _public_projection(row: TenantEnumOut) -> TenantEnumPublicOut:
    """Strip inactive options + drop bookkeeping fields."""
    active_options = sorted(
        [opt for opt in row.options if opt.active],
        key=lambda o: (o.sort_order, o.label.lower()),
    )
    return TenantEnumPublicOut(
        kind=row.kind,
        allow_custom=row.allow_custom,
        options=active_options,
    )


async def public_enum_bundle_for_tenant(tenant_id: str) -> TenantEnumBundleOut:
    """Build the kiosk-facing enum bundle.

    Auto-seeds any missing kinds so the kiosk never sees a half-empty
    bundle — the seed defaults are explicitly *the* contract for kinds
    a tenant has not touched yet.
    """
    rows = await list_enums_for_tenant(tenant_id)
    by_kind = {row.kind: row for row in rows}
    enums: dict[str, TenantEnumPublicOut] = {}
    for kind in TenantEnumKind:
        row = by_kind.get(kind)
        if row is None:
            row = await get_or_seed_enum(tenant_id, kind)
        enums[kind.value] = _public_projection(row)
    return TenantEnumBundleOut(tenant_id=tenant_id, enums=enums)


def validate_enum_value(
    bundle: TenantEnumBundleOut,
    kind: TenantEnumKind,
    value: Optional[str],
) -> None:
    """Raise 400 if ``value`` is not a member of the active set.

    ``value`` may be ``None`` for optional fields — the caller decides
    requiredness. ``allow_custom`` short-circuits the check, mirroring
    what the kiosk presents to the visitor.
    """
    if value is None or value == "":
        return
    enum_view = bundle.enums.get(kind.value)
    if enum_view is None:
        # No row at all — fall back to permissive (defensive: the bundle
        # builder seeds every kind, so this branch is unreachable in
        # normal operation).
        return
    if enum_view.allow_custom:
        return
    if not any(opt.value == value for opt in enum_view.options):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"'{value}' is not an accepted {kind.value} for this tenant",
            details={
                "kind": kind.value,
                "accepted": [opt.value for opt in enum_view.options],
            },
        )


