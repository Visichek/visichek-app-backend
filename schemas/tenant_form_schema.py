"""Tenant form definitions used by the visitor / appointment / kiosk flows.

The model supports a draft → publish workflow so the super_admin form-builder
can autosave continuously without minting a new published version on every
keystroke.

Layout per ``form_id``:

* One **head** row exists at any time with ``status in {active, draft, archived}``.
  Drafts live on the head row in the ``draft_*`` columns until the user
  publishes (which clears them and bumps ``version``) or discards them.
* Zero or more **superseded** rows preserve older published versions so
  historical submissions remain interpretable.
"""

from __future__ import annotations

import re
import time
from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator
from bson import ObjectId

from schemas.imports import (
    FormFieldType,
    FormStatus,
    FormTargetType,
    LawfulBasis,
)


_FIELD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Allowlist used to validate FormFieldDefinition.maps_to. Keep in sync with
# the per-record mapping helper in ``services/tenant_form_service``.
ALLOWED_MAPS_TO: tuple[str, ...] = (
    "visitor.full_name",
    "visitor.phone",
    "visitor.email",
    "visitor.company",
    "purpose",
    "expected_duration_minutes",
    "host_id",
    "department_id",
)


# ─── Field-level sub-schemas ───────────────────────────────────────────


class FormFieldOption(BaseModel):
    """One option on a select / multi_select field."""

    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    archived: bool = False


class CalculatedOperand(BaseModel):
    kind: str = Field(description="field_ref | literal")
    field_id: Optional[str] = None
    value: Any = None
    transform: Optional[str] = None


class CalculatedFormula(BaseModel):
    operation: str
    operands: List[CalculatedOperand] = Field(default_factory=list)


class FormFieldDefinition(BaseModel):
    """A single field on a tenant form.

    Validation rules per type are opt-in: only the relevant subset of
    properties applies. The renderer-side validator picks the right
    subset based on ``type``.
    """

    # --- Common ---
    field_id: str = Field(min_length=1, max_length=64)
    type: FormFieldType
    label: str = Field(default="", max_length=400)
    help_text: Optional[str] = Field(default=None, max_length=600)
    placeholder: Optional[str] = Field(default=None, max_length=200)
    required: bool = False
    visible: bool = True
    order: int = 0
    maps_to: Optional[str] = None
    # System-managed lock. Locked fields cannot be removed from the form,
    # and their ``required`` / ``maps_to`` / ``type`` cannot be changed by
    # the tenant — only label / help_text / placeholder / order remain
    # editable. Enforced server-side in ``services/tenant_form_service``
    # (autosave + publish) for system fields such as the check-in
    # ``department_id`` picker.
    locked: bool = False

    # --- String validation ---
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    trim: Optional[bool] = True

    # --- Numeric validation ---
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    unit: Optional[str] = None

    # --- Temporal validation ---
    min_offset_seconds: Optional[int] = None
    max_offset_seconds: Optional[int] = None
    allow_past: Optional[bool] = True

    # --- Options ---
    options: Optional[List[FormFieldOption]] = None
    multi_min: Optional[int] = None
    multi_max: Optional[int] = None

    # --- File / image / signature ---
    max_bytes: Optional[int] = None
    allowed_mime_types: Optional[List[str]] = None
    storage_prefix: Optional[str] = None

    # --- Consent ---
    consent_text: Optional[str] = None
    lawful_basis: Optional[LawfulBasis] = None

    # --- Calculated ---
    formula: Optional[CalculatedFormula] = None

    @model_validator(mode="after")
    def _validate_field(self) -> "FormFieldDefinition":
        if not _FIELD_ID_RE.match(self.field_id):
            raise ValueError(
                "field_id must be snake_case, ≤ 64 chars, starting with a letter "
                "(matched against ^[a-z][a-z0-9_]{0,63}$)"
            )
        if self.maps_to is not None and self.maps_to not in ALLOWED_MAPS_TO:
            raise ValueError(
                f"maps_to '{self.maps_to}' is not on the allowlist: "
                f"{sorted(ALLOWED_MAPS_TO)}"
            )
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"pattern is not a valid regex: {exc}") from exc
        return self


# ─── Persistence schemas ───────────────────────────────────────────


class TenantFormBase(BaseModel):
    """Shared tenant form fields between Create / Update / Out.

    A form row is the canonical record of a single version. The
    ``form_id`` is stable across versions and links the head row to its
    superseded predecessors.
    """

    form_id: str
    tenant_id: str
    target_type: FormTargetType
    name: str = Field(default="", max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    status: FormStatus = FormStatus.DRAFT
    version: int = 0
    fields: List[FormFieldDefinition] = Field(default_factory=list)

    # --- Draft (mutable working copy on the head row) ---
    draft_name: Optional[str] = None
    draft_description: Optional[str] = None
    draft_fields: Optional[List[FormFieldDefinition]] = None
    draft_updated_at: Optional[int] = None
    draft_updated_by: Optional[str] = None

    created_by_user_id: Optional[str] = None
    last_updated_by_user_id: Optional[str] = None


class TenantFormCreate(TenantFormBase):
    """Internal create schema — used by the service layer."""

    date_created: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantFormDraftPatch(BaseModel):
    """Public PATCH payload — autosave target.

    All fields are optional and write only to the draft_* columns.
    Validation is permissive — empty labels and empty field lists are
    allowed because the user is mid-typing.
    """

    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    fields: Optional[List[FormFieldDefinition]] = None


class TenantFormCreateRequest(BaseModel):
    """Public POST payload for creating a brand-new form."""

    target_type: FormTargetType
    name: str = Field(default="", max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    fields: Optional[List[FormFieldDefinition]] = None


class TenantFormUpdate(BaseModel):
    """Partial update used by the repository.

    Each field is independently optional so callers can target a single
    column without overwriting siblings.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[FormStatus] = None
    version: Optional[int] = None
    fields: Optional[List[FormFieldDefinition]] = None
    draft_name: Optional[str] = None
    draft_description: Optional[str] = None
    draft_fields: Optional[List[FormFieldDefinition]] = None
    draft_updated_at: Optional[int] = None
    draft_updated_by: Optional[str] = None
    last_updated_by_user_id: Optional[str] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class TenantFormOut(TenantFormBase):
    """Response schema returned by tenant-form routes."""

    id: Optional[str] = Field(default=None, alias="_id")
    date_created: Optional[int] = None
    last_updated: Optional[int] = None
    has_unpublished_changes: bool = False

    @model_validator(mode="before")
    @classmethod
    def _convert_objectid(cls, values: Any) -> Any:
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    @model_validator(mode="after")
    def _compute_has_unpublished_changes(self) -> "TenantFormOut":
        if (
            self.draft_fields is not None
            or self.draft_name is not None
            or self.draft_description is not None
        ):
            self.has_unpublished_changes = True
        return self

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class TenantFormPublicOut(BaseModel):
    """Kiosk / public-facing form payload — only the published state.

    Strips draft_*, internal user ids and timestamps so callers without
    a super_admin token never see in-flight edits.
    """

    form_id: str
    tenant_id: str
    target_type: FormTargetType
    name: str
    description: Optional[str] = None
    version: int
    fields: List[FormFieldDefinition] = Field(default_factory=list)
