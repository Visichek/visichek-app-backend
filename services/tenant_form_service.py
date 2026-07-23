"""Tenant form business logic — draft autosave, publish, archive, clone.

The service layer is the only place that knows the draft / published
column distinction. Routes call into it (or enqueue a writer that does);
repositories never compute publish promotion themselves.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from bson import ObjectId

from config.default_forms import (
    checkin_department_field,
    default_description_for_target,
    default_fields_for_target,
    default_name_for_target,
)
from core.errors import AppException, ErrorCode, resource_not_found
from core.queue.precompute import delete_precompute, mark_scope_dirty
from repositories.tenant_form_repo import (
    count_tenant_forms,
    create_tenant_form,
    get_active_by_target,
    get_head_by_target,
    get_head_for_form_id,
    get_tenant_form,
    get_tenant_form_by_row_id,
    list_tenant_forms,
    supersede_existing_active,
    unset_draft,
    update_tenant_form,
)
from schemas.imports import FormFieldType, FormStatus, FormTargetType
from schemas.tenant_form_schema import (
    FormFieldDefinition,
    TenantFormCreate,
    TenantFormDraftPatch,
    TenantFormOut,
    TenantFormPublicOut,
    TenantFormUpdate,
)
from services.audit_service import record_audit_event


# ─── Helpers ───────────────────────────────────────────


def _invalidate_form_caches(tenant_id: str, target_type: str) -> None:
    """Drop precompute entries + flag the tenant scope dirty.

    Called from every mutation so a fresh GET (kiosk, receptionist,
    super_admin) sees post-commit state instead of the 5-minute
    precompute TTL of pre-mutation state.
    """
    # Import lazily — keeps the service import-side-effect-free and
    # avoids a cycle with services.tenant_form_writer which imports back
    # into this module for its precompute loaders.
    from services.tenant_form_writer import (
        RESOURCE_LIST,
        enqueue_refresh,
        resource_active,
        resource_public,
    )

    for resource in (
        RESOURCE_LIST,
        resource_active(target_type),
        resource_public(target_type),
    ):
        try:
            delete_precompute(resource, tenant_id=tenant_id)
        except Exception:
            pass

    try:
        mark_scope_dirty(f"t:{tenant_id}")
    except Exception:
        pass

    enqueue_refresh(tenant_id=tenant_id, target_type=target_type)


def _new_form_id() -> str:
    return uuid4().hex


def _to_public(form: TenantFormOut) -> TenantFormPublicOut:
    return TenantFormPublicOut(
        form_id=form.form_id,
        tenant_id=form.tenant_id,
        target_type=form.target_type,
        name=form.name,
        description=form.description,
        version=form.version,
        fields=form.fields,
    )


def _enforce_locked_department_field(
    fields: Optional[List[FormFieldDefinition]], target_type: str
) -> Optional[List[FormFieldDefinition]]:
    """Server-side lock for the system Department field on check-in forms.

    The public registration QR (GENERAL scope) relies on the check-in form
    carrying a required ``department_id`` picker, so for
    ``target_type == "checkin"``:

    * a field list missing the department field gets the system default
      re-injected (see :func:`config.default_forms.checkin_department_field`);
    * an existing department field (matched on ``field_id`` or ``maps_to``)
      has ``required`` / ``locked`` / ``maps_to`` / ``type`` forced back to
      the system values — label / help_text / placeholder / order stay
      tenant-editable.

    Appointment / visit_session targets are exempt: department is a
    first-class column there, not a form field. ``None`` (no draft fields
    supplied) passes through untouched.
    """
    if fields is None or target_type != FormTargetType.CHECKIN.value:
        return fields

    result: List[FormFieldDefinition] = []
    found = False
    for field in fields:
        if field.field_id == "department_id" or field.maps_to == "department_id":
            found = True
            result.append(
                field.model_copy(
                    update={
                        "type": FormFieldType.SELECT,
                        "required": True,
                        "locked": True,
                        "maps_to": "department_id",
                    }
                )
            )
        else:
            result.append(field)

    if not found:
        default = checkin_department_field()
        max_order = max((f.order for f in result), default=0)
        result.append(default.model_copy(update={"order": max_order + 10}))

    return result


def _validate_publish(
    name: str, fields: List[FormFieldDefinition]
) -> List[Dict[str, Any]]:
    """Strict publish-time validation. Returns a list of issue dicts.

    Empty list means the form is publishable. Each issue is shaped for
    direct return in the 422 ``details`` array.
    """
    issues: List[Dict[str, Any]] = []

    if not name or not name.strip():
        issues.append(
            {
                "fieldId": None,
                "code": "NAME_REQUIRED",
                "message": "Form name is required to publish.",
            }
        )

    if not fields:
        issues.append(
            {
                "fieldId": None,
                "code": "NO_FIELDS",
                "message": "A published form must have at least one field.",
            }
        )

    seen_ids: set[str] = set()
    for field in fields:
        if not field.label or not field.label.strip():
            issues.append(
                {
                    "fieldId": field.field_id,
                    "code": "EMPTY_LABEL",
                    "message": "Every field must have a label to publish.",
                }
            )

        if field.field_id in seen_ids:
            issues.append(
                {
                    "fieldId": field.field_id,
                    "code": "DUPLICATE_FIELD_ID",
                    "message": f"Duplicate field_id '{field.field_id}'.",
                }
            )
        seen_ids.add(field.field_id)

        if field.type.value == "consent_checkbox" and not (
            field.consent_text and field.consent_text.strip()
        ):
            issues.append(
                {
                    "fieldId": field.field_id,
                    "code": "CONSENT_TEXT_REQUIRED",
                    "message": "consent_checkbox fields require consent_text.",
                }
            )

        if field.type.value in ("select", "multi_select"):
            # The system Department picker deliberately carries an empty
            # option list — the kiosk resolves options live from the
            # tenant's active departments at render time.
            if field.maps_to == "department_id":
                continue
            if not field.options or len(field.options) == 0:
                issues.append(
                    {
                        "fieldId": field.field_id,
                        "code": "OPTIONS_REQUIRED",
                        "message": "select / multi_select fields require at "
                        "least one option.",
                    }
                )

        if field.type.value == "calculated" and field.formula is None:
            issues.append(
                {
                    "fieldId": field.field_id,
                    "code": "FORMULA_REQUIRED",
                    "message": "calculated fields require a formula.",
                }
            )

    return issues


def _diff_for_audit(
    before: Optional[TenantFormOut], after: TenantFormOut
) -> Dict[str, Any]:
    """Field-level diff for audit details."""
    if before is None:
        return {
            "from_version": None,
            "to_version": after.version,
            "field_count": len(after.fields),
        }

    before_fields = {f.field_id: f.model_dump() for f in before.fields}
    after_fields = {f.field_id: f.model_dump() for f in after.fields}

    added = sorted(set(after_fields) - set(before_fields))
    removed = sorted(set(before_fields) - set(after_fields))
    changed = sorted(
        fid
        for fid in set(before_fields) & set(after_fields)
        if before_fields[fid] != after_fields[fid]
    )
    return {
        "from_version": before.version,
        "to_version": after.version,
        "added_fields": added,
        "removed_fields": removed,
        "changed_fields": changed,
    }


def _warnings_for_draft(
    name: Optional[str], fields: Optional[List[FormFieldDefinition]]
) -> List[Dict[str, Any]]:
    """Soft hints surfaced on autosave PATCH responses."""
    hints: List[Dict[str, Any]] = []
    if name is not None and not name.strip():
        hints.append(
            {
                "fieldId": None,
                "code": "EMPTY_NAME",
                "message": "Form name is empty — required to publish.",
            }
        )
    for field in fields or []:
        if not field.label or not field.label.strip():
            hints.append(
                {
                    "fieldId": field.field_id,
                    "code": "EMPTY_LABEL",
                    "message": "Question text is empty.",
                }
            )
    return hints


# ─── Public retrieval ───────────────────────────────────────────


async def retrieve_form_by_id(tenant_id: str, form_id: str) -> Optional[TenantFormOut]:
    """Return the head row (active or draft) for a form_id."""
    return await get_head_for_form_id(tenant_id=tenant_id, form_id=form_id)


async def retrieve_form_by_row_id(
    tenant_id: str, row_id: str
) -> Optional[TenantFormOut]:
    """Return a specific row by its Mongo _id, tenant-scoped for safety."""
    row = await get_tenant_form_by_row_id(row_id)
    if not row or row.tenant_id != tenant_id:
        return None
    return row


async def list_forms_for_tenant(
    tenant_id: str,
    *,
    target_type: Optional[str] = None,
    status: Optional[str] = None,
    start: int = 0,
    stop: int = 100,
) -> List[TenantFormOut]:
    """List forms for a tenant. By default returns only head rows
    (status ∈ {active, draft, archived}); superseded rows are excluded
    unless explicitly requested via ``status``.
    """
    filt: Dict[str, Any] = {"tenant_id": tenant_id}
    if target_type:
        filt["target_type"] = target_type
    if status:
        filt["status"] = status
    else:
        filt["status"] = {"$in": ["active", "draft", "archived"]}
    return await list_tenant_forms(filt, start=start, stop=stop)


async def retrieve_active_by_target(
    tenant_id: str, target_type: str
) -> Optional[TenantFormOut]:
    """Active row for (tenant, target_type), or None."""
    return await get_active_by_target(tenant_id=tenant_id, target_type=target_type)


async def retrieve_public_active_by_target(
    tenant_id: str, target_type: str
) -> Optional[TenantFormPublicOut]:
    """Kiosk-facing read — published state only, no draft fields."""
    if not ObjectId.is_valid(tenant_id):
        return None
    form = await get_active_by_target(tenant_id=tenant_id, target_type=target_type)
    if not form:
        return None
    return _to_public(form)


async def retrieve_head_by_target(
    tenant_id: str, target_type: str
) -> Optional[TenantFormOut]:
    return await get_head_by_target(tenant_id=tenant_id, target_type=target_type)


# ─── Mutations ───────────────────────────────────────────


async def create_form_shell(
    *,
    tenant_id: str,
    target_type: str,
    name: str = "",
    description: Optional[str] = None,
    fields: Optional[List[FormFieldDefinition]] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
    preassigned_id: Optional[str] = None,
    seed_defaults: bool = True,
) -> TenantFormOut:
    """Create a brand-new form for a tenant in draft status.

    When ``seed_defaults=True`` (the default) and the caller did not
    supply name / description / fields, the draft is pre-populated with
    the system defaults for the ``target_type`` (see
    :mod:`config.default_forms`). This means a freshly bootstrapped
    check-in form already lists ``full_name``, ``phone``, ``email``,
    ``company`` and ``purpose`` so the super_admin can edit them
    instead of starting from a blank canvas.

    The published columns (``fields``, ``version``) stay empty regardless
    — the caller still has to ``publish`` to make the seeded draft go
    live.
    """
    form_id = _new_form_id()
    now = int(time.time())

    seeded_name = name
    seeded_description = description
    seeded_fields = fields
    if seed_defaults and not name and not description and not fields:
        seeded_name = default_name_for_target(target_type)
        seeded_description = default_description_for_target(target_type) or None
        seeded_fields = default_fields_for_target(target_type) or None

    payload = TenantFormCreate(
        form_id=form_id,
        tenant_id=tenant_id,
        target_type=FormTargetType(target_type),
        name="",
        description=None,
        status=FormStatus.DRAFT,
        version=0,
        fields=[],
        draft_name=seeded_name if seeded_name else None,
        draft_description=seeded_description,
        draft_fields=seeded_fields,
        draft_updated_at=(
            now if (seeded_name or seeded_description or seeded_fields) else None
        ),
        draft_updated_by=(
            actor_id if (seeded_name or seeded_description or seeded_fields) else None
        ),
        created_by_user_id=actor_id,
        last_updated_by_user_id=actor_id,
        date_created=now,
        last_updated=now,
    )

    created = await create_tenant_form(payload, preassigned_id=preassigned_id)
    _invalidate_form_caches(tenant_id=tenant_id, target_type=target_type)
    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role or "system",
            action="form.created",
            resource_type="tenant_form",
            resource_id=form_id,
            tenant_id=tenant_id,
            details={"target_type": target_type, "row_id": created.id},
            request_id=request_id,
        )
    except Exception:
        pass
    return created


async def bootstrap_draft_for_target(
    *,
    tenant_id: str,
    target_type: str,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TenantFormOut:
    """Idempotent — return the head row for (tenant, target), creating
    an empty draft shell when one does not yet exist.

    Used by ``POST /v1/tenant-forms/draft/{target_type}`` so the
    autosave loop always has a stable form_id to PATCH against.
    """
    head = await get_head_by_target(tenant_id=tenant_id, target_type=target_type)
    if head:
        return head
    return await create_form_shell(
        tenant_id=tenant_id,
        target_type=target_type,
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=request_id,
    )


async def autosave_draft(
    *,
    tenant_id: str,
    form_id: str,
    patch: TenantFormDraftPatch,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Permissive draft write.

    Updates ``draft_*`` columns only. Never bumps ``version`` and never
    touches the live ``fields``. Returns the updated form plus any
    warnings the renderer should surface inline.
    """
    head = await get_head_for_form_id(tenant_id=tenant_id, form_id=form_id)
    if not head:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)
    if head.status == FormStatus.ARCHIVED:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Cannot edit an archived form.",
            details={"form_id": form_id, "status": head.status.value},
        )

    now = int(time.time())
    update = TenantFormUpdate(
        last_updated_by_user_id=actor_id,
        last_updated=now,
    )

    if patch.name is not None:
        update.draft_name = patch.name
    if patch.description is not None:
        update.draft_description = patch.description
    if patch.fields is not None:
        # System-field lock: check-in forms must always carry the required
        # department picker, regardless of what the client sent.
        update.draft_fields = _enforce_locked_department_field(
            patch.fields, head.target_type.value
        )

    touched_draft = (
        patch.name is not None
        or patch.description is not None
        or patch.fields is not None
    )
    if touched_draft:
        update.draft_updated_at = now
        update.draft_updated_by = actor_id

    updated = await update_tenant_form(
        {"_id": ObjectId(head.id)} if head.id else {"form_id": form_id},
        update,
    )
    if not updated:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)

    # Draft writes don't change the published form, but they still bust
    # the form list cache so the builder UI sees the updated draft_*
    # state on a refresh.
    _invalidate_form_caches(tenant_id=tenant_id, target_type=head.target_type.value)

    warnings = _warnings_for_draft(patch.name, patch.fields)

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role or "system",
            action="form.draft_saved",
            resource_type="tenant_form",
            resource_id=form_id,
            tenant_id=tenant_id,
            details={
                "row_id": updated.id,
                "touched": {
                    "name": patch.name is not None,
                    "description": patch.description is not None,
                    "fields": patch.fields is not None,
                },
            },
            request_id=request_id,
        )
    except Exception:
        pass

    return {"form": updated, "warnings": warnings}


