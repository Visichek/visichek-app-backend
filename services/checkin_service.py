from __future__ import annotations

import secrets
import time
from typing import Any, Optional

from core.errors import AppException, ErrorCode, resource_not_found
from repositories.checkin_repo import (
    create_checkin,
    get_active_pending_for_visitor,
    get_checkin,
    get_checkins,
    update_checkin,
    count_checkins,
)
from repositories.badge_repo import create_badge
from repositories.checkin_config_repo import get_checkin_config
from schemas.badge_schema import BadgeCreate, BadgePayload
from schemas.checkin_schema import (
    CheckinCreate,
    CheckinConfirmRequest,
    CheckinOut,
    CheckinPurpose,
    CheckinState,
    CheckinSubmitRequest,
    CheckinUpdate,
)
from schemas.imports import IDType


async def _collect_returning_visitor_fallback(
    *,
    tenant_id: str,
    visitor: Any,
    email: Optional[str],
    phone: Optional[str],
) -> dict:
    """Build a ``merged_bio_data`` fallback for a returning visitor.

    Looks at the resolved ``visitor`` record and the matching ``VisitorProfile``
    (if one exists) and returns a dict of fields that the kiosk didn't re-send
    but that we already know. The caller merges the submitted bio_data over
    this, so submitted values always win. This keeps the "welcome back, just
    fill purpose" UX viable even when the tenant's config requires fields like
    ``full_name`` or ``company``.
    """
    fallback: dict[str, Any] = {}

    if visitor is not None:
        if visitor.bio_data:
            fallback.update({k: v for k, v in visitor.bio_data.items() if v})
        if visitor.full_name and visitor.full_name != "Unknown":
            fallback["full_name"] = visitor.full_name

    from repositories.visitor_profile_repo import (
        get_visitor_profile_by_email,
        get_visitor_profile_by_phone,
    )

    profile = None
    if phone:
        profile = await get_visitor_profile_by_phone(
            tenant_id=tenant_id, phone=phone
        )
    if profile is None and email:
        profile = await get_visitor_profile_by_email(
            tenant_id=tenant_id, email=email
        )

    if profile is not None:
        if profile.full_name and "full_name" not in fallback:
            fallback["full_name"] = profile.full_name
        if profile.company and "company" not in fallback:
            fallback["company"] = profile.company
        if profile.id_type and "id_type" not in fallback:
            fallback["id_type"] = profile.id_type
        if profile.id_number and "id_number" not in fallback:
            fallback["id_number"] = profile.id_number

    return fallback


async def _upsert_visitor_profile_from_submit(
    *,
    tenant_id: str,
    email: Optional[str],
    phone: Optional[str],
    full_name: str,
    company: Optional[str],
    portrait_url: Optional[str],
    verified: bool,
    id_type: Optional[str],
) -> None:
    """Upsert a VisitorProfile row tied to the submitting visitor.

    The profile is the authoritative record of "has this person visited us
    before" for the public prefill lookup. Keyed on phone first, then email —
    whichever the visitor supplied is used to find an existing profile; a new
    one is created if neither matches. Visit count is incremented on every
    successful submit so the profile reflects true visit frequency.

    Fire-and-forget at the caller — any exception here is logged but never
    blocks the check-in.
    """
    from bson import ObjectId

    from repositories.visitor_profile_repo import (
        get_visitor_profile_by_email,
        get_visitor_profile_by_phone,
        increment_visitor_profile_visits,
        update_visitor_profile,
    )
    from schemas.visitor_profile_schema import VisitorProfileUpdate
    from services.visitor_profile_service import get_or_create_visitor_profile

    profile = None
    if phone:
        profile = await get_visitor_profile_by_phone(
            tenant_id=tenant_id, phone=phone
        )
    if profile is None and email:
        profile = await get_visitor_profile_by_email(
            tenant_id=tenant_id, email=email
        )

    if profile is None:
        profile = await get_or_create_visitor_profile(
            tenant_id=tenant_id,
            phone=phone,
            email=email,
            full_name=full_name,
            company=company,
            photo_object_key=portrait_url,
        )

    if profile is None or not profile.id or not ObjectId.is_valid(profile.id):
        return

    # Merge any missing fields into the existing profile so future submissions
    # can lookup via either channel. Never overwrite an existing value with
    # None — we only fill in gaps.
    update_fields: dict[str, Any] = {}
    if phone and not profile.phone:
        update_fields["phone"] = phone
    if email and not profile.email_address:
        update_fields["email_address"] = email
    if full_name and full_name != "Unknown" and profile.full_name != full_name:
        update_fields["full_name"] = full_name
    if company and not profile.company:
        update_fields["company"] = company
    if portrait_url and not profile.photo_object_key:
        update_fields["photo_object_key"] = portrait_url
    if verified:
        update_fields["last_verification_date"] = int(time.time())
        if id_type:
            update_fields["verification_method"] = id_type
            update_fields["id_type"] = id_type

    if update_fields:
        await update_visitor_profile(
            {"_id": ObjectId(profile.id), "tenant_id": tenant_id},
            VisitorProfileUpdate(**update_fields),
        )

    await increment_visitor_profile_visits({"_id": ObjectId(profile.id)})


