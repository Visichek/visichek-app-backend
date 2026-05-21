"""Upload request / response schemas for the unified upload routes.

Two contracts ride on the same DTO surface:

* ``/v1/uploads/private`` — authenticated upload used for system
  flows (badge photos, ID re-scans, host/visitor avatars, anything
  invoked from a logged-in receptionist / super_admin / app admin).
  Available on every plan including Free.

* ``/v1/public/tenants/{tenant_id}/uploads`` — plan-gated public
  upload used to back ``file`` / ``image`` / ``signature`` /
  ``id_document`` fields on kiosk forms (TenantForm
  ``target_type=checkin`` or ``appointment``). Denied on plans that
  do not enable ``/v1/public/tenants/*/uploads``.

Both routes return the same shape so the kiosk client can carry the
``object_key`` straight into the matching ``tenant_form_data`` /
``bio_data`` payload on the subsequent submit.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class UploadPurpose(str, Enum):
    """What the upload is for. Drives the storage prefix + the
    audit category recorded against the document row."""

    KIOSK_FORM = "kiosk_form"
    VISITOR_PHOTO = "visitor_photo"
    ID_DOCUMENT = "id_document"
    APPOINTMENT_PHOTO = "appointment_photo"
    BRANDING = "branding"
    SYSTEM = "system"
    # Host roster image fields. Distinct from the generic buckets above so
    # the upload service can enforce image-only MIME on them (see
    # ``_IMAGE_ONLY_PURPOSES`` in services/upload_service.py) without
    # restricting kiosk ``file`` / ``id_document`` fields that take PDFs.
    HOST_PHOTO = "host_photo"
    HOST_SIGNATURE = "host_signature"


class UploadIntentRequest(BaseModel):
    """Step 1 of every client upload: declare what you're about to PUT.

    The server validates this (image-MIME guard, advisory quota pre-check),
    reserves an ``object_key``, and returns a presigned ``upload_url``. The
    declared ``size`` / ``mime_type`` are advisory — the authoritative values
    are read back from storage at confirm time.
    """

    file_name: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    size: int = Field(gt=0, description="Declared byte size (advisory pre-check)")
    purpose: UploadPurpose = UploadPurpose.SYSTEM
    field_id: Optional[str] = Field(
        default=None, description="Tenant form field_id this upload satisfies"
    )


class UploadIntentResponse(BaseModel):
    """Step 1 result. PUT the raw file bytes to ``upload_url`` using
    ``method`` and ``headers`` (S3 requires the signed Content-Type), then
    call confirm with ``object_key``."""

    object_key: str
    upload_url: str
    method: str = "PUT"
    headers: Optional[dict[str, str]] = None
    expires_in: int
    backend: str  # "s3" | "local"
    purpose: UploadPurpose
    field_id: Optional[str] = None


class UploadConfirmRequest(BaseModel):
    """Step 2: confirm the bytes landed. The server HEADs the object for the
    real size/type, enforces quota authoritatively, and finalises the record."""

    object_key: str = Field(min_length=1)


class UploadResponse(BaseModel):
    object_key: str
    download_url: str
    file_name: str
    mime_type: str
    size: int
    backend: str  # "s3" | "local"
    purpose: UploadPurpose
    tenant_id: Optional[str] = None
    field_id: Optional[str] = None  # Tenant form field this was uploaded for
    expires_in_seconds: int = Field(
        default=24 * 3600,
        description=(
            "Lifetime of ``download_url`` in seconds. For local backends "
            "the URL doesn't expire; for S3 this is the presign TTL."
        ),
    )
