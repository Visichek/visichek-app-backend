from __future__ import annotations

import json
from typing import Union

from schemas.imports import *
from pydantic import Field


# Validation limits (kept small + boring on purpose — see CLAUDE.md and the
# planning doc).  The frontend is the source of truth for which fields exist;
# the backend only enforces size/abuse caps.
MAX_PAYLOAD_KEYS = 50
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_STRING_BYTES = 4 * 1024
MAX_LABEL_LENGTH = 200
MAX_FORM_VERSION_LENGTH = 64

# Best-effort key lists for the indexed columns.  Edit freely as field names
# evolve — old rows still work because their original values live in
# ``payload``.
EMAIL_KEYS: tuple[str, ...] = ("work_email", "email", "contact_email")
NAME_KEYS: tuple[str, ...] = ("full_name", "name", "contact_name")
ORGANIZATION_KEYS: tuple[str, ...] = (
    "organization_name",
    "company",
    "company_name",
    "organisation_name",
)


PayloadValue = Union[str, int, float, bool, list]


# --- Public submission request (the body POST'd from the marketing site) ---


class OnboardingSubmissionRequest(BaseModel):
    """Schema-on-read submission body.

    Storage is verbatim; this validator only enforces size/abuse limits so the
    frontend can change fields, labels, and order unilaterally without backend
    coordination.
    """

    form_version: str = Field(..., min_length=1, max_length=MAX_FORM_VERSION_LENGTH)
    payload: dict[str, Any]
    field_labels: dict[str, str]
    field_order: List[str]
    turnstile_token: str = Field(..., min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _enforce_shape(self) -> "OnboardingSubmissionRequest":
        if len(self.payload) > MAX_PAYLOAD_KEYS:
            raise ValueError(f"payload has more than {MAX_PAYLOAD_KEYS} keys")

        for key, value in self.payload.items():
            if not isinstance(key, str) or not key:
                raise ValueError("payload keys must be non-empty strings")
            _validate_payload_value(key, value)

        try:
            serialized_size = len(json.dumps(self.payload).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"payload is not JSON-serializable: {exc}")
        if serialized_size > MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"payload exceeds {MAX_PAYLOAD_BYTES} bytes (got {serialized_size})"
            )

        payload_keys = set(self.payload.keys())
        label_keys = set(self.field_labels.keys())
        if payload_keys != label_keys:
            missing = payload_keys - label_keys
            extra = label_keys - payload_keys
            raise ValueError(
                "field_labels keys must equal payload keys "
                f"(missing={sorted(missing)}, extra={sorted(extra)})"
            )
        for key, label in self.field_labels.items():
            if not isinstance(label, str):
                raise ValueError(f"field_labels[{key!r}] must be a string")
            if len(label) > MAX_LABEL_LENGTH:
                raise ValueError(
                    f"field_labels[{key!r}] exceeds {MAX_LABEL_LENGTH} chars"
                )

        if set(self.field_order) != payload_keys:
            raise ValueError("field_order must contain exactly the payload keys")
        if len(self.field_order) != len(set(self.field_order)):
            raise ValueError("field_order contains duplicate keys")

        return self


def _validate_payload_value(key: str, value: Any) -> None:
    """Allow str/int/float/bool or a list of those.  No nested objects."""
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise ValueError(f"payload[{key!r}] string exceeds {MAX_STRING_BYTES} bytes")
        return
    if isinstance(value, bool):  # bool is a subclass of int — check first
        return
    if isinstance(value, (int, float)):
        return
    if isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, str):
                if len(item.encode("utf-8")) > MAX_STRING_BYTES:
                    raise ValueError(
                        f"payload[{key!r}][{idx}] string exceeds {MAX_STRING_BYTES} bytes"
                    )
                continue
            if isinstance(item, bool):
                continue
            if isinstance(item, (int, float)):
                continue
            raise ValueError(
                f"payload[{key!r}][{idx}] must be str/int/float/bool, got {type(item).__name__}"
            )
        return
    raise ValueError(
        f"payload[{key!r}] must be str/int/float/bool or a list of those, "
        f"got {type(value).__name__}"
    )


# --- Internal create + read schemas ---