async def submit_verified_checkin(
    *,
    checkin_config_id: str,
    email: str,
    phone: str,
    bio_data: dict,
    tenant_specific_data: dict,
    purpose: CheckinPurpose,
    id_file_bytes: Optional[bytes] = None,
    id_file_mime: Optional[str] = None,
    id_type: Optional[IDType] = None,
) -> CheckinOut:
    """Single-step check-in that optionally runs ID verification.

    If id_file_bytes is provided:
      - Runs OCR + face crop via visitor_verification_service.
      - Creates/updates a visitor with verified=True.
      - Merges OCR bio_data under submitted bio_data (submitted wins on conflict).
      - On verification failure raises 422 telling the caller to either upload a
        clearer document or fall back to manual entry (retry without id_file).
    Otherwise:
      - Upserts visitor by email/phone, verified=False, submitted bio_data only.

    Either way, the visitor's `verified` flag propagates to the Checkin and the
    check-in is created in PENDING_APPROVAL with the usual notification.
    """
    config = await get_checkin_config({"_id": checkin_config_id, "active": True})
    if not config:
        raise resource_not_found(
            resource="CheckinConfig", resource_id=checkin_config_id
        )
    return await _submit_verified_checkin_core(
        tenant_id=config.tenant_id,
        checkin_config_id=checkin_config_id,
        required_field_keys={f.key for f in config.required_fields},
        email=email,
        phone=phone,
        bio_data=bio_data,
        tenant_specific_data=tenant_specific_data,
        purpose=purpose,
        id_file_bytes=id_file_bytes,
        id_file_mime=id_file_mime,
        id_type=id_type,
    )