async def publish_form(
    *,
    tenant_id: str,
    form_id: str,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TenantFormOut:
    """Promote the draft to a new published version.

    Strict validation. Bumps ``version`` and supersedes any other active
    row for the same (tenant, target_type). Clears draft_* columns.
    """
    head = await get_head_for_form_id(tenant_id=tenant_id, form_id=form_id)
    if not head:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)
    if head.status == FormStatus.ARCHIVED:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Cannot publish an archived form. Restore it first.",
            details={"form_id": form_id, "status": head.status.value},
        )

    candidate_name = head.draft_name if head.draft_name is not None else head.name
    candidate_description = (
        head.draft_description
        if head.draft_description is not None
        else head.description
    )
    candidate_fields = (
        head.draft_fields if head.draft_fields is not None else head.fields
    )
    # System-field lock: re-assert the required department picker on
    # check-in forms before validation, so drafts saved by older clients
    # (or hand-crafted PATCHes) can't publish without it.
    candidate_fields = (
        _enforce_locked_department_field(
            candidate_fields or [], head.target_type.value
        )
        or []
    )

    issues = _validate_publish(candidate_name or "", candidate_fields or [])
    if issues:
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="Form is not ready to publish.",
            details={"form_id": form_id, "errors": issues},
        )

    # Snapshot for diff before we mutate.
    before = head

    # Demote any sibling active row to superseded. Safe to do before our
    # own promotion because the filter excludes our form_id.
    await supersede_existing_active(
        tenant_id=tenant_id,
        target_type=head.target_type.value,
        form_id=form_id,
    )

    now = int(time.time())
    update = TenantFormUpdate(
        name=candidate_name,
        description=candidate_description,
        status=FormStatus.ACTIVE,
        version=(head.version or 0) + 1,
        fields=candidate_fields,
        last_updated_by_user_id=actor_id,
        last_updated=now,
    )
    promoted = await update_tenant_form(
        {"_id": ObjectId(head.id)} if head.id else {"form_id": form_id},
        update,
    )
    if not promoted:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)

    promoted = await unset_draft(
        {"_id": ObjectId(promoted.id)} if promoted.id else {"form_id": form_id}
    )
    assert promoted is not None

    _invalidate_form_caches(tenant_id=tenant_id, target_type=promoted.target_type.value)

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role or "system",
            action="form.published",
            resource_type="tenant_form",
            resource_id=form_id,
            tenant_id=tenant_id,
            details={
                "row_id": promoted.id,
                "target_type": promoted.target_type.value,
                **_diff_for_audit(before, promoted),
            },
            request_id=request_id,
        )
    except Exception:
        pass
    return promoted