class OnboardingSubmissionCreate(BaseModel):
    """Internal — built by the service layer after Turnstile verification."""

    form_version: str
    payload: dict[str, Any]
    field_labels: dict[str, str]
    field_order: List[str]

    # Best-effort extracted columns for search / dedup.  None when the form
    # didn't include any of the configured aliases.
    email: Optional[str] = None
    full_name: Optional[str] = None
    organization_name: Optional[str] = None

    status: OnboardingStatus = OnboardingStatus.NEW
    turnstile_verified: bool = False
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None

    # Set when the admin partial-accepts and the tenant still owes data.
    tenant_id: Optional[str] = None
    super_admin_user_id: Optional[str] = None
    pending_field_keys: List[str] = Field(default_factory=list)
    pending_field_labels: dict[str, str] = Field(default_factory=dict)
    review_notes: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[int] = None

    submitted_at: int = Field(default_factory=lambda: int(time.time()))
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class OnboardingSubmissionUpdate(BaseModel):
    """Partial update — used by accept / reject / complete flows."""

    status: Optional[OnboardingStatus] = None
    tenant_id: Optional[str] = None
    super_admin_user_id: Optional[str] = None
    pending_field_keys: Optional[List[str]] = None
    pending_field_labels: Optional[dict[str, str]] = None
    review_notes: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[int] = None
    payload: Optional[dict[str, Any]] = None
    field_labels: Optional[dict[str, str]] = None
    field_order: Optional[List[str]] = None
    last_updated: int = Field(default_factory=lambda: int(time.time()))


class OnboardingSubmissionOut(BaseModel):
    """Response schema for admin views and self-completion lookups."""

    id: Optional[str] = Field(default=None, alias="_id")
    form_version: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    field_labels: dict[str, str] = Field(default_factory=dict)
    field_order: List[str] = Field(default_factory=list)

    email: Optional[str] = None
    full_name: Optional[str] = None
    organization_name: Optional[str] = None

    status: OnboardingStatus = OnboardingStatus.NEW
    turnstile_verified: bool = False
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None

    tenant_id: Optional[str] = None
    super_admin_user_id: Optional[str] = None
    pending_field_keys: List[str] = Field(default_factory=list)
    pending_field_labels: dict[str, str] = Field(default_factory=dict)
    review_notes: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[int] = None

    submitted_at: Optional[int] = None
    last_updated: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def convert_objectid(cls, values):
        if isinstance(values, dict) and "_id" in values:
            if isinstance(values["_id"], ObjectId):
                values["_id"] = str(values["_id"])
        return values

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


# --- Admin action bodies ---


class OnboardingAcceptRequest(BaseModel):
    """Application-admin body for full acceptance.

    Only ``admin_password`` is required; everything else is best-effort
    extracted from the submission payload but can be overridden here.
    """

    admin_password: str = Field(..., min_length=1, max_length=128)
    company_name: Optional[str] = None
    admin_full_name: Optional[str] = None
    admin_email: Optional[EmailStr] = None
    review_notes: Optional[str] = Field(default=None, max_length=4000)


class OnboardingPartialAcceptRequest(OnboardingAcceptRequest):
    """Same shape as accept, plus the field keys the tenant still owes.

    ``pending_field_keys`` lists payload keys whose values were missing or
    unsatisfactory; the tenant fills them in via the self-completion endpoint.
    """

    pending_field_keys: List[str] = Field(default_factory=list)


class OnboardingRejectRequest(BaseModel):
    review_notes: str = Field(..., min_length=1, max_length=4000)


class OnboardingCompleteRequest(BaseModel):
    """Tenant self-completion body — fills in the admin-flagged missing keys.

    Values follow the same per-value rules as the original submission to keep
    the storage contract consistent.
    """

    values: dict[str, Any]

    @model_validator(mode="after")
    def _enforce_value_shape(self) -> "OnboardingCompleteRequest":
        if not self.values:
            raise ValueError("values must not be empty")
        if len(self.values) > MAX_PAYLOAD_KEYS:
            raise ValueError(f"values has more than {MAX_PAYLOAD_KEYS} keys")
        for key, value in self.values.items():
            if not isinstance(key, str) or not key:
                raise ValueError("values keys must be non-empty strings")
            _validate_payload_value(key, value)
        return self


class OnboardingPendingFieldsOut(BaseModel):
    """What the tenant sees on the self-completion screen."""

    submission_id: str
    status: OnboardingStatus
    tenant_id: Optional[str] = None
    pending_field_keys: List[str] = Field(default_factory=list)
    pending_field_labels: dict[str, str] = Field(default_factory=dict)
    review_notes: Optional[str] = None


class OnboardingAcceptOut(BaseModel):
    """Returned to admins after accept / partial-accept."""

    submission_id: str
    status: OnboardingStatus
    tenant_id: str
    super_admin_user_id: str
    pending_field_keys: List[str] = Field(default_factory=list)


class MarketingOptInEmailsOut(BaseModel):
    """Deduplicated list of normalized emails for marketing-opt-in submissions."""

    emails: List[str] = Field(default_factory=list)
    total: int = 0