async def submit_returning_visitor_checkin_by_id(
    *,
    tenant_id: str,
    visitor_id: str,
    purpose: CheckinPurpose,
    tenant_specific_data: dict,
) -> CheckinOut:
    """Submit a check-in for an already-known visitor using their visitor_id.

    This is the companion to the ``/visitor-status`` lookup. The frontend
    looks up the visitor (no PII returned), gets back the ``visitor_id``,
    and then submits with only:
      - ``purpose`` (always required)
      - ``tenant_specific_data`` — required if the tenant's active check-in
        config has required fields in the ``TENANT_SPECIFIC`` category

    BIO-category required fields (name, email, phone, company) are satisfied
    from the stored visitor record — the frontend never re-sends them.

    This endpoint is the right call whenever ``/visitor-status`` returns a
    non-null ``visitor_id``. If ``visitor_id`` was null, use the
    email/phone-based ``submit_verified_checkin_for_tenant`` instead.
    """
    from bson import ObjectId

    from repositories.checkin_config_repo import (
        get_active_checkin_config_for_tenant,
    )
    from repositories.tenant_repo import get_tenant
    from repositories.visitor_repo import get_visitor
    from schemas.imports import CheckinFieldCategory
    from services.checkin_config_service import DEFAULT_REQUIRED_FIELDS

    if not ObjectId.is_valid(tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)

    if not ObjectId.is_valid(visitor_id):
        raise resource_not_found(resource="Visitor", resource_id=visitor_id)

    visitor = await get_visitor({"_id": visitor_id, "tenant_id": tenant_id})
    if visitor is None:
        raise resource_not_found(resource="Visitor", resource_id=visitor_id)

    config = await get_active_checkin_config_for_tenant(tenant_id)
    if config is not None:
        checkin_config_id = config.id or ""
        required_fields = list(config.required_fields)
    else:
        checkin_config_id = ""
        required_fields = list(DEFAULT_REQUIRED_FIELDS)

    # Only TENANT_SPECIFIC required fields must be re-sent on every visit.
    # BIO fields are already on the visitor record.
    required_tenant_specific_keys = {
        f.key
        for f in required_fields
        if f.required and f.category == CheckinFieldCategory.TENANT_SPECIFIC
    }
    missing_fields = required_tenant_specific_keys - set(tenant_specific_data.keys())
    if missing_fields:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Missing required tenant-specific fields",
            details={"missing_fields": list(missing_fields)},
        )

    # Reject a second pending check-in for the same visitor.
    existing_pending = await get_active_pending_for_visitor(tenant_id, visitor_id)
    if existing_pending:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Visitor has a pending check-in already",
            details={"existing_checkin_id": existing_pending.id},
        )

    create_data = CheckinCreate(
        tenant_id=tenant_id,
        visitor_id=visitor_id,
        checkin_config_id=checkin_config_id,
        id_extraction_id=None,
        tenant_specific_data=tenant_specific_data,
        purpose=purpose,
        state=CheckinState.PENDING_APPROVAL,
        verified=visitor.verified,
    )
    checkin = await create_checkin(create_data)

    # Keep the VisitorProfile visit counter in sync (fire-and-forget).
    try:
        await _upsert_visitor_profile_from_submit(
            tenant_id=tenant_id,
            email=visitor.email,
            phone=visitor.phone,
            full_name=visitor.full_name,
            company=(visitor.bio_data or {}).get("company")
            or (visitor.bio_data or {}).get("organization"),
            portrait_url=visitor.portrait_url,
            verified=visitor.verified,
            id_type=(
                visitor.verification_method.value
                if visitor.verification_method is not None
                else None
            ),
        )
    except Exception as e:
        import logging

        logging.warning(f"Failed to upsert visitor profile from returning submit: {e}")

    # Fire notification (fire-and-forget).
    try:
        from services.notification_service import notify_checkin_pending_approval

        await notify_checkin_pending_approval(
            tenant_id=tenant_id,
            checkin_id=checkin.id or "",
            visitor_name=visitor.full_name,
            verified=visitor.verified,
            purpose=purpose.purpose,
            host_employee_id="",
        )
    except Exception as e:
        import logging

        logging.warning(f"Failed to send returning-visitor checkin notification: {e}")

    return checkin


async def submit_verified_checkin_for_tenant(
    *,
    tenant_id: str,
    email: str,
    phone: str,
    bio_data: dict,
    tenant_specific_data: dict,
    purpose: CheckinPurpose,
    id_file_bytes: Optional[bytes] = None,
    id_file_mime: Optional[str] = None,
    id_type: Optional[IDType] = None,
) -> CheckinOut:
    """Tenant-scoped submit. Resolves the tenant's active config, or falls back
    to the default required-field set when the tenant hasn't configured one yet.

    Used by the public kiosk endpoint ``POST /public/tenants/{tenant_id}/submit``
    so the kiosk can submit against the tenant even when the super_admin has
    not yet customized the check-in form.
    """
    from bson import ObjectId

    from repositories.tenant_repo import get_tenant
    from repositories.checkin_config_repo import (
        get_active_checkin_config_for_tenant,
    )
    from services.checkin_config_service import DEFAULT_REQUIRED_FIELDS

    if not ObjectId.is_valid(tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)

    config = await get_active_checkin_config_for_tenant(tenant_id)
    if config is not None:
        checkin_config_id = config.id or ""
        required_field_keys = {f.key for f in config.required_fields}
    else:
        checkin_config_id = ""
        required_field_keys = {f.key for f in DEFAULT_REQUIRED_FIELDS}

    return await _submit_verified_checkin_core(
        tenant_id=tenant_id,
        checkin_config_id=checkin_config_id,
        required_field_keys=required_field_keys,
        email=email,
        phone=phone,
        bio_data=bio_data,
        tenant_specific_data=tenant_specific_data,
        purpose=purpose,
        id_file_bytes=id_file_bytes,
        id_file_mime=id_file_mime,
        id_type=id_type,
    )


