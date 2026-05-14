"""Tenant form builder routes.

Two routers in one module:

* ``router`` — authenticated paths under ``/v1/tenant-forms`` for the
  super_admin form-builder UI.
* ``public_router`` — unauthenticated kiosk paths under
  ``/v1/public/tenant-forms`` that expose only the published shape.

The autosave PATCH, publish, archive, discard-draft and clone flows
all run synchronously: the super_admin needs the form back in the
response (with validation errors on publish) so we don't enqueue. Cache
invalidation is handled by the service layer.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query, Request, status

from core.errors import resource_not_found
from core.queue.precompute import PrecomputeScope, get_or_compute
from core.response_envelope import document_response
from schemas.imports import FormTargetType
from schemas.tenant_form_schema import (
    TenantFormCreateRequest,
    TenantFormDraftPatch,
    TenantFormOut,
)
from security.auth import verify_any_system_user_token, verify_super_admin_token
from security.principal import AuthPrincipal
from services.tenant_form_service import (
    archive_form,
    autosave_draft,
    bootstrap_draft_for_target,
    clone_form,
    create_form_shell,
    discard_draft,
    list_forms_for_tenant,
    publish_form,
    retrieve_active_by_target,
    retrieve_form_by_id,
    retrieve_head_by_target,
    retrieve_public_active_by_target,
)
from services.tenant_form_writer import (
    RESOURCE_LIST,
    resource_active,
    resource_public,
)


router = APIRouter(prefix="/tenant-forms", tags=["Tenant Forms"])
public_router = APIRouter(
    prefix="/public/tenant-forms", tags=["Tenant Forms (Public)"]
)


def _validate_target(target_type: str) -> str:
    try:
        return FormTargetType(target_type).value
    except ValueError as exc:
        raise resource_not_found(
            resource="FormTargetType", resource_id=target_type
        ) from exc


def _request_id(request: Request) -> Optional[str]:
    return getattr(request.state, "request_id", None)


# ─── Authenticated reads ───────────────────────────────────────────


@router.get("")
@document_response(
    message="Tenant forms listed",
    description=(
        "Lists form heads (draft / active / archived) for the calling "
        "tenant. Superseded rows are excluded by default; pass "
        "``status=superseded`` to inspect history."
    ),
    summary="List tenant forms",
)
async def list_tenant_forms_endpoint(
    target: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    start: int = Query(default=0, ge=0),
    stop: int = Query(default=100, ge=1, le=500),
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    if target is None and status_filter is None and start == 0 and stop == 100:
        # Default unfiltered first page is served from the precompute cache.
        return await get_or_compute(
            scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
            resource=RESOURCE_LIST,
            ttl=60,
            loader=lambda: _load_form_list(tenant_id),
        )
    forms = await list_forms_for_tenant(
        tenant_id=tenant_id,
        target_type=target,
        status=status_filter,
        start=start,
        stop=stop,
    )
    return [f.model_dump(mode="json", by_alias=True) for f in forms]


async def _load_form_list(tenant_id: str) -> Any:
    forms = await list_forms_for_tenant(tenant_id, start=0, stop=200)
    return [f.model_dump(mode="json", by_alias=True) for f in forms]


@router.get("/by-target/{target_type}")
@document_response(
    message="Active form fetched",
    description=(
        "Returns the head row (active or draft) for a tenant target. "
        "Includes both published state and any in-flight draft_* "
        "columns so the form-builder can render the working copy. "
        "Receptionist / kiosk render paths should call this for the "
        "currently published shape."
    ),
    summary="Get tenant form by target_type",
    response_codes={404: "No form configured for this target_type"},
)
async def get_form_by_target_endpoint(
    target_type: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    target = _validate_target(target_type)
    head = await retrieve_head_by_target(
        tenant_id=tenant_id, target_type=target
    )
    if head is None:
        raise resource_not_found(resource="TenantForm", resource_id=target)
    return head.model_dump(mode="json", by_alias=True)


@router.get("/{form_id}")
@document_response(
    message="Form fetched",
    response_codes={404: "Form not found"},
)
async def get_form_by_id_endpoint(
    form_id: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    form = await retrieve_form_by_id(tenant_id=tenant_id, form_id=form_id)
    if form is None:
        raise resource_not_found(resource="TenantForm", resource_id=form_id)
    return form.model_dump(mode="json", by_alias=True)


# ─── Authenticated mutations (synchronous) ─────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Tenant form created",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Create a brand-new form for a tenant. The form starts in "
        "``status=draft`` and any supplied fields land in the draft_* "
        "columns — call ``POST /tenant-forms/{id}/publish`` to make it "
        "live."
    ),
    summary="Create tenant form",
    response_codes={
        401: "Unauthorized",
        403: "Forbidden - must be super admin",
    },
)
async def create_form_endpoint(
    payload: TenantFormCreateRequest,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    form = await create_form_shell(
        tenant_id=tenant_id,
        target_type=payload.target_type.value,
        name=payload.name,
        description=payload.description,
        fields=payload.fields,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=_request_id(request),
    )
    return form.model_dump(mode="json", by_alias=True)


@router.post("/draft/{target_type}", status_code=status.HTTP_200_OK)
@document_response(
    message="Form draft ready",
    description=(
        "Idempotent — returns the existing form head for "
        "``target_type``, or creates an empty draft shell when none "
        "exists. Use this as the autosave bootstrap so the first PATCH "
        "always has a stable form_id to target."
    ),
    summary="Bootstrap form draft (idempotent)",
)
async def bootstrap_draft_endpoint(
    target_type: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    target = _validate_target(target_type)
    form = await bootstrap_draft_for_target(
        tenant_id=tenant_id,
        target_type=target,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=_request_id(request),
    )
    return form.model_dump(mode="json", by_alias=True)


@router.patch("/{form_id}")
@document_response(
    message="Draft saved",
    description=(
        "Autosave target. Writes to draft_* columns ONLY — does NOT "
        "bump the published version or touch live fields. Validation is "
        "permissive (empty labels / zero fields are allowed). Returns "
        "the updated row with ``hasUnpublishedChanges`` and inline "
        "``warnings`` in ``meta``."
    ),
    summary="Autosave form draft",
    include_meta=True,
    response_codes={
        404: "Form not found",
        409: "Form is archived",
    },
)
async def autosave_draft_endpoint(
    form_id: str,
    patch: TenantFormDraftPatch,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    result = await autosave_draft(
        tenant_id=tenant_id,
        form_id=form_id,
        patch=patch,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=_request_id(request),
    )
    form: TenantFormOut = result["form"]
    warnings = result.get("warnings") or []
    return {
        "items": form.model_dump(mode="json", by_alias=True),
        "warnings": warnings,
    }


@router.post("/{form_id}/publish")
@document_response(
    message="Form published",
    description=(
        "Promote the draft to a new published version. Strict "
        "validation: fields must have labels, options, consent_text "
        "etc. On failure returns 422 with per-field error details so "
        "the builder can pin question cards inline."
    ),
    summary="Publish form draft",
    response_codes={
        404: "Form not found",
        409: "Form is archived",
        422: "Form is not ready to publish",
    },
)
async def publish_form_endpoint(
    form_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    form = await publish_form(
        tenant_id=tenant_id,
        form_id=form_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=_request_id(request),
    )
    return form.model_dump(mode="json", by_alias=True)


@router.post("/{form_id}/discard-draft")
@document_response(
    message="Draft discarded",
    description=(
        "Drop the working copy back to the last published state. "
        "Idempotent — calling this on a form with no draft is a no-op."
    ),
    summary="Discard form draft",
    response_codes={404: "Form not found"},
)
async def discard_draft_endpoint(
    form_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    form = await discard_draft(
        tenant_id=tenant_id,
        form_id=form_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=_request_id(request),
    )
    return form.model_dump(mode="json", by_alias=True)


@router.post("/{form_id}/archive")
@document_response(
    message="Form archived",
    description=(
        "Retire a form. Existing submissions still resolve against the "
        "form's last published version, but new submissions for this "
        "target_type are rejected until a fresh form is published."
    ),
    summary="Archive form",
    response_codes={404: "Form not found"},
)
async def archive_form_endpoint(
    form_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    form = await archive_form(
        tenant_id=tenant_id,
        form_id=form_id,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=_request_id(request),
    )
    return form.model_dump(mode="json", by_alias=True)


@router.post("/{form_id}/clone", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Form cloned",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Duplicate the source form's published shape into a fresh "
        "draft. The new form gets a new form_id, status=draft, version "
        "0; the source row is left untouched."
    ),
    summary="Clone form",
    response_codes={404: "Source form not found"},
)
async def clone_form_endpoint(
    form_id: str,
    request: Request,
    principal: AuthPrincipal = Depends(verify_super_admin_token),
    payload: Optional[dict] = Body(default=None),
) -> Any:
    tenant_id = principal.tenant_id or ""
    new_name = None
    if isinstance(payload, dict):
        candidate = payload.get("name")
        if isinstance(candidate, str):
            new_name = candidate
    form = await clone_form(
        tenant_id=tenant_id,
        source_form_id=form_id,
        name=new_name,
        actor_id=principal.user_id,
        actor_role=principal.role,
        request_id=_request_id(request),
    )
    return form.model_dump(mode="json", by_alias=True)


# ─── Authenticated kiosk read (non-super_admin tenant users) ───────


@router.get("/active/{target_type}")
@document_response(
    message="Active form fetched",
    description=(
        "Returns the published form for a target_type — never the "
        "draft. Receptionist / kiosk / staged-visit render paths "
        "should call this. Served from the per-tenant precompute "
        "cache."
    ),
    summary="Get active tenant form (any tenant role)",
    response_codes={404: "No active form for this target_type"},
)
async def get_active_form_endpoint(
    target_type: str,
    principal: AuthPrincipal = Depends(verify_any_system_user_token),
) -> Any:
    tenant_id = principal.tenant_id or ""
    target = _validate_target(target_type)

    payload = await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource=resource_active(target),
        ttl=60,
        loader=lambda: _load_active_form(tenant_id, target),
    )
    if payload is None:
        raise resource_not_found(resource="TenantForm", resource_id=target)
    return payload


async def _load_active_form(tenant_id: str, target_type: str) -> Any:
    form = await retrieve_active_by_target(
        tenant_id=tenant_id, target_type=target_type
    )
    if form is None:
        return None
    return form.model_dump(mode="json", by_alias=True)


# ─── Public (kiosk / unauthenticated) ─────────────────────────────


@public_router.get("/by-target/{tenant_id}/{target_type}")
@document_response(
    message="Active form fetched (public)",
    description=(
        "Public endpoint — no auth required. Returns the published "
        "form for a tenant + target_type combination so the kiosk QR "
        "landing page can render check-in fields without a login. "
        "Served from the per-tenant precompute cache; never exposes "
        "draft_* columns."
    ),
    summary="Get active tenant form (public)",
    response_codes={404: "No active form configured"},
)
async def public_active_form_endpoint(
    tenant_id: str, target_type: str
) -> Any:
    target = _validate_target(target_type)
    payload = await get_or_compute(
        scope_key=f"{PrecomputeScope.TENANT.value}:{tenant_id}",
        resource=resource_public(target),
        ttl=60,
        loader=lambda: _load_public_form(tenant_id, target),
    )
    if payload is None:
        raise resource_not_found(resource="TenantForm", resource_id=target)
    return payload


async def _load_public_form(tenant_id: str, target_type: str) -> Any:
    form = await retrieve_public_active_by_target(
        tenant_id=tenant_id, target_type=target_type
    )
    if form is None:
        return None
    return form.model_dump(mode="json", by_alias=True)