async def discard_draft(
    *,
    tenant_id: str,
    form_id: str,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TenantFormOut:
    """Drop the working copy back to the last published state."""
    head = await get_head_for_form_id(tenant_id=tenant_id, form_id=form_id)
    if not head:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)

    cleared = await unset_draft(
        {"_id": ObjectId(head.id)} if head.id else {"form_id": form_id}
    )
    if not cleared:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)

    _invalidate_form_caches(tenant_id=tenant_id, target_type=cleared.target_type.value)

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role or "system",
            action="form.draft_discarded",
            resource_type="tenant_form",
            resource_id=form_id,
            tenant_id=tenant_id,
            details={"row_id": cleared.id},
            request_id=request_id,
        )
    except Exception:
        pass
    return cleared


async def seed_default_fields_into_draft(
    *,
    tenant_id: str,
    form_id: str,
    force: bool = False,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TenantFormOut:
    """Load the system default fields into a form's draft.

    Idempotent in the common case: if the draft already has fields and
    ``force`` is False, the call is a no-op (returns the form unchanged).
    When ``force=True`` the existing draft fields are overwritten with
    the defaults — useful for "reset to defaults" UX. The published
    columns (``name``, ``description``, ``fields``, ``version``) are
    never touched; this is a draft-only operation.

    Existing drafts where the super_admin already started editing
    (added/removed fields) keep their work unless ``force=True`` is
    passed. A draft whose ``draft_fields`` is ``None`` or empty is
    treated as untouched and gets seeded.
    """
    head = await get_head_for_form_id(tenant_id=tenant_id, form_id=form_id)
    if not head:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)
    if head.status == FormStatus.ARCHIVED:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Cannot seed defaults into an archived form.",
            details={"form_id": form_id, "status": head.status.value},
        )

    has_draft_fields = bool(head.draft_fields)
    if has_draft_fields and not force:
        # Draft already populated — nothing to do.
        return head

    target = head.target_type.value
    defaults = default_fields_for_target(target)
    if not defaults:
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message=(
                f"No system defaults defined for target_type='{target}'. "
                "Add fields manually or extend config.default_forms."
            ),
            details={"target_type": target, "form_id": form_id},
        )

    now = int(time.time())
    update = TenantFormUpdate(
        draft_name=head.draft_name or default_name_for_target(target),
        draft_description=(
            head.draft_description
            if head.draft_description is not None
            else (default_description_for_target(target) or None)
        ),
        draft_fields=defaults,
        draft_updated_at=now,
        draft_updated_by=actor_id,
        last_updated_by_user_id=actor_id,
        last_updated=now,
    )
    updated = await update_tenant_form(
        {"_id": ObjectId(head.id)} if head.id else {"form_id": form_id},
        update,
    )
    if not updated:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)

    _invalidate_form_caches(tenant_id=tenant_id, target_type=target)

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role or "system",
            action="form.defaults_seeded",
            resource_type="tenant_form",
            resource_id=form_id,
            tenant_id=tenant_id,
            details={
                "row_id": updated.id,
                "target_type": target,
                "force": force,
                "field_count": len(defaults),
            },
            request_id=request_id,
        )
    except Exception:
        pass
    return updated