async def _submit_verified_checkin_core(
    *,
    tenant_id: str,
    checkin_config_id: str,
    required_field_keys: set[str],
    email: str,
    phone: str,
    bio_data: dict,
    tenant_specific_data: dict,
    purpose: CheckinPurpose,
    id_file_bytes: Optional[bytes] = None,
    id_file_mime: Optional[str] = None,
    id_type: Optional[IDType] = None,
) -> CheckinOut:
    from repositories.visitor_repo import find_visitor_by_email_or_phone_any
    from schemas.visitor_schema import VisitorCreate

    if not email or not phone:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="email and phone are required",
        )

    if id_file_bytes and not id_type:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="id_type is required when an ID file is uploaded",
        )

    # 2. Resolve visitor — with or without verification
    id_extraction_id: Optional[str] = None
    merged_bio_data = dict(bio_data)

    if id_file_bytes and id_type:
        from services.visitor_verification_service import verify_visitor_from_id

        try:
            verified_visitor = await verify_visitor_from_id(
                tenant_id=tenant_id,
                file_bytes=id_file_bytes,
                mime_type=id_file_mime or "application/octet-stream",
                id_type=id_type,
                email=email,
                phone=phone,
            )
        except AppException:
            # Re-raise with a friendlier combined message so the kiosk can
            # present both options to the visitor.
            raise AppException(
                status_code=422,
                code=ErrorCode.VALIDATION_FAILED,
                message=(
                    "We couldn't verify the uploaded ID. Please upload a clearer "
                    "photo of the document, or continue by entering your details "
                    "manually (re-submit without the id_file)."
                ),
            )

        # OCR bio_data under submitted bio_data (submitted wins on conflict)
        ocr_bio = dict(verified_visitor.bio_data or {})
        ocr_bio.update(bio_data)
        merged_bio_data = ocr_bio
        visitor = verified_visitor
    else:
        # Unverified path — look up or create the visitor using submitted fields
        existing = await find_visitor_by_email_or_phone_any(
            tenant_id=tenant_id, email=email, phone=phone
        )
        if existing is not None:
            # Reuse the upsert helper; it won't downgrade verified.
            from repositories.visitor_repo import update_visitor as repo_update_visitor
            from schemas.visitor_schema import VisitorUpdate

            update_payload = VisitorUpdate(
                full_name=str(merged_bio_data.get("full_name") or existing.full_name),
                bio_data=merged_bio_data,
            )
            if not existing.email:
                update_payload.email = email
            if not existing.phone:
                update_payload.phone = phone
            assert existing.id is not None
            visitor = await repo_update_visitor(existing.id, update_payload)
        else:
            from repositories.visitor_repo import create_visitor

            visitor = await create_visitor(
                VisitorCreate(
                    tenant_id=tenant_id,
                    full_name=str(merged_bio_data.get("full_name") or "Unknown"),
                    email=email,
                    phone=phone,
                    bio_data=merged_bio_data,
                    verified=False,
                )
            )

    visitor_id = visitor.id or ""

    # 2b. Backfill merged_bio_data from the resolved visitor (and any matching
    # VisitorProfile) so a returning visitor who submits only purpose + phone
    # +email still passes required-field validation. Submitted values always
    # win — stored values only fill in gaps.
    stored_fallback = await _collect_returning_visitor_fallback(
        tenant_id=tenant_id,
        visitor=visitor,
        email=email,
        phone=phone,
    )
    merged_bio_data = {**stored_fallback, **merged_bio_data}

    # 2c. Upsert a VisitorProfile keyed on email OR phone so repeat submissions
    # are linked to the same profile for visit-history tracking.
    try:
        await _upsert_visitor_profile_from_submit(
            tenant_id=tenant_id,
            email=email,
            phone=phone,
            full_name=str(merged_bio_data.get("full_name") or visitor.full_name),
            company=merged_bio_data.get("company") or merged_bio_data.get("organization"),
            portrait_url=visitor.portrait_url,
            verified=visitor.verified,
            id_type=(id_type.value if id_type is not None else None),
        )
    except Exception as e:
        import logging

        logging.warning(f"Failed to upsert visitor profile from submit: {e}")

    # 3. Validate required fields against combined data
    available_keys = set(merged_bio_data.keys()) | set(tenant_specific_data.keys())
    missing_fields = required_field_keys - available_keys
    if missing_fields:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Missing required fields",
            details={"missing_fields": list(missing_fields)},
        )

    # 4. Reject if the visitor has another pending check-in
    existing_pending = await get_active_pending_for_visitor(tenant_id, visitor_id)
    if existing_pending:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Visitor has a pending check-in already",
            details={"existing_checkin_id": existing_pending.id},
        )

    # If we went through verification, surface the extraction_id so the check-in
    # record can reference it for audit.
    if id_extraction_id is None and id_file_bytes:
        from repositories.id_verification_hash_repo import find_by_hash
        import hashlib

        sha = hashlib.sha256(id_file_bytes).hexdigest()
        hash_record = await find_by_hash(tenant_id=tenant_id, sha256=sha)
        if hash_record is not None:
            id_extraction_id = hash_record.extraction_id

    # 5. Create check-in
    create_data = CheckinCreate(
        tenant_id=tenant_id,
        visitor_id=visitor_id,
        checkin_config_id=checkin_config_id,
        id_extraction_id=id_extraction_id,
        tenant_specific_data=tenant_specific_data,
        purpose=purpose,
        state=CheckinState.PENDING_APPROVAL,
        verified=visitor.verified,
    )
    checkin = await create_checkin(create_data)

    # 6. Fire notification (fire-and-forget — never fail the check-in on notify errors)
    try:
        from services.notification_service import notify_checkin_pending_approval

        await notify_checkin_pending_approval(
            tenant_id=tenant_id,
            checkin_id=checkin.id or "",
            visitor_name=visitor.full_name,
            verified=visitor.verified,
            purpose=purpose.purpose,
            host_employee_id="",
        )
    except Exception as e:
        import logging

        logging.warning(f"Failed to send checkin notification: {e}")

    return checkin


