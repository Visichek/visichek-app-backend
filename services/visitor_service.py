from __future__ import annotations

import logging
import re
from typing import Any, Optional

from core.errors import AppException, ErrorCode, resource_not_found
from repositories.visitor_repo import (
    create_visitor,
    find_visitor_by_email_or_phone,
    get_visitor,
    update_visitor,
)
from schemas.summary_schema import VisitorBriefSummary
from schemas.visitor_schema import (
    VisitorCreate,
    VisitorEditRequest,
    VisitorLookupResponse,
    VisitorOut,
    VisitorUpdate,
)

logger = logging.getLogger(__name__)

# Mirrors the frontend's permissive phone pattern (edit-visitor-modal).
_PHONE_RE = re.compile(r"^\+?[0-9\s\-()]{7,}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EDITABLE_FIELDS = ("full_name", "email", "phone", "company")


async def lookup_visitor(
    tenant_id: str, email: Optional[str] = None, phone: Optional[str] = None
) -> VisitorLookupResponse:
    """Lookup a visitor by email and/or phone.

    At least one of email or phone is required.
    """
    if not email and not phone:
        return VisitorLookupResponse(found=False, visitor=None)

    visitor = await find_visitor_by_email_or_phone(
        tenant_id=tenant_id, email=email, phone=phone
    )

    if visitor:
        return VisitorLookupResponse(found=True, visitor=visitor)
    return VisitorLookupResponse(found=False, visitor=None)


async def upsert_visitor_from_checkin(
    tenant_id: str,
    bio_data: dict,
    id_extraction_id: Optional[str] = None,
    visitor_id: Optional[str] = None,
) -> VisitorOut:
    """Upsert a visitor for check-in.

    If visitor_id is provided, updates the existing visitor.
    If not, creates a new one.

    Verified status: new visitors start unverified unless id_extraction succeeded.
    Never downgrade verified for returning visitors.
    """
    # Determine verified status based on id_extraction
    verified = False
    id_document_id = None
    if id_extraction_id:
        try:
            from repositories.id_extraction_repo import get_id_extraction

            extraction = await get_id_extraction({"_id": id_extraction_id})
            if extraction and extraction.verified:
                verified = True
                id_document_id = extraction.document_id
        except Exception:
            pass  # Gracefully skip id extraction lookup

    if visitor_id:
        # Update existing visitor
        visitor = await get_visitor({"_id": visitor_id, "tenant_id": tenant_id})
        if not visitor:
            raise ValueError(f"Visitor {visitor_id} not found in tenant {tenant_id}")

        # Never downgrade verified status
        if visitor.verified:
            verified = True

        update_data = VisitorUpdate(
            bio_data=bio_data,
            verified=verified,
        )
        if id_document_id:
            update_data.id_document_id = id_document_id

        return await update_visitor(visitor_id, update_data)
    else:
        # Create new visitor from bio_data
        # Extract name, email, phone from bio_data
        full_name = bio_data.get("full_name", "Unknown")
        email = bio_data.get("email")
        phone = bio_data.get("phone")

        create_data = VisitorCreate(
            tenant_id=tenant_id,
            full_name=full_name,
            email=email,
            phone=phone,
            bio_data=bio_data,
            verified=verified,
            id_document_id=id_document_id,
        )
        return await create_visitor(create_data)


def _visitor_company(visitor: VisitorOut) -> Optional[str]:
    bio = visitor.bio_data or {}
    return bio.get("company") or bio.get("organization")


def _validate_visitor_edit(payload: VisitorEditRequest) -> dict[str, Any]:
    """Validate a partial visitor edit and return the normalized change set.

    Honors PATCH semantics: only keys PRESENT in the body are considered; an
    explicit ``null`` on ``email`` / ``company`` clears the value. Raises 422
    ``VALIDATION_ERROR`` with field-mapped ``details`` so the modal can pin the
    error inline. The returned dict maps each changed editable field to its new
    value (``None`` meaning "clear")."""
    present = payload.model_fields_set & set(_EDITABLE_FIELDS)
    if not present:
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="At least one editable field is required.",
            details={
                "_body": "Provide at least one of: full_name, email, phone, company."
            },
        )

    changes: dict[str, Any] = {}

    if "full_name" in present:
        name = (payload.full_name or "").strip()
        if not name:
            raise AppException(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="Full name cannot be empty.",
                details={"full_name": "Full name is required and cannot be empty."},
            )
        changes["full_name"] = name

    if "email" in present:
        if payload.email is None:
            changes["email"] = None  # explicit clear
        else:
            email = payload.email.strip()
            if not _EMAIL_RE.match(email):
                raise AppException(
                    status_code=422,
                    code=ErrorCode.VALIDATION_ERROR,
                    message="Email address is invalid.",
                    details={"email": "Enter a valid email address."},
                )
            changes["email"] = email

    if "phone" in present:
        phone = (payload.phone or "").strip()
        if not _PHONE_RE.match(phone):
            raise AppException(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="Phone number is invalid.",
                details={"phone": "Enter a valid phone number."},
            )
        changes["phone"] = phone

    if "company" in present:
        if payload.company is None:
            changes["company"] = None  # explicit clear
        else:
            changes["company"] = payload.company.strip()

    return changes


