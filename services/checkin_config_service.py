from __future__ import annotations

from typing import Optional

from bson import ObjectId

from core.errors import resource_not_found
from repositories.checkin_config_repo import (
    create_checkin_config,
    delete_checkin_config,
    get_active_checkin_config_for_tenant,
    get_checkin_config,
    get_checkin_configs,
    update_checkin_config,
)
from repositories.tenant_repo import get_tenant
from schemas.checkin_config_schema import (
    CheckinConfigCreate,
    CheckinConfigOut,
    CheckinConfigUpdate,
    CheckinFieldDef,
    PublicCheckinConfigOut,
)
from schemas.imports import CheckinFieldCategory, TenantEnumKind
from schemas.tenant_form_schema import (
    FormFieldDefinition,
    TenantFormOut,
)


# System-default required fields. Phone + Full Name are *system-mandated*
# (the visitor profile uniqueness key + badge label); everything else is
# tenant-configurable on the active CheckinConfig. Email is shown by
# default but optional — tenants who care about email capture can flip
# ``required=True`` on their copy of the config.
DEFAULT_REQUIRED_FIELDS: list[CheckinFieldDef] = [
    CheckinFieldDef(
        key="full_name",
        label="Full Name",
        type="text",
        required=True,
        category=CheckinFieldCategory.BIO,
    ),
    CheckinFieldDef(
        key="phone",
        label="Phone Number",
        type="tel",
        required=True,
        category=CheckinFieldCategory.BIO,
    ),
    CheckinFieldDef(
        key="email",
        label="Email (optional)",
        type="email",
        required=False,
        category=CheckinFieldCategory.BIO,
    ),
    CheckinFieldDef(
        key="company",
        label="Company",
        type="text",
        required=False,
        category=CheckinFieldCategory.BIO,
    ),
    CheckinFieldDef(
        key="purpose",
        label="Purpose of Visit",
        type="select",
        required=True,
        category=CheckinFieldCategory.TENANT_SPECIFIC,
        enum_kind=TenantEnumKind.PURPOSE_OF_VISIT,
    ),
]


async def _resolve_tenant_logo_url(tenant_id: str) -> Optional[str]:
    try:
        from repositories.branding_repo import get_branding

        branding = await get_branding({"tenant_id": tenant_id})
        if branding and branding.logo_object_key:
            from services.storage_url_service import try_resolve_download_url

            return try_resolve_download_url(branding.logo_object_key)
    except Exception:
        pass
    return None