async def submit_checkin(
    checkin_config_id: str, req: CheckinSubmitRequest
) -> CheckinOut:
    """Submit a check-in.

    1. Resolve config (error if inactive)
    2. Validate required fields are present
    3. Upsert visitor
    4. Check for existing pending checkin (409 if exists)
    5. Create checkin with state PENDING_APPROVAL
    6. Fire notification
    """
    # Resolve config
    config = await get_checkin_config({"_id": checkin_config_id, "active": True})
    if not config:
        raise resource_not_found(
            resource="CheckinConfig", resource_id=checkin_config_id
        )

    tenant_id = config.tenant_id

    # Validate required fields
    bio_data_keys = set(req.bio_data.keys())
    tenant_specific_keys = set(req.tenant_specific_data.keys())
    required_field_keys = {f.key for f in config.required_fields}

    available_keys = bio_data_keys | tenant_specific_keys
    missing_fields = required_field_keys - available_keys
    if missing_fields:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Missing required fields",
            details={"missing_fields": list(missing_fields)},
        )

    # Upsert visitor
    from services.visitor_service import upsert_visitor_from_checkin

    visitor = await upsert_visitor_from_checkin(
        tenant_id=tenant_id,
        bio_data=req.bio_data,
        id_extraction_id=req.id_extraction_id,
        visitor_id=req.visitor_id,
    )

    visitor_id = visitor.id or ""

    # Check for existing pending checkin
    existing = await get_active_pending_for_visitor(tenant_id, visitor_id)
    if existing:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Visitor has a pending check-in already",
            details={"existing_checkin_id": existing.id},
        )

    # Create checkin
    create_data = CheckinCreate(
        tenant_id=tenant_id,
        visitor_id=visitor_id,
        checkin_config_id=checkin_config_id,
        id_extraction_id=req.id_extraction_id,
        tenant_specific_data=req.tenant_specific_data,
        purpose=req.purpose,
        state=CheckinState.PENDING_APPROVAL,
        verified=visitor.verified,
    )
    checkin = await create_checkin(create_data)

    # Fire notification
    try:
        from services.notification_service import notify_checkin_pending_approval

        await notify_checkin_pending_approval(
            tenant_id=tenant_id,
            checkin_id=checkin.id or "",
            visitor_name=visitor.full_name,
            verified=visitor.verified,
            purpose=req.purpose.purpose,
            host_employee_id="",  # TODO: extract from context if available
        )
    except Exception as e:
        import logging

        logging.warning(f"Failed to send checkin notification: {e}")

    return checkin


async def list_checkins_for_tenant(
    tenant_id: str, state: Optional[str] = None, skip: int = 0, limit: int = 20
) -> tuple[list[CheckinOut], int]:
    """List check-ins for a tenant with optional state filter."""
    filter_dict = {"tenant_id": tenant_id}
    if state:
        filter_dict["state"] = state

    checkins = await get_checkins(filter_dict, skip=skip, limit=limit)
    total = await count_checkins(filter_dict)
    return checkins, total