async def archive_form(
    *,
    tenant_id: str,
    form_id: str,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TenantFormOut:
    """Flip a form's head row to archived.

    Submissions captured under the previous active version still resolve;
    new submissions are rejected for this target_type until another form
    is published (or this one restored).
    """
    head = await get_head_for_form_id(tenant_id=tenant_id, form_id=form_id)
    if not head:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)

    previous_status = head.status.value
    update = TenantFormUpdate(
        status=FormStatus.ARCHIVED,
        last_updated_by_user_id=actor_id,
        last_updated=int(time.time()),
    )
    result = await update_tenant_form(
        {"_id": ObjectId(head.id)} if head.id else {"form_id": form_id},
        update,
    )
    if not result:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)

    _invalidate_form_caches(tenant_id=tenant_id, target_type=result.target_type.value)

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role or "system",
            action="form.archived",
            resource_type="tenant_form",
            resource_id=form_id,
            tenant_id=tenant_id,
            details={"row_id": result.id, "previous_status": previous_status},
            request_id=request_id,
        )
    except Exception:
        pass
    return result


async def clone_form(
    *,
    tenant_id: str,
    source_form_id: str,
    name: Optional[str] = None,
    actor_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request_id: Optional[str] = None,
) -> TenantFormOut:
    """Duplicate the source form's published shape into a new form_id
    as a fresh draft. Useful for trial redesigns.
    """
    source = await get_head_for_form_id(tenant_id=tenant_id, form_id=source_form_id)
    if not source:
        # Allow cloning archived forms too — they're not in the head set.
        source = await get_tenant_form(
            {"tenant_id": tenant_id, "form_id": source_form_id, "status": "archived"}
        )
    if not source:
        raise resource_not_found(resource="TenantForm", resource_id=source_form_id)

    base_name = name or source.name or "Form copy"
    new_name = base_name if "(copy)" in base_name.lower() else f"{base_name} (copy)"
    cloned = await create_form_shell(
        tenant_id=tenant_id,
        target_type=source.target_type.value,
        name=new_name,
        description=source.description,
        fields=list(source.fields),
        actor_id=actor_id,
        actor_role=actor_role,
        request_id=request_id,
    )

    try:
        await record_audit_event(
            actor_id=actor_id or "system",
            actor_role=actor_role or "system",
            action="form.cloned",
            resource_type="tenant_form",
            resource_id=cloned.form_id,
            tenant_id=tenant_id,
            details={
                "source_form_id": source_form_id,
                "row_id": cloned.id,
                "target_type": source.target_type.value,
            },
            request_id=request_id,
        )
    except Exception:
        pass
    return cloned


async def count_active_forms(tenant_id: str) -> int:
    return await count_tenant_forms({"tenant_id": tenant_id, "status": "active"})


async def serialize_form(form: TenantFormOut) -> Dict[str, Any]:
    return form.model_dump(mode="json", by_alias=True)