async def create_config(
    payload: CheckinConfigCreate,
    *,
    preassigned_id: Optional[str] = None,
) -> CheckinConfigOut:
    """Create a new check-in configuration."""
    if not ObjectId.is_valid(payload.tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=payload.tenant_id)
    tenant = await get_tenant({"_id": ObjectId(payload.tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=payload.tenant_id)
    return await create_checkin_config(payload, preassigned_id=preassigned_id)


# ─── Form ↔ CheckinConfig sync helpers ─────────────────────────────
#
# The tenant form builder (target_type=CHECKIN) is the user-facing way
# super_admins describe the kiosk form. The legacy ``CheckinConfig`` row
# carries id/feature toggles only — fields are sourced from the
# published tenant form when one exists. Helpers below merge the two
# so callers (kiosk, receptionist, submit validation) see a single
# consistent list.


_FIELD_TYPE_MAP: dict[str, str] = {
    "text": "text",
    "long_text": "text",
    "email": "email",
    "phone": "tel",
    "url": "url",
    "number": "number",
    "integer": "number",
    "boolean": "boolean",
    "date": "date",
    "time": "time",
    "datetime": "datetime",
    "select": "select",
    "multi_select": "multi_select",
    "country": "select",
    "address": "text",
    "file": "file",
    "image": "image",
    "signature": "signature",
    "consent_checkbox": "consent_checkbox",
    "rating": "rating",
    "id_document": "file",
    "host_picker": "select",
    "visitor_picker": "select",
    "calculated": "text",
}


def _form_field_to_checkin_field(field: FormFieldDefinition) -> CheckinFieldDef:
    """Project a tenant form field onto the kiosk CheckinFieldDef shape.

    The kiosk renderer was originally written against ``CheckinFieldDef``
    so we coerce here instead of teaching the kiosk a new schema. Tenant
    form fields are always considered ``tenant_specific`` (BIO fields
    are system-managed and emitted separately by ``DEFAULT_REQUIRED_FIELDS``).
    """
    field_type = field.type.value if hasattr(field.type, "value") else str(field.type)
    options_payload: Optional[list[dict]] = None
    if field.options:
        options_payload = [
            {"key": o.key, "label": o.label} for o in field.options if not o.archived
        ]
    return CheckinFieldDef(
        key=field.field_id,
        label=field.label or field.field_id,
        type=_FIELD_TYPE_MAP.get(field_type, "text"),
        required=bool(field.required),
        category=CheckinFieldCategory.TENANT_SPECIFIC,
        options=options_payload,
        enum_kind=None,
        placeholder=field.placeholder,
        help_text=field.help_text,
    )


async def _get_active_checkin_form(tenant_id: str) -> Optional[TenantFormOut]:
    """Fetch the published TenantForm for ``target_type=checkin`` if any."""
    try:
        from repositories.tenant_form_repo import get_active_by_target
        from schemas.imports import FormTargetType

        return await get_active_by_target(
            tenant_id=tenant_id, target_type=FormTargetType.CHECKIN.value
        )
    except Exception:
        return None


def _merge_required_fields(
    *,
    config_fields: Optional[list[CheckinFieldDef]],
    form: Optional[TenantFormOut],
) -> list[CheckinFieldDef]:
    """Merge tenant form fields into the CheckinConfig field list.

    Source-of-truth precedence:

    1. The published tenant form (when one exists). Fields the
       super_admin published win over equally-keyed system defaults so
       making ``email`` required or dropping ``company`` actually takes
       effect on the kiosk. Their render order is preserved.
    2. Any legacy CheckinConfig ``tenant_specific`` fields that the
       form does not already cover.
    3. System BIO defaults (``full_name``, ``phone``, ``email``,
       ``company``) for keys nobody else provided — this guarantees a
       freshly bootstrapped tenant has a usable kiosk before any form
       is published.
    4. The ``purpose`` picker is always present (defaulted from
       :data:`DEFAULT_REQUIRED_FIELDS`) — every kiosk needs to bucket
       the visit.
    """
    merged: list[CheckinFieldDef] = []
    seen_keys: set[str] = set()

    if form is not None:
        for form_field in form.fields or []:
            projected = _form_field_to_checkin_field(form_field)
            if projected.key in seen_keys:
                continue
            merged.append(projected)
            seen_keys.add(projected.key)

    for config_field in config_fields or []:
        if config_field.key in seen_keys:
            continue
        merged.append(config_field)
        seen_keys.add(config_field.key)

    bio_defaults = [
        f for f in DEFAULT_REQUIRED_FIELDS if f.category == CheckinFieldCategory.BIO
    ]
    for default_field in bio_defaults:
        if default_field.key in seen_keys:
            continue
        merged.append(default_field)
        seen_keys.add(default_field.key)

    if "purpose" not in seen_keys:
        purpose_default = next(
            (f for f in DEFAULT_REQUIRED_FIELDS if f.key == "purpose"), None
        )
        if purpose_default is not None:
            merged.append(purpose_default)
            seen_keys.add("purpose")

    return merged


async def resolve_required_fields_for_tenant(
    tenant_id: str,
) -> tuple[list[CheckinFieldDef], Optional[TenantFormOut]]:
    """Return the merged (form + config) required-field set for a tenant.

    Used by both the public kiosk config endpoint and the kiosk submit
    validators so a single source of truth exists. The TenantFormOut is
    returned alongside so callers that need the form_id / version (e.g.
    submit handlers writing form_id onto the check-in record) can pick
    it up without a second lookup.
    """
    config = await get_active_checkin_config_for_tenant(tenant_id)
    form = await _get_active_checkin_form(tenant_id)
    merged = _merge_required_fields(
        config_fields=(config.required_fields if config else None),
        form=form,
    )
    return merged, form


async def _plan_allows_public_self_checkin(tenant_id: str) -> bool:
    """True iff the tenant's plan grants the public kiosk submit feature.

    Free / Starter tenants do NOT get the unauthenticated kiosk submit —
    they must instead use a receptionist / super_admin / dept_admin
    token to drive the kiosk. The check resolves the plan via the same
    fnmatch path the middleware uses for ``/v1/public/tenants/*/submit``.
    """
    try:
        from services.plan_limits import is_feature_enabled

        return await is_feature_enabled(
            tenant_id=tenant_id,
            endpoint_pattern="/v1/public/tenants/*/submit",
            method="POST",
        )
    except Exception:
        # Fail OPEN — the auth path will still be checked at submit
        # time. Better to surface a usable kiosk than to silently
        # require auth on a flaky plan lookup.
        return True


async def enforce_kiosk_submit_access(
    *,
    tenant_id: str,
    principal: Optional[object],
) -> None:
    """Authorize a kiosk submit against the tenant's plan + caller.

    Two outcomes:

    * Tenant's plan grants ``/v1/public/tenants/*/submit`` →
      anonymous calls are allowed; ``principal`` is ignored.
    * Plan denies the public endpoint (Free / Starter) →
      a system user principal with visitor permissions
      (``super_admin`` / ``dept_admin`` / ``receptionist``) is
      required. Anyone else (no token, application admin token, or
      auditor / dpo / security_officer) gets a 403 explaining the
      kiosk must be driven by a logged-in receptionist on the
      current plan.

    The principal type is ``object`` to avoid a hard import cycle on
    ``AuthPrincipal``; we duck-type the ``role`` + ``tenant_id``
    attributes below.
    """
    from core.errors import AppException, ErrorCode

    if await _plan_allows_public_self_checkin(tenant_id):
        return

    if principal is None:
        raise AppException(
            status_code=403,
            code=ErrorCode.FEATURE_DISABLED,
            message=(
                "This tenant's plan does not include unattended public "
                "kiosk check-ins. Log in as a receptionist / department "
                "admin / super admin and resubmit, or upgrade the plan "
                "to enable kiosk self check-in."
            ),
            details={"required": "system_user_with_visitor_permissions"},
        )

    role = getattr(principal, "role", "")
    principal_tenant_id = getattr(principal, "tenant_id", None)
    if role not in ("super_admin", "dept_admin", "receptionist"):
        raise AppException(
            status_code=403,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message=(
                "Public self check-in is disabled on this plan and only "
                "tenant receptionists, department admins, or super admins "
                "may drive the kiosk."
            ),
            details={"role": role},
        )
    if principal_tenant_id and principal_tenant_id != tenant_id:
        raise AppException(
            status_code=403,
            code=ErrorCode.AUTH_PERMISSION_DENIED,
            message="System user is not scoped to this tenant.",
            details={
                "tenant_id": tenant_id,
                "principal_tenant_id": principal_tenant_id,
            },
        )


async def _plan_denies_kyc(tenant_id: str) -> bool:
    """True iff the tenant's current plan explicitly denies KYC.

    Used to force ``id_upload_enabled = false`` on the public kiosk
    config for tenants whose plan can't actually serve the Dojah flow
    (Free, Starter). The stored config row is left untouched so the
    preference comes back automatically on upgrade.

    Returns ``False`` (i.e. do NOT override) on resolution failure —
    better to surface the Verify button and let it silently degrade via
    ``/v1/kyc/initiate`` than to hide KYC for a tenant whose plan we
    couldn't look up.
    """
    try:
        from core.plan_enforcement import _check_feature_access
        from services.plan_cache_service import resolve_tenant_plan

        resolved = await resolve_tenant_plan(tenant_id)
        if not resolved:
            return False
        allowed, _ = _check_feature_access(
            "/v1/kyc/initiate",
            "POST",
            resolved.get("feature_rules") or [],
        )
        return not allowed
    except Exception:
        return False


async def resolve_public_config(checkin_config_id: str) -> PublicCheckinConfigOut:
    """Resolve a public check-in config with tenant info and logo.

    Returns a sanitized DTO suitable for public/unauthenticated access.
    """
    config = await get_checkin_config({"_id": checkin_config_id, "active": True})
    if not config:
        raise resource_not_found(
            resource="CheckinConfig", resource_id=checkin_config_id
        )

    if not ObjectId.is_valid(config.tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=config.tenant_id)
    tenant = await get_tenant({"_id": ObjectId(config.tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=config.tenant_id)

    logo_url = await _resolve_tenant_logo_url(config.tenant_id)

    id_upload_enabled = config.id_upload_enabled
    if id_upload_enabled and await _plan_denies_kyc(config.tenant_id):
        id_upload_enabled = False

    form = await _get_active_checkin_form(config.tenant_id)
    merged_fields = _merge_required_fields(
        config_fields=config.required_fields, form=form
    )
    public_self_checkin = await _plan_allows_public_self_checkin(config.tenant_id)

    return PublicCheckinConfigOut(
        checkin_config_id=config.id or "",
        tenant_id=config.tenant_id,
        tenant_name=tenant.company_name or "",
        logo_url=logo_url,
        id_upload_enabled=id_upload_enabled,
        allow_returning_visitor_lookup=config.allow_returning_visitor_lookup,
        required_fields=merged_fields,
        tenant_form_id=(form.form_id if form else None),
        tenant_form_version=(form.version if form else None),
        public_self_checkin_enabled=public_self_checkin,
    )


async def resolve_public_config_by_tenant(tenant_id: str) -> PublicCheckinConfigOut:
    """Resolve the active check-in config for a tenant.

    Falls back to a default config when the tenant hasn't configured one yet
    so the public kiosk/registration UI can still render a usable form.
    """
    if not ObjectId.is_valid(tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)

    logo_url = await _resolve_tenant_logo_url(tenant_id)
    config = await get_active_checkin_config_for_tenant(tenant_id)
    plan_denies_kyc = await _plan_denies_kyc(tenant_id)
    public_self_checkin = await _plan_allows_public_self_checkin(tenant_id)

    form = await _get_active_checkin_form(tenant_id)
    merged_fields = _merge_required_fields(
        config_fields=(config.required_fields if config else None),
        form=form,
    )

    if config is None:
        return PublicCheckinConfigOut(
            checkin_config_id="",
            tenant_id=tenant_id,
            tenant_name=tenant.company_name or "",
            logo_url=logo_url,
            id_upload_enabled=not plan_denies_kyc,
            allow_returning_visitor_lookup=True,
            required_fields=merged_fields,
            tenant_form_id=(form.form_id if form else None),
            tenant_form_version=(form.version if form else None),
            public_self_checkin_enabled=public_self_checkin,
        )

    id_upload_enabled = config.id_upload_enabled
    if id_upload_enabled and plan_denies_kyc:
        id_upload_enabled = False

    return PublicCheckinConfigOut(
        checkin_config_id=config.id or "",
        tenant_id=tenant_id,
        tenant_name=tenant.company_name or "",
        logo_url=logo_url,
        id_upload_enabled=id_upload_enabled,
        allow_returning_visitor_lookup=config.allow_returning_visitor_lookup,
        required_fields=merged_fields,
        tenant_form_id=(form.form_id if form else None),
        tenant_form_version=(form.version if form else None),
        public_self_checkin_enabled=public_self_checkin,
    )


async def update_config(config_id: str, data: CheckinConfigUpdate) -> CheckinConfigOut:
    """Update a check-in configuration."""
    return await update_checkin_config(config_id, data)


async def list_configs_for_tenant(
    tenant_id: str, skip: int = 0, limit: int = 20
) -> tuple[list[CheckinConfigOut], int]:
    """List check-in configurations for a tenant."""
    configs = await get_checkin_configs(
        {"tenant_id": tenant_id}, skip=skip, limit=limit
    )
    from repositories.checkin_config_repo import (
        COLLECTION,
    )
    from core.database import db

    total = await db[COLLECTION].count_documents({"tenant_id": tenant_id})
    return configs, total


async def delete_config(config_id: str) -> None:
    """Delete a check-in configuration."""
    await delete_checkin_config(config_id)