async def get_checkin_detail(tenant_id: str, checkin_id: str) -> CheckinOut:
    """Get check-in detail with tenant validation."""
    checkin = await get_checkin({"_id": checkin_id, "tenant_id": tenant_id})
    if not checkin:
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)
    return checkin


async def confirm_checkin(
    checkin_id: str, principal, req: CheckinConfirmRequest
) -> dict:
    """Confirm a check-in (approve or reject)."""
    from repositories.visitor_repo import get_visitor

    # Fetch checkin
    checkin = await get_checkin({"_id": checkin_id})
    if not checkin:
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)

    tenant_id = checkin.tenant_id

    if req.action == "approve":
        # Fetch visitor for response payload
        visitor = await get_visitor({"_id": checkin.visitor_id})
        if not visitor:
            raise resource_not_found(resource="Visitor", resource_id=checkin.visitor_id)

        # Create badge
        now = int(time.time())
        # Set expires_at to end of tenant's local day (for now use UTC end-of-day)
        expires_at = ((now // 86400) + 1) * 86400  # Next midnight UTC

        qr_code_value = secrets.token_urlsafe(24)
        badge_create = BadgeCreate(
            tenant_id=tenant_id,
            checkin_id=checkin_id,
            qr_code_value=qr_code_value,
            issued_at=now,
            expires_at=expires_at,
        )
        badge = await create_badge(badge_create)

        # Update checkin
        await update_checkin(
            checkin_id,
            CheckinUpdate(
                state=CheckinState.APPROVED,
                approved_by_user_id=principal.user_id,
                approved_at=now,
            ),
        )

        # Fire notification
        try:
            from services.notification_service import notify_checkin_approved

            await notify_checkin_approved(
                tenant_id=tenant_id,
                checkin_id=checkin_id,
                badge_id=badge.id or "",
                visitor_name=visitor.full_name,
                host_employee_id="",  # TODO: extract from context if available
                approved_by_user_id=principal.user_id,
            )
        except Exception as e:
            import logging

            logging.warning(f"Failed to send approval notification: {e}")

        # Build response
        badge_payload = BadgePayload(
            badge_id=badge.id or "",
            qr_code_value=badge.qr_code_value,
            visitor_name=visitor.full_name,
            verified=visitor.verified,
            portrait_url=visitor.portrait_url,
            host_employee_name=None,  # TODO: resolve from context if available
            purpose=checkin.purpose.purpose,
            issued_at=badge.issued_at,
            expires_at=badge.expires_at,
        )

        return {
            "checkin_id": checkin_id,
            "state": CheckinState.APPROVED,
            "badge": badge_payload,
        }

    elif req.action == "reject":
        now = int(time.time())
        # Update checkin
        await update_checkin(
            checkin_id,
            CheckinUpdate(
                state=CheckinState.REJECTED,
                rejection_reason=req.notes,
            ),
        )

        # Fire notification
        try:
            from services.notification_service import notify_checkin_rejected

            visitor = await get_visitor({"_id": checkin.visitor_id})
            await notify_checkin_rejected(
                tenant_id=tenant_id,
                checkin_id=checkin_id,
                visitor_name=visitor.full_name if visitor else "Unknown",
                host_employee_id="",  # TODO: extract from context if available
                rejected_by_user_id=principal.user_id,
                reason=req.notes or "No reason provided",
            )
        except Exception as e:
            import logging

            logging.warning(f"Failed to send rejection notification: {e}")

        return {
            "checkin_id": checkin_id,
            "state": CheckinState.REJECTED,
            "rejected_by_user_id": principal.user_id,
            "rejected_at": now,
            "rejection_reason": req.notes,
        }
    else:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Invalid action: {req.action}. Must be 'approve' or 'reject'",
        )


async def list_checkins_analytics(
    tenant_id: str,
    state: Optional[str] = None,
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[CheckinOut], int]:
    """List check-ins for analytics with date range filtering."""
    filter_dict: dict[str, Any] = {"tenant_id": tenant_id}
    if state:
        filter_dict["state"] = state

    # Add date range filter
    if from_ts is not None or to_ts is not None:
        date_filter: dict[str, int] = {}
        if from_ts is not None:
            date_filter["$gte"] = from_ts
        if to_ts is not None:
            date_filter["$lte"] = to_ts
        if date_filter:
            filter_dict["date_created"] = date_filter

    checkins = await get_checkins(filter_dict, skip=skip, limit=limit)
    total = await count_checkins(filter_dict)
    return checkins, total