async def _sync_visitor_profile_identity(
    *, tenant_id: str, before: VisitorOut, changes: dict[str, Any]
) -> None:
    """Best-effort propagation of an identity edit to the linked VisitorProfile.

    Matches the profile by the visitor's pre-edit phone then email (the keys
    the profile is indexed on) and patches the changed identity fields so the
    visitor-profile reads / awaiting-checkout feed reflect the correction on
    next fetch. Fire-and-forget — a missing profile or a flaky read must never
    fail the edit."""
    from bson import ObjectId

    try:
        from repositories.visitor_profile_repo import (
            get_visitor_profile_by_email,
            get_visitor_profile_by_phone,
            update_visitor_profile,
        )
        from schemas.visitor_profile_schema import VisitorProfileUpdate

        profile = None
        if before.phone:
            profile = await get_visitor_profile_by_phone(
                tenant_id=tenant_id, phone=before.phone
            )
        if profile is None and before.email:
            profile = await get_visitor_profile_by_email(
                tenant_id=tenant_id, email=before.email
            )
        if profile is None or not profile.id or not ObjectId.is_valid(profile.id):
            return

        update_fields: dict[str, Any] = {}
        if "full_name" in changes:
            update_fields["full_name"] = changes["full_name"]
        if "phone" in changes:
            update_fields["phone"] = changes["phone"]
        if "email" in changes:
            update_fields["email_address"] = changes["email"]
        if "company" in changes:
            update_fields["company"] = changes["company"]
        if not update_fields:
            return

        await update_visitor_profile(
            {"_id": ObjectId(profile.id), "tenant_id": tenant_id},
            VisitorProfileUpdate(**update_fields),
        )
    except Exception:
        logger.warning(
            "edit_visitor_details: visitor-profile sync failed for visitor %s",
            before.id,
            exc_info=True,
        )


async def edit_visitor_details(
    *,
    visitor_id: str,
    tenant_id: str,
    payload: VisitorEditRequest,
    actor_id: str,
    actor_role: str,
    request_id: Optional[str] = None,
) -> VisitorBriefSummary:
    """Staff edit of a visitor's identity fields (name / email / phone / company).

    Tenant-scoped: a lookup miss (unknown id OR an id in another tenant) is a
    404 so ids don't leak. Applies a partial update, propagates the corrected
    identity to the linked visitor profile, audits the before/after diff, and
    returns the canonical visitor snapshot (the same shape embedded as
    ``checkin.visitor``)."""
    from bson import ObjectId

    if not ObjectId.is_valid(visitor_id):
        raise resource_not_found(resource="Visitor", resource_id=visitor_id)

    before = await get_visitor({"_id": visitor_id, "tenant_id": tenant_id})
    if before is None:
        raise resource_not_found(resource="Visitor", resource_id=visitor_id)

    changes = _validate_visitor_edit(payload)

    update = VisitorUpdate()
    if "full_name" in changes:
        update.full_name = changes["full_name"]
    if "email" in changes:
        update.email = changes["email"]
    if "phone" in changes:
        update.phone = changes["phone"]
    if "company" in changes:
        # ``company`` lives inside bio_data (no first-class column). Copy the
        # existing dict so the rest of the visitor's bio is preserved.
        new_bio = dict(before.bio_data or {})
        new_bio["company"] = changes["company"]
        update.bio_data = new_bio

    updated = await update_visitor(visitor_id, update)

    # Best-effort propagation to the visitor-profile directory.
    await _sync_visitor_profile_identity(
        tenant_id=tenant_id, before=before, changes=changes
    )

    # Audit — identity / PII mutation, reportable under NDPA. Record the
    # before/after diff so the trail answers "what changed", not just "that
    # something changed".
    try:
        from services.audit_service import record_audit_event

        before_company = _visitor_company(before)
        field_diff: dict[str, Any] = {}
        if "full_name" in changes:
            field_diff["full_name"] = {
                "before": before.full_name,
                "after": changes["full_name"],
            }
        if "email" in changes:
            field_diff["email"] = {"before": before.email, "after": changes["email"]}
        if "phone" in changes:
            field_diff["phone"] = {"before": before.phone, "after": changes["phone"]}
        if "company" in changes:
            field_diff["company"] = {
                "before": before_company,
                "after": changes["company"],
            }

        await record_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            action="visitor.updated",
            resource_type="visitor",
            resource_id=visitor_id,
            tenant_id=tenant_id,
            details={"changes": field_diff},
            request_id=request_id,
        )
    except Exception:
        logger.warning(
            "edit_visitor_details: audit record failed for visitor %s",
            visitor_id,
            exc_info=True,
        )

    try:
        from services.dashboard_cache_service import invalidate_tenant_dashboard_cache

        invalidate_tenant_dashboard_cache(tenant_id)
    except Exception:
        pass

    from services.checkin_service import _visitor_to_brief

    return _visitor_to_brief(updated)
