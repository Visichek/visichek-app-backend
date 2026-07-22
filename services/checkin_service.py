from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Optional

from core.errors import AppException, ErrorCode, resource_not_found
from core.geofencing import check_visitor_within_geofence
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
    CheckinWithVisitorOut,
    IdentityCheckSummary,
)
from schemas.imports import IDType, KYCStatus, VerificationMethod
from schemas.summary_schema import ManualVerificationInfo, VisitorBriefSummary
from services.consent_service import (
    enforce_consent_if_required,
    record_visitor_consent,
)
from services.dashboard_cache_service import invalidate_tenant_dashboard_cache
from services.plan_limits import (
    KIOSK_CAP_MESSAGE,
    _get_plan_data,
    enforce_branch_visitor_cap,
)


logger = logging.getLogger(__name__)


async def _resolve_checkin_branch_id(
    tenant_id: str, tenant_specific_data: Optional[dict]
) -> Optional[str]:
    """Resolve the branch a check-in belongs to.

    The signed registration QR scope (when present) is stamped onto
    ``tenant_specific_data['branch_id']`` by ``_enforce_registration_token_scope``;
    that wins. Otherwise the check-in falls back to the tenant HQ so it is
    never branch-null going forward. The value is promoted to the first-class
    ``CheckinCreate.branch_id`` field by the callers.
    """
    branch_id = (tenant_specific_data or {}).get("branch_id")
    if branch_id:
        return branch_id
    from services.branch_service import resolve_hq_branch_id

    return await resolve_hq_branch_id(tenant_id)


async def _enforce_tenant_geofence(
    *,
    tenant_id: str,
    visitor_lat: Optional[float],
    visitor_lng: Optional[float],
) -> None:
    """Reject the check-in if the visitor is outside the tenant's geofence.

    Loads the tenant's settings via the upsert helper (so tenants that have
    never saved settings still get the default-disabled behaviour), then
    delegates the decision to :func:`core.geofencing.check_visitor_within_geofence`.
    Short-circuits cheaply when geofencing is disabled so this helper is safe
    to call from every submit path.
    """
    from services.tenant_settings_service import retrieve_or_create_tenant_settings

    try:
        settings = await retrieve_or_create_tenant_settings(tenant_id)
    except Exception:
        logger.warning(
            "geofencing: failed to load tenant settings tenant_id=%s — allowing",
            tenant_id,
            exc_info=True,
        )
        return

    if not getattr(settings, "geofencing_enabled", False):
        return

    result = check_visitor_within_geofence(
        tenant_settings=settings,
        visitor_lat=visitor_lat,
        visitor_lng=visitor_lng,
    )
    if result.allowed:
        return

    message_map = {
        "missing_visitor_location": (
            "This location requires geofence verification. Please enable "
            "location access in your browser and retry."
        ),
        "outside_reference_point": (
            "You appear to be outside the check-in zone. Please move closer "
            "to the reception area and retry."
        ),
        "outside_approver_radius": (
            "No approver is currently in range to verify your check-in. "
            "Please ask reception to retry on your behalf."
        ),
        "no_active_approvers": (
            "No approver is currently on-site to verify your check-in. "
            "Please contact your host or try again shortly."
        ),
        "tenant_misconfigured": (
            "This tenant has geofencing enabled but no reference point "
            "configured. Ask the super admin to set a reference location."
        ),
    }
    raise AppException(
        status_code=403,
        code=ErrorCode.GEOFENCE_VIOLATION,
        message=message_map.get(result.reason or "", "Geofence verification failed"),
        details={"reason": result.reason, **(result.details or {})},
    )


def _build_identity_check(checkin: Any, kyc: Any) -> IdentityCheckSummary:
    """Explain a check-in's verification state to the receptionist.

    Every branch returns a reason. "Not verified" alone is ambiguous — it looks
    the same whether nobody ran a check, the visitor skipped it, or the ID they
    presented belongs to someone else — and the receptionist needs to tell those
    apart before deciding who to let in.
    """
    verified = bool(getattr(checkin, "verified", False))
    state = getattr(checkin, "state", None)
    state_value = getattr(state, "value", state)

    # No provider record: either a human vouched, or no check ever ran.
    if kyc is None:
        manual = getattr(checkin, "manual_verification", None)
        if verified and manual is not None:
            by = getattr(manual, "verified_by_name", None) or "a staff member"
            return IdentityCheckSummary(
                verified=True,
                status="manual",
                headline="Verified by staff",
                reason=f"{by} checked this visitor's physical ID in person.",
            )
        if verified:
            return IdentityCheckSummary(
                verified=True,
                status="manual",
                headline="Verified",
                reason="This visitor was marked verified without an automated ID check.",
            )
        if state_value == CheckinState.PENDING_VERIFICATION.value:
            return IdentityCheckSummary(
                verified=False,
                status="ongoing",
                headline="ID check in progress",
                reason="Waiting for the visitor to finish the ID check on the kiosk.",
            )
        return IdentityCheckSummary(
            verified=False,
            status="not_started",
            headline="No ID check",
            reason=(
                "No automated ID check ran for this visitor. Check their physical "
                "ID before approving."
            ),
        )

    status = getattr(kyc.status, "value", kyc.status)

    if status == KYCStatus.SUCCESS.value:
        # The dangerous case: the document is real, but it is not theirs.
        if kyc.identity_match_passed is False:
            detail = (
                kyc.identity_mismatch_reason
                or "The verified ID does not match the details this visitor entered"
            ).rstrip(" .")
            return IdentityCheckSummary(
                verified=False,
                status=status,
                headline="ID mismatch",
                reason=(
                    f"{detail}. Do not approve without checking their physical ID."
                ),
                mismatch=True,
                extracted_name=kyc.extracted_full_name,
                name_score=kyc.identity_name_score,
            )
        return IdentityCheckSummary(
            verified=True,
            status=status,
            headline="ID verified",
            reason=(
                "The visitor's ID was verified and matches the details they entered."
            ),
            extracted_name=kyc.extracted_full_name,
            name_score=kyc.identity_name_score,
        )

    if status == KYCStatus.FAILED.value:
        return IdentityCheckSummary(
            verified=False,
            status=status,
            headline="ID check failed",
            reason=(
                kyc.failure_reason
                or "The ID check failed. The visitor could not prove their identity."
            ),
        )

    if status == KYCStatus.SKIPPED.value:
        return IdentityCheckSummary(
            verified=False,
            status=status,
            headline="ID check skipped",
            reason=(
                "The visitor skipped the ID check"
                + (f" ({kyc.failure_reason})" if kyc.failure_reason else "")
                + ". Check their physical ID before approving."
            ),
        )

    if status == KYCStatus.EXPIRED.value:
        return IdentityCheckSummary(
            verified=False,
            status=status,
            headline="ID check expired",
            reason="The ID check expired before the visitor completed it.",
        )

    return IdentityCheckSummary(
        verified=verified,
        status=status,
        headline="ID check in progress",
        reason="The visitor's ID check has not finished yet.",
    )


async def _kyc_reference_is_verified(*, tenant_id: str, reference_id: str) -> bool:
    """True only when a kiosk-supplied KYC reference is backed by real evidence.

    Requires the reference to resolve to a verification row that (a) exists,
    (b) belongs to this tenant — otherwise one tenant's reference could verify
    a visitor at another, (c) the provider marked SUCCESS, and (d) passed the
    submitted-vs-extracted identity reconciliation.

    Fails closed: any lookup error returns False, because "we could not confirm
    this identity" must never render as "this identity is confirmed."
    """
    try:
        from repositories.kyc_repo import get_kyc_by_reference
        from schemas.imports import KYCStatus

        record = await get_kyc_by_reference(reference_id)
        if record is None:
            return False
        if record.tenant_id and record.tenant_id != tenant_id:
            return False
        if record.status != KYCStatus.SUCCESS:
            return False
        return record.identity_match_passed is not False
    except Exception:
        logger.warning(
            "checkin submit: kyc reference validation failed tenant=%s ref=%s",
            tenant_id,
            reference_id,
            exc_info=True,
        )
        return False


def _visitor_to_brief(visitor: Any) -> VisitorBriefSummary:
    bio = visitor.bio_data or {}
    manual = getattr(visitor, "manual_verification", None)
    # A manual (staff-vouched) verification wins the displayed method: the
    # ``verification_method`` column on the visitor row historically stores
    # the ID *document* type (IDType), so we derive "manual" from the
    # attribution block rather than overloading that column.
    if manual is not None:
        method = "manual"
    elif visitor.verification_method is not None:
        method = visitor.verification_method.value
    else:
        method = None
    return VisitorBriefSummary(
        id=visitor.id or "",
        full_name=visitor.full_name,
        email=visitor.email,
        phone=visitor.phone,
        company=bio.get("company") or bio.get("organization"),
        verified=bool(visitor.verified),
        verification_method=method,
        portrait_url=visitor.portrait_url,
        manual_verification=manual,
        created_at=getattr(visitor, "date_created", None),
    )


async def _enrich_checkins_with_visitors(
    tenant_id: str, checkins: list[CheckinOut]
) -> list[CheckinWithVisitorOut]:
    """Embed a ``VisitorBriefSummary`` on each check-in. Uses a single
    batched ``$in`` query instead of N lookups, so the approval queue
    endpoint stays cheap as it grows.

    The receptionist UI needs the visitor's name + contact to confirm
    identity, cross-check spelling, and verify that ID-verification
    actually ran — it cannot render a useful approval row from just
    ``visitor_id``.
    """
    if not checkins:
        return []
    from repositories.visitor_repo import get_visitors_by_ids

    visitor_ids = list({c.visitor_id for c in checkins if c.visitor_id})
    visitors = await get_visitors_by_ids(tenant_id=tenant_id, visitor_ids=visitor_ids)
    by_id = {v.id: v for v in visitors if v.id}

    # Resolve a BranchBriefSummary for each distinct branch referenced, so the
    # approval queue renders the originating branch without a follow-up call.
    import asyncio
    from services.summary_resolver import resolve_branch_summary

    branch_ids = list({c.branch_id for c in checkins if c.branch_id})
    branch_summaries = await asyncio.gather(
        *[resolve_branch_summary(bid) for bid in branch_ids]
    )
    branch_by_id = dict(zip(branch_ids, branch_summaries))

    # One batched KYC lookup for the whole page, so every row can explain WHY
    # it is (or isn't) verified without turning the queue into N+1 queries.
    from repositories.kyc_repo import get_kyc_by_checkin_ids

    kyc_by_checkin = await get_kyc_by_checkin_ids([c.id for c in checkins if c.id])

    enriched: list[CheckinWithVisitorOut] = []
    for c in checkins:
        visitor = by_id.get(c.visitor_id)
        enriched.append(
            CheckinWithVisitorOut(
                **c.model_dump(by_alias=True),
                visitor=_visitor_to_brief(visitor) if visitor is not None else None,
                branch_summary=branch_by_id.get(c.branch_id) if c.branch_id else None,
                identity_check=_build_identity_check(c, kyc_by_checkin.get(c.id or "")),
            )
        )
    return enriched


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
        profile = await get_visitor_profile_by_phone(tenant_id=tenant_id, phone=phone)
    if profile is None and email:
        profile = await get_visitor_profile_by_email(tenant_id=tenant_id, email=email)

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
) -> Optional[str]:
    """Upsert a VisitorProfile row tied to the submitting visitor.

    The profile is the authoritative record of "has this person visited us
    before" for the public prefill lookup. Keyed on phone first, then email —
    whichever the visitor supplied is used to find an existing profile; a new
    one is created if neither matches. Visit count is NOT incremented here —
    callers must do so only after per-branch cap enforcement passes, so a
    429-blocked submission never inflates the visitor's visit history.

    Fire-and-forget at the caller — any exception here is logged but never
    blocks the check-in.
    """
    from bson import ObjectId

    from repositories.visitor_profile_repo import (
        get_visitor_profile_by_email,
        get_visitor_profile_by_phone,
        update_visitor_profile,
    )
    from schemas.visitor_profile_schema import VisitorProfileUpdate
    from services.visitor_profile_service import get_or_create_visitor_profile

    profile = None
    if phone:
        profile = await get_visitor_profile_by_phone(tenant_id=tenant_id, phone=phone)
    if profile is None and email:
        profile = await get_visitor_profile_by_email(tenant_id=tenant_id, email=email)

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
        return None

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

    return profile.id


def _registration_token_id(token: str) -> str:
    """Stable, non-reversible identifier for a registration QR token.

    Phase A3 (Issue 5 audit follow-up). The raw token is HMAC-signed
    and we don't want to persist it on the audit trail — anyone with
    read access to the audit log could otherwise replay the QR.
    Truncated SHA-256 is one-way and collision-resistant enough for
    "which token was scanned for this check-in?" queries.
    """
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _enforce_registration_token_scope(
    *,
    tenant_id: str,
    tenant_specific_data: dict,
    registration_token: Optional[str],
) -> Optional[dict]:
    """Verify a signed QR registration token and override conflicting client values.

    Issue 5 backend gate. When the kiosk forwards a ``registration_token``
    that was minted by ``POST /v1/super-admin/registration-qr``, this helper:

      - Verifies the HMAC signature + expiry via
        ``services.qr_service.verify_registration_token``.
      - Rejects tokens whose ``tenant_id`` doesn't match the resolved
        check-in tenant (visitor scanned the wrong tenant's QR, or a
        token was replayed across tenants).
      - Overrides ``tenant_specific_data['department_id']`` /
        ``branch_id`` with the token's scope so a browser can't claim a
        different department just because they typed it. The override
        happens silently — that's exactly the security property the
        token is meant to provide.
      - Returns the verified scope dict plus a non-reversible
        ``token_id`` (sha256 prefix of the raw token) so the caller
        can record which QR shaped each registration on the audit
        trail without storing the replayable token itself.

    Returns ``None`` when no token was provided (anonymous check-in
    path). Raises 400 ``INVALID_REGISTRATION_TOKEN`` for any structural
    failure so the kiosk surfaces a recoverable error.
    """
    if not registration_token:
        return None

    from services.qr_service import verify_registration_token

    scope = verify_registration_token(registration_token)
    if not scope:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Registration QR token is invalid or expired",
            details={"reason": "INVALID_REGISTRATION_TOKEN"},
        )

    if scope.get("tenant_id") and scope["tenant_id"] != tenant_id:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Registration QR token does not belong to this tenant",
            details={"reason": "TENANT_SCOPE_MISMATCH"},
        )

    # Override scope-bearing fields. We don't merge — the token wins
    # outright because the browser-supplied value can't be trusted.
    if scope.get("department_id"):
        tenant_specific_data["department_id"] = scope["department_id"]
    if scope.get("branch_id"):
        tenant_specific_data["branch_id"] = scope["branch_id"]

    # Attach a stable, non-reversible token id so the caller can land
    # it on the audit row.
    scope = {**scope, "token_id": _registration_token_id(registration_token)}

    return scope


async def submit_verified_checkin(
    *,
    checkin_config_id: str,
    email: Optional[str],
    phone: str,
    bio_data: dict,
    tenant_specific_data: dict,
    purpose: CheckinPurpose,
    id_file_bytes: Optional[bytes] = None,
    id_file_mime: Optional[str] = None,
    id_type: Optional[IDType] = None,
    visitor_lat: Optional[float] = None,
    visitor_lng: Optional[float] = None,
    kyc_reference_id: Optional[str] = None,
    registration_token: Optional[str] = None,
    consent: Optional[dict] = None,
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

    Issue 5: when ``registration_token`` is present, the token's
    department/branch scope overrides whatever the browser put in
    ``tenant_specific_data`` and a mismatched tenant raises 400 before
    any database writes.
    """
    config = await get_checkin_config({"_id": checkin_config_id, "active": True})
    if not config:
        raise resource_not_found(
            resource="CheckinConfig", resource_id=checkin_config_id
        )

    # Enforce token scope BEFORE we hit visitor upserts so a mismatch
    # never leaves partial state behind.
    token_scope = _enforce_registration_token_scope(
        tenant_id=config.tenant_id,
        tenant_specific_data=tenant_specific_data,
        registration_token=registration_token,
    )

    # Merge tenant form fields into the config's required-field set so
    # super_admins can drive the kiosk via the form builder without
    # touching the legacy CheckinConfig row.
    from services.checkin_config_service import resolve_required_fields_for_tenant

    merged_fields, _form = await resolve_required_fields_for_tenant(config.tenant_id)
    merged_required_keys = {f.key for f in merged_fields if f.required}

    checkin = await _submit_verified_checkin_core(
        tenant_id=config.tenant_id,
        checkin_config_id=checkin_config_id,
        required_field_keys=merged_required_keys,
        email=email,
        phone=phone,
        bio_data=bio_data,
        tenant_specific_data=tenant_specific_data,
        purpose=purpose,
        id_file_bytes=id_file_bytes,
        id_file_mime=id_file_mime,
        id_type=id_type,
        visitor_lat=visitor_lat,
        visitor_lng=visitor_lng,
        kyc_reference_id=kyc_reference_id,
        consent=consent,
    )

    # Phase A3 audit hook (Issue 5). Land a focused event on the
    # check-in whenever a QR token shaped the registration. Carries
    # the token id (sha256 prefix — not the raw token) and the
    # resolved scope so support can trace "which QR scanned this
    # visitor in" without exposing replayable secrets. Fire-and-forget
    # — an audit failure must not roll back a successful check-in.
    if token_scope:
        try:
            from services.audit_service import record_audit_event

            await record_audit_event(
                actor_id=checkin.visitor_id,
                actor_role="kiosk_visitor",
                action="checkin.registered_via_qr",
                resource_type="checkin",
                resource_id=checkin.id or "",
                tenant_id=config.tenant_id,
                details={
                    "registration_token_id": token_scope.get("token_id"),
                    "registration_token_scope": {
                        "department_id": token_scope.get("department_id"),
                        "branch_id": token_scope.get("branch_id"),
                    },
                },
            )
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "checkin.registered_via_qr audit record failed for checkin %s",
                checkin.id,
                exc_info=True,
            )

    return checkin


async def submit_returning_visitor_checkin_by_id(
    *,
    tenant_id: str,
    visitor_id: str,
    purpose: CheckinPurpose,
    tenant_specific_data: dict,
    visitor_lat: Optional[float] = None,
    visitor_lng: Optional[float] = None,
    consent: Optional[dict] = None,
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
    from services.checkin_config_service import (
        resolve_required_fields_for_tenant,
    )

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

    consent = consent or {}
    await enforce_consent_if_required(
        tenant_id, consent_granted=consent.get("consent_granted")
    )

    await _enforce_tenant_geofence(
        tenant_id=tenant_id,
        visitor_lat=visitor_lat,
        visitor_lng=visitor_lng,
    )

    config = await get_active_checkin_config_for_tenant(tenant_id)
    checkin_config_id = config.id or "" if config else ""
    required_fields, _form = await resolve_required_fields_for_tenant(tenant_id)

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

    # Keep the VisitorProfile visit counter in sync (fire-and-forget).
    # Resolved BEFORE the check-in is created so the is_new peek used by
    # cap enforcement below reflects the correct profile.
    visitor_profile_id: Optional[str] = None
    try:
        visitor_profile_id = await _upsert_visitor_profile_from_submit(
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

    # Per-branch new-visitor cap enforcement (WS0.2). This is the
    # "submit by visitor_id" (already-known-visitor) choke point. By
    # definition the caller believes this visitor is returning, but that
    # is only true for THIS branch if a ledger row already exists here —
    # if this is their first visit to this particular branch, they count
    # as new and the cap applies (see plan brief).
    resolved_branch_id = await _resolve_checkin_branch_id(
        tenant_id, tenant_specific_data
    )
    is_new_visitor = True
    if visitor_profile_id and resolved_branch_id:
        from repositories.visitor_branch_first_repo import has_first_seen

        is_new_visitor = not await has_first_seen(
            tenant_id, resolved_branch_id, visitor_profile_id
        )
    resolved_plan = await _get_plan_data(tenant_id)
    await enforce_branch_visitor_cap(
        tenant_id,
        resolved_branch_id,
        resolved_plan,
        is_new_visitor=is_new_visitor,
        friendly_message=KIOSK_CAP_MESSAGE,
    )

    # Increment visit count only after cap enforcement passes, so a
    # 429-blocked submission never inflates the visitor's visit history.
    from bson import ObjectId

    if visitor_profile_id and ObjectId.is_valid(visitor_profile_id):
        from repositories.visitor_profile_repo import increment_visitor_profile_visits

        await increment_visitor_profile_visits({"_id": ObjectId(visitor_profile_id)})

    create_data = CheckinCreate(
        tenant_id=tenant_id,
        visitor_id=visitor_id,
        checkin_config_id=checkin_config_id,
        id_extraction_id=None,
        tenant_specific_data=tenant_specific_data,
        branch_id=resolved_branch_id,
        purpose=purpose,
        state=CheckinState.PENDING_APPROVAL,
        verified=visitor.verified,
    )
    checkin = await create_checkin(create_data)
    invalidate_tenant_dashboard_cache(tenant_id)

    # Persist consent acceptance for the returning visitor (fire-and-forget).
    await record_visitor_consent(
        tenant_id=tenant_id,
        consent_granted=consent.get("consent_granted"),
        consent_method=consent.get("consent_method"),
        privacy_notice_id=consent.get("privacy_notice_id"),
        privacy_notice_version_id=consent.get("privacy_notice_version_id"),
        consent_accepted_at=consent.get("consent_accepted_at"),
        checkin_id=checkin.id,
        visitor_id=visitor_id,
        visitor_name_snapshot=visitor.full_name,
        department_id=tenant_specific_data.get("department_id"),
        client_ip=consent.get("client_ip"),
        user_agent=consent.get("user_agent"),
    )

    # New-visitor first-seen ledger (WS0.3). submit-by-visitor-id is a
    # distinct kiosk choke point from _submit_verified_checkin_core (it
    # skips visitor verification since the visitor is already known).
    if visitor_profile_id and create_data.branch_id:
        if is_new_visitor:
            from repositories.visitor_branch_first_repo import record_first_seen

            await record_first_seen(
                tenant_id, create_data.branch_id, visitor_profile_id, int(time.time())
            )
    else:
        logger.warning(
            "visitor_branch_first ledger insert skipped tenant=%s checkin=%s "
            "visitor_profile_id=%s branch_id=%s",
            tenant_id,
            checkin.id,
            visitor_profile_id,
            create_data.branch_id,
        )

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
    email: Optional[str],
    phone: str,
    bio_data: dict,
    tenant_specific_data: dict,
    purpose: CheckinPurpose,
    id_file_bytes: Optional[bytes] = None,
    id_file_mime: Optional[str] = None,
    id_type: Optional[IDType] = None,
    visitor_lat: Optional[float] = None,
    visitor_lng: Optional[float] = None,
    kyc_reference_id: Optional[str] = None,
    consent: Optional[dict] = None,
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
    from services.checkin_config_service import (
        resolve_required_fields_for_tenant,
    )

    if not ObjectId.is_valid(tenant_id):
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise resource_not_found(resource="Tenant", resource_id=tenant_id)

    config = await get_active_checkin_config_for_tenant(tenant_id)
    checkin_config_id = config.id or "" if config else ""
    merged_fields, _form = await resolve_required_fields_for_tenant(tenant_id)
    required_field_keys = {f.key for f in merged_fields if f.required}

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
        visitor_lat=visitor_lat,
        visitor_lng=visitor_lng,
        kyc_reference_id=kyc_reference_id,
        consent=consent,
    )


async def _submit_verified_checkin_core(
    *,
    tenant_id: str,
    checkin_config_id: str,
    required_field_keys: set[str],
    email: Optional[str],
    phone: str,
    bio_data: dict,
    tenant_specific_data: dict,
    purpose: CheckinPurpose,
    id_file_bytes: Optional[bytes] = None,
    id_file_mime: Optional[str] = None,
    id_type: Optional[IDType] = None,
    visitor_lat: Optional[float] = None,
    visitor_lng: Optional[float] = None,
    kyc_reference_id: Optional[str] = None,
    consent: Optional[dict] = None,
) -> CheckinOut:
    from repositories.visitor_repo import find_visitor_by_email_or_phone_any
    from schemas.visitor_schema import VisitorCreate

    consent = consent or {}
    # Defence-in-depth: reject when the active notice requires explicit consent
    # but none was granted (the frontend already gates this).
    await enforce_consent_if_required(
        tenant_id, consent_granted=consent.get("consent_granted")
    )

    # Phone is the visitor identity key — required system-wide.
    # Email is optional (tenants can flip it to required on their config,
    # which is enforced via ``required_field_keys`` below).
    if not phone:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="phone is required",
        )
    full_name_in = str(bio_data.get("full_name") or "").strip()
    if not full_name_in:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="full_name is required",
        )

    if id_file_bytes and not id_type:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="id_type is required when an ID file is uploaded",
        )

    await _enforce_tenant_geofence(
        tenant_id=tenant_id,
        visitor_lat=visitor_lat,
        visitor_lng=visitor_lng,
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
                email=email or "",
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
            if email and not existing.email:
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
    visitor_profile_id: Optional[str] = None
    try:
        visitor_profile_id = await _upsert_visitor_profile_from_submit(
            tenant_id=tenant_id,
            email=email,
            phone=phone,
            full_name=str(merged_bio_data.get("full_name") or visitor.full_name),
            company=merged_bio_data.get("company")
            or merged_bio_data.get("organization"),
            portrait_url=visitor.portrait_url,
            verified=visitor.verified,
            id_type=(id_type.value if id_type is not None else None),
        )
    except Exception as e:
        import logging

        logging.warning(f"Failed to upsert visitor profile from submit: {e}")

    # 3. Validate required fields against combined data.
    #
    # Phone, email, and full_name arrive as top-level form fields on
    # the multipart submit endpoints (not inside bio_data). Inject them
    # so a config that lists e.g. ``phone`` as a required field finds a
    # value to satisfy the requirement.
    if phone:
        merged_bio_data.setdefault("phone", phone)
    if email:
        merged_bio_data.setdefault("email", email)
    # ``email`` is system-optional regardless of what an older
    # CheckinConfig (provisioned under the v1 contract) says. Tenants
    # that need email capture should keep ``required=True`` on their
    # config for the *display* hint, but the kiosk submit never rejects
    # a missing email — the v2 contract makes phone the sole identity
    # key. ``full_name`` and ``phone`` remain mandatory.
    effective_required_keys = required_field_keys - {"email"}
    available_keys = set(merged_bio_data.keys()) | set(tenant_specific_data.keys())
    missing_fields = effective_required_keys - available_keys
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

    # 5. KYC routing decision.
    #
    # If the tenant's plan grants Dojah KYC and the provider is
    # configured, the check-in starts in PENDING_VERIFICATION — invisible to the
    # receptionist queue until the kiosk completes (or skips) the
    # widget. ``kyc_reference_id`` is a kiosk-supplied opt-in: if the
    # kiosk has already pre-run the widget before submit, we trust it
    # and short-circuit to PENDING_APPROVAL with verified=True. The
    # webhook still validates and persists the verification record
    # asynchronously.
    from services.kyc_service import kyc_available_for_tenant

    kyc_available, _kyc_required, _kyc_provider = await kyc_available_for_tenant(
        tenant_id
    )
    visitor_verified = visitor.verified
    if kyc_reference_id:
        # ``kyc_reference_id`` arrives as an unauthenticated form field on the
        # public kiosk endpoint, so it is a CLAIM, not evidence. Trusting it
        # outright meant anyone could post an arbitrary string and be stamped
        # verified without Dojah ever being involved. We now only honour a
        # reference that resolves to a real, successful, identity-matched
        # verification row belonging to THIS tenant. Anything else falls back
        # to the normal unverified route to the receptionist — the webhook can
        # still upgrade it later once it genuinely lands.
        initial_state = CheckinState.PENDING_APPROVAL
        visitor_verified = await _kyc_reference_is_verified(
            tenant_id=tenant_id, reference_id=kyc_reference_id
        )
        if not visitor_verified:
            logger.warning(
                "checkin submit: unverified kyc_reference_id claimed tenant=%s "
                "visitor=%s ref=%s — routing as UNVERIFIED",
                tenant_id,
                visitor_id,
                kyc_reference_id,
            )
    elif kyc_available:
        initial_state = CheckinState.PENDING_VERIFICATION
    else:
        initial_state = CheckinState.PENDING_APPROVAL
    logger.info(
        "checkin submit: kyc decision tenant=%s visitor=%s "
        "kyc_available=%s kyc_reference_present=%s initial_state=%s",
        tenant_id,
        visitor_id,
        kyc_available,
        bool(kyc_reference_id),
        initial_state.value,
    )

    # 5b. Per-branch new-visitor cap enforcement (WS0.2). Kiosk submit was
    # previously UNCAPPED — this is a deliberate behavior change, softened
    # by counting only new visitors. Resolve the branch first so the
    # is_new peek and the cap check both run BEFORE the check-in is
    # created; a new visitor at a branch at cap gets a 429 and nothing is
    # persisted.
    resolved_branch_id = await _resolve_checkin_branch_id(
        tenant_id, tenant_specific_data
    )
    is_new_visitor = True
    if visitor_profile_id and resolved_branch_id:
        from repositories.visitor_branch_first_repo import has_first_seen

        is_new_visitor = not await has_first_seen(
            tenant_id, resolved_branch_id, visitor_profile_id
        )
    resolved_plan = await _get_plan_data(tenant_id)
    await enforce_branch_visitor_cap(
        tenant_id,
        resolved_branch_id,
        resolved_plan,
        is_new_visitor=is_new_visitor,
        friendly_message=KIOSK_CAP_MESSAGE,
    )

    # Increment visit count only after cap enforcement passes, so a
    # 429-blocked submission never inflates the visitor's visit history.
    from bson import ObjectId

    if visitor_profile_id and ObjectId.is_valid(visitor_profile_id):
        from repositories.visitor_profile_repo import increment_visitor_profile_visits

        await increment_visitor_profile_visits({"_id": ObjectId(visitor_profile_id)})

    # 6. Create check-in
    create_data = CheckinCreate(
        tenant_id=tenant_id,
        visitor_id=visitor_id,
        checkin_config_id=checkin_config_id,
        id_extraction_id=id_extraction_id,
        tenant_specific_data=tenant_specific_data,
        branch_id=resolved_branch_id,
        purpose=purpose,
        state=initial_state,
        verified=visitor_verified,
    )
    checkin = await create_checkin(create_data)
    invalidate_tenant_dashboard_cache(tenant_id)

    # New-visitor first-seen ledger (WS0.3). Kiosk submit is the choke
    # point for both fresh-verification submits and the anti-spoof KYC
    # path — everything reaching this line has a persisted check-in.
    if visitor_profile_id and create_data.branch_id:
        if is_new_visitor:
            from repositories.visitor_branch_first_repo import record_first_seen

            await record_first_seen(
                tenant_id, create_data.branch_id, visitor_profile_id, int(time.time())
            )
    else:
        logger.warning(
            "visitor_branch_first ledger insert skipped tenant=%s checkin=%s "
            "visitor_profile_id=%s branch_id=%s",
            tenant_id,
            checkin.id,
            visitor_profile_id,
            create_data.branch_id,
        )

    # Persist the visitor's consent acceptance (fire-and-forget). The kiosk
    # submit path has no visit_sessions row, so consent lives in the dedicated
    # consent_records collection surfaced by GET /v1/compliance/consent-log.
    await record_visitor_consent(
        tenant_id=tenant_id,
        consent_granted=consent.get("consent_granted"),
        consent_method=consent.get("consent_method"),
        privacy_notice_id=consent.get("privacy_notice_id"),
        privacy_notice_version_id=consent.get("privacy_notice_version_id"),
        consent_accepted_at=consent.get("consent_accepted_at"),
        checkin_id=checkin.id,
        visitor_id=visitor_id,
        visitor_name_snapshot=visitor.full_name,
        department_id=tenant_specific_data.get("department_id"),
        client_ip=consent.get("client_ip"),
        user_agent=consent.get("user_agent"),
    )

    # If the visitor came in with a pre-run KYC reference, link the
    # verification record so the webhook lands on the correct check-in.
    if kyc_reference_id and checkin.id:
        try:
            from repositories.kyc_repo import (
                get_kyc_by_reference,
                update_kyc_verification,
            )
            from schemas.kyc_schema import KYCVerificationCreate
            from repositories.kyc_repo import create_kyc_verification

            kyc_existing = await get_kyc_by_reference(kyc_reference_id)
            if kyc_existing is None:
                await create_kyc_verification(
                    KYCVerificationCreate(
                        tenant_id=tenant_id,
                        checkin_id=checkin.id,
                        visitor_id=visitor_id,
                        provider="dojah",
                        reference_id=kyc_reference_id,
                        status=__import__(
                            "schemas.imports", fromlist=["KYCStatus"]
                        ).KYCStatus.ONGOING,
                    )
                )
            else:
                await update_kyc_verification(
                    {"reference_id": kyc_reference_id},
                    __import__(
                        "schemas.kyc_schema", fromlist=["KYCVerificationUpdate"]
                    ).KYCVerificationUpdate(),
                )
        except Exception:
            logger.warning(
                "kyc_reference_id link failed checkin=%s ref=%s",
                checkin.id,
                kyc_reference_id,
                exc_info=True,
            )

    # 7. Fire receptionist notification (only when the check-in is
    # actually in the queue — PENDING_VERIFICATION waits for KYC to complete /
    # be skipped before notifying).
    if initial_state == CheckinState.PENDING_APPROVAL:
        try:
            from services.notification_service import notify_checkin_pending_approval

            await notify_checkin_pending_approval(
                tenant_id=tenant_id,
                checkin_id=checkin.id or "",
                visitor_name=visitor.full_name,
                verified=visitor_verified,
                purpose=purpose.purpose,
                host_employee_id="",
            )
        except Exception as e:
            logger.warning("Failed to send checkin notification: %s", e)
    else:
        logger.info(
            "checkin notification: skipped pending approval notification "
            "tenant=%s checkin=%s initial_state=%s reason=awaiting_kyc",
            tenant_id,
            checkin.id,
            initial_state.value,
        )

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

    # Validate required fields. Email is system-optional in v2 — see
    # ``_submit_verified_checkin_core`` for the full rationale. The set
    # is merged with the active tenant_form (target=checkin) so the
    # form builder is the canonical source of truth.
    from services.checkin_config_service import resolve_required_fields_for_tenant

    merged_fields, _form = await resolve_required_fields_for_tenant(tenant_id)
    bio_data_keys = set(req.bio_data.keys())
    tenant_specific_keys = set(req.tenant_specific_data.keys())
    required_field_keys = {f.key for f in merged_fields if f.required} - {"email"}

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

    # KYC routing — see ``_submit_verified_checkin_core`` for the full
    # rationale. Tenants with KYC available park new check-ins in
    # PENDING_VERIFICATION until the kiosk completes / skips the widget.
    from services.kyc_service import kyc_available_for_tenant

    kyc_available, _kyc_required, _kyc_provider = await kyc_available_for_tenant(
        tenant_id
    )
    initial_state = (
        CheckinState.PENDING_VERIFICATION
        if kyc_available
        else CheckinState.PENDING_APPROVAL
    )
    logger.info(
        "legacy checkin submit: kyc decision tenant=%s visitor=%s "
        "kyc_available=%s initial_state=%s",
        tenant_id,
        visitor_id,
        kyc_available,
        initial_state.value,
    )

    # Upsert a VisitorProfile the same way the other submit paths do (this
    # legacy endpoint only touches the ``visitors`` collection above, so
    # without this the visitor has no visitor_profile_id and is invisible
    # to the first-seen ledger / cap enforcement below). Resolved BEFORE
    # the check-in is created so cap enforcement can run first — never
    # blocks the check-in on failure (best-effort upsert).
    visitor_profile_id: Optional[str] = None
    try:
        visitor_profile_id = await _upsert_visitor_profile_from_submit(
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
        logger.warning(f"Failed to upsert visitor profile from legacy submit: {e}")

    # Per-branch new-visitor cap enforcement (WS0.2). Same choke point as
    # the other kiosk submit paths — resolve branch + is_new BEFORE
    # creating the check-in.
    resolved_branch_id = await _resolve_checkin_branch_id(
        tenant_id, req.tenant_specific_data
    )
    is_new_visitor = True
    if visitor_profile_id and resolved_branch_id:
        from repositories.visitor_branch_first_repo import has_first_seen

        is_new_visitor = not await has_first_seen(
            tenant_id, resolved_branch_id, visitor_profile_id
        )
    resolved_plan = await _get_plan_data(tenant_id)
    await enforce_branch_visitor_cap(
        tenant_id,
        resolved_branch_id,
        resolved_plan,
        is_new_visitor=is_new_visitor,
        friendly_message=KIOSK_CAP_MESSAGE,
    )

    # Increment visit count only after cap enforcement passes, so a
    # 429-blocked submission never inflates the visitor's visit history.
    from bson import ObjectId

    if visitor_profile_id and ObjectId.is_valid(visitor_profile_id):
        from repositories.visitor_profile_repo import increment_visitor_profile_visits

        await increment_visitor_profile_visits({"_id": ObjectId(visitor_profile_id)})

    # Create checkin
    create_data = CheckinCreate(
        tenant_id=tenant_id,
        visitor_id=visitor_id,
        checkin_config_id=checkin_config_id,
        id_extraction_id=req.id_extraction_id,
        tenant_specific_data=req.tenant_specific_data,
        branch_id=resolved_branch_id,
        purpose=req.purpose,
        state=initial_state,
        verified=visitor.verified,
    )
    checkin = await create_checkin(create_data)
    invalidate_tenant_dashboard_cache(tenant_id)

    # New-visitor first-seen ledger (WS0.3). This is the third and last
    # kiosk check-in creation path (``/checkin-configs/{id}/checkins``) —
    # see checkin_service create_checkin call-site audit in the WS0.3
    # report for the full list.
    if visitor_profile_id and create_data.branch_id:
        if is_new_visitor:
            from repositories.visitor_branch_first_repo import record_first_seen

            await record_first_seen(
                tenant_id, create_data.branch_id, visitor_profile_id, int(time.time())
            )
    else:
        logger.warning(
            "visitor_branch_first ledger insert skipped tenant=%s checkin=%s "
            "visitor_profile_id=%s branch_id=%s",
            tenant_id,
            checkin.id,
            visitor_profile_id,
            create_data.branch_id,
        )

    # Notify approvers only when the check-in is queue-visible.
    if initial_state == CheckinState.PENDING_APPROVAL:
        try:
            from services.notification_service import notify_checkin_pending_approval

            await notify_checkin_pending_approval(
                tenant_id=tenant_id,
                checkin_id=checkin.id or "",
                visitor_name=visitor.full_name,
                verified=visitor.verified,
                purpose=req.purpose.purpose,
                host_employee_id="",
            )
        except Exception as e:
            logger.warning("Failed to send checkin notification: %s", e)
    else:
        logger.info(
            "checkin notification: skipped pending approval notification "
            "tenant=%s checkin=%s initial_state=%s reason=awaiting_kyc",
            tenant_id,
            checkin.id,
            initial_state.value,
        )

    return checkin


async def list_checkins_for_tenant(
    tenant_id: str,
    state: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    branch_filter: Optional[dict] = None,
) -> tuple[list[CheckinWithVisitorOut], int]:
    """List check-ins for a tenant with optional state filter. Each row is
    enriched with a ``VisitorBriefSummary`` so the approval UI can show who
    the visitor is without a second request.

    ``branch_filter`` (from ``branch_service.branch_scope_filter``) scopes the
    query to a branch-scoped caller's branches; ``None`` for unscoped roles.
    """
    filter_dict: dict = {"tenant_id": tenant_id}
    if state:
        filter_dict["state"] = state
    if branch_filter:
        filter_dict.update(branch_filter)

    checkins = await get_checkins(filter_dict, skip=skip, limit=limit)
    total = await count_checkins(filter_dict)
    enriched = await _enrich_checkins_with_visitors(tenant_id, checkins)
    return enriched, total


def _resolve_storage_url(object_key: Optional[str]) -> Optional[str]:
    """Best-effort presigned URL for ``object_key``. None if storage is
    not configured or generation fails — never raises."""
    from services.storage_url_service import try_resolve_download_url

    return try_resolve_download_url(object_key)


async def list_pending_approvals_for_tenant(
    tenant_id: str,
    *,
    skip: int = 0,
    limit: int = 50,
    include_appointments: bool = True,
    branch_filter: Optional[dict] = None,
) -> tuple[list, int]:
    """Unified approval queue: kiosk check-ins awaiting approval +
    SCHEDULED appointments the host pre-vetted.

    Returns a list of ``PendingApprovalItem`` (declared in
    ``schemas/checkin_schema.py``). Each row carries a ``source_type``
    discriminator so the frontend knows which action endpoint to call:

    * ``checkin``     → ``POST /v1/checkins/{id}/confirm``
    * ``appointment`` → ``POST /v1/appointments/{id}/check-in``

    Appointment rows always report ``state="scheduled"`` and
    ``verified=true`` (the host pre-vetted them when scheduling).
    Pagination is applied to the merged, sorted result so the caller
    sees a stable ``limit``-bounded page."""
    from repositories.appointment_repo import (
        count_appointments,
        get_appointments,
    )
    from repositories.visitor_profile_repo import get_visitor_profiles
    from schemas.checkin_schema import PendingApprovalItem
    from schemas.imports import AppointmentStatus
    from schemas.summary_schema import VisitorBriefSummary

    # Include pending_verification rows so KYC-parked check-ins are visible to
    # the receptionist queue. Without this, a stalled or never-started KYC
    # flow leaves the visitor invisible until someone explicitly skips KYC or
    # a webhook lands. The frontend distinguishes the two states via the
    # ``state`` field on each row and renders a "KYC in progress" badge for
    # pending_verification.
    pending_filter: dict = {
        "tenant_id": tenant_id,
        "state": {"$in": ["pending_approval", "pending_verification"]},
    }
    if branch_filter:
        pending_filter.update(branch_filter)
    pending_checkins = await get_checkins(pending_filter, skip=0, limit=200)
    enriched_checkins = await _enrich_checkins_with_visitors(
        tenant_id, pending_checkins
    )
    pending_total = await count_checkins(pending_filter)

    rows: list[PendingApprovalItem] = []
    for c in enriched_checkins:
        visitor_summary = c.visitor
        rows.append(
            PendingApprovalItem(
                id=c.id or "",
                source_type="checkin",
                tenant_id=c.tenant_id,
                state=str(c.state.value if hasattr(c.state, "value") else c.state),
                verified=bool(c.verified),
                visitor_name=visitor_summary.full_name if visitor_summary else None,
                company=visitor_summary.company if visitor_summary else None,
                purpose=c.purpose.purpose if c.purpose else None,
                expected_duration_minutes=(
                    c.purpose.expected_duration_minutes if c.purpose else None
                ),
                photo_url=visitor_summary.portrait_url if visitor_summary else None,
                department_id=None,
                host_id=None,
                scheduled_datetime=None,
                created_at=c.date_created or 0,
                visitor=visitor_summary,
                appointment_id=None,
                checkin_id=c.id,
                # Carries the WHY behind ``verified`` — built in
                # ``_enrich_checkins_with_visitors``, never None for checkin rows.
                identity_check=c.identity_check,
            )
        )

    appointment_total = 0
    if include_appointments:
        appt_filter: dict = {
            "tenant_id": tenant_id,
            "status": AppointmentStatus.SCHEDULED.value,
        }
        if branch_filter:
            appt_filter.update(branch_filter)
        appointments = await get_appointments(
            filter_dict=appt_filter, start=0, stop=200
        )
        appointment_total = await count_appointments(appt_filter)

        # Hydrate visitor names/photos in one batch to keep the queue cheap.
        profile_ids = {
            a.visitor_profile_id for a in appointments if a.visitor_profile_id
        }
        profile_by_id: dict[str, Any] = {}
        if profile_ids:
            from bson import ObjectId

            obj_ids = []
            for raw in profile_ids:
                try:
                    obj_ids.append(ObjectId(raw))
                except Exception:
                    continue
            if obj_ids:
                profiles = await get_visitor_profiles(
                    {"tenant_id": tenant_id, "_id": {"$in": obj_ids}},
                    start=0,
                    stop=len(obj_ids),
                )
                profile_by_id = {p.id: p for p in profiles if p.id}

        for a in appointments:
            profile = (
                profile_by_id.get(a.visitor_profile_id)
                if a.visitor_profile_id
                else None
            )
            visitor_name = (
                (profile.full_name if profile else None)
                or a.visitor_name_snapshot
                or "Scheduled visitor"
            )
            company = profile.company if profile else None
            photo_key = a.expected_visitor_photo_object_key or (
                profile.photo_object_key if profile else None
            )
            photo_url = _resolve_storage_url(photo_key)

            visitor_summary = (
                VisitorBriefSummary(
                    id=profile.id or "",
                    full_name=profile.full_name,
                    email=profile.email_address,
                    phone=profile.phone,
                    company=profile.company,
                    verified=True,
                    verification_method=profile.verification_method,
                    portrait_url=photo_url,
                )
                if profile is not None
                else VisitorBriefSummary(
                    id="",
                    full_name=visitor_name,
                    verified=True,
                    portrait_url=photo_url,
                )
            )

            rows.append(
                PendingApprovalItem(
                    id=a.id or "",
                    source_type="appointment",
                    tenant_id=a.tenant_id,
                    state="scheduled",
                    verified=True,
                    visitor_name=visitor_name,
                    company=company,
                    purpose=a.purpose,
                    expected_duration_minutes=None,
                    photo_url=photo_url,
                    department_id=a.department_id,
                    host_id=a.host_id,
                    scheduled_datetime=a.scheduled_datetime,
                    created_at=a.date_created or a.scheduled_datetime or 0,
                    visitor=visitor_summary,
                    appointment_id=a.id,
                    checkin_id=None,
                    # Appointment rows are trusted because a host vetted the
                    # visitor ahead of time — say that, rather than showing a
                    # bare "Verified" the receptionist can't account for.
                    identity_check=IdentityCheckSummary(
                        verified=True,
                        status="host_approved",
                        headline="Host-approved",
                        reason=(
                            "This visitor was pre-approved by their host when the "
                            "appointment was booked — no ID check was run."
                        ),
                    ),
                )
            )

    # Sort: scheduled appointments by scheduled time (soonest first),
    # checkins by creation time (oldest first — they've been waiting).
    rows.sort(
        key=lambda r: (
            r.source_type != "appointment",  # appointments first
            r.scheduled_datetime or r.created_at,
        )
    )

    total = pending_total + appointment_total
    paged = rows[skip : skip + limit]
    return paged, total


async def get_checkin_detail(tenant_id: str, checkin_id: str) -> CheckinWithVisitorOut:
    """Get check-in detail with tenant validation. Enriched with the
    visitor snapshot so the approver sees the visitor's name + contact +
    verification state."""
    checkin = await get_checkin({"_id": checkin_id, "tenant_id": tenant_id})
    if not checkin:
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)
    enriched = await _enrich_checkins_with_visitors(tenant_id, [checkin])
    return enriched[0]


async def _send_visitor_badge_email_if_enabled(
    *,
    tenant_id: str,
    visitor,
    badge,
    checkin_id: str,
) -> None:
    """Issue 7: dispatch the visitor's badge email after approval.

    All three of these must be true or the email is skipped silently:

      1. Tenant settings ``send_visitor_badge_email`` is ``True``.
      2. The visitor record has a non-empty email address.
      3. A badge was actually generated for this check-in (Free plan
         skips badge generation entirely, so we'd have nothing useful
         to link).

    Fire-and-forget — the approval workflow MUST NOT block on email
    delivery, and a missing recipient address MUST NOT fail the
    approval. Exceptions are logged at WARNING; failures don't bubble.

    Uses the queue-aware dispatch (``dispatch="auto"``) so production
    respects ``EMAIL_QUEUE_ENABLED`` and local dev sends synchronously
    without any extra plumbing.
    """
    if badge is None:
        return
    if not visitor or not getattr(visitor, "email", None):
        return

    try:
        from repositories.tenant_settings_repo import get_tenant_settings

        tenant_settings = await get_tenant_settings({"tenant_id": tenant_id})
        # `send_visitor_badge_email` defaults to False at the schema
        # level; respect that — tenants must opt in.
        if not tenant_settings or not getattr(
            tenant_settings, "send_visitor_badge_email", False
        ):
            return
    except Exception:
        # If we can't read tenant settings we err on the side of NOT
        # sending — a tenant that hasn't configured email shouldn't
        # leak addresses just because the read path is flaky.
        return

    # Look up the tenant for branding-friendly copy in the subject.
    tenant_name = "VisiChek"
    try:
        from repositories.tenant_repo import get_tenant
        from bson import ObjectId

        if ObjectId.is_valid(tenant_id):
            tenant = await get_tenant({"_id": ObjectId(tenant_id)})
            if tenant and getattr(tenant, "company_name", None):
                tenant_name = tenant.company_name
    except Exception:
        pass

    # Badge PDFs are rendered by the frontend from the badge token +
    # session snapshots — the backend no longer stores a PDF, so
    # ``badge_url`` is always empty. The template hides the download
    # button when empty and leans on the QR / printable-badge-page
    # link instead.
    badge_url = ""

    # Public printable-badge page URL — the frontend hosts
    # ``/badge/{token}`` and resolves the token via
    # ``GET /v1/public/badge/{token}``. The token here is the badge's
    # ``qr_code_value`` (random opaque value minted by this approval
    # flow); the public endpoint accepts both that and the kiosk-flow
    # signed token. Empty string when ``APP_BASE_URL`` is unset — the
    # template hides the button rather than emitting a broken link.
    from core.settings import get_settings

    badge_qr_token = getattr(badge, "qr_code_value", "") or ""
    badge_page_url = ""
    if badge_qr_token:
        base = (get_settings().app_base_url or "").rstrip("/")
        if base:
            badge_page_url = f"{base}/badge/{badge_qr_token}"

    expires_at_iso = ""
    if getattr(badge, "expires_at", None):
        from datetime import datetime, timezone

        try:
            expires_at_iso = (
                datetime.fromtimestamp(badge.expires_at, tz=timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                + "Z"
            )
        except Exception:
            expires_at_iso = ""

    from core.email.manager import EmailManager
    from core.email.types import EmailDispatchRequest
    from services.branding_service import get_email_branding_context

    # Visitor-facing email → carry the tenant's brand (logo + accent) so the
    # badge notice looks like it came from the host org, not VisiChek.
    email_brand = await get_email_branding_context(tenant_id)

    manager = EmailManager.get_instance()
    await manager.send_template(
        EmailDispatchRequest(
            to_email=visitor.email,
            template_key="visitor_badge_approved",
            context={
                "visitor_name": getattr(visitor, "full_name", None) or "there",
                "tenant_name": tenant_name,
                "host_name": "",  # TODO: resolve from checkin context
                "department_name": "",  # TODO: resolve from checkin context
                "badge_url": badge_url,
                "badge_page_url": badge_page_url,
                "badge_qr_token": badge_qr_token,
                "expires_at_formatted": expires_at_iso,
                "checkin_id": checkin_id,
                **email_brand,
            },
            dispatch="auto",
        )
    )


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

        now = int(time.time())
        # Set expires_at to end of tenant's local day (for now use UTC end-of-day)
        expires_at = ((now // 86400) + 1) * 86400  # Next midnight UTC

        # Plan gate — badge printing is denied on Free. Manual check-in
        # still goes through (we transition the checkin to APPROVED
        # below) but we skip the badge artifact entirely so the Free
        # tenant gets visitor logging without paid-tier hardware.
        from services.plan_limits import is_feature_enabled

        badge = None
        if await is_feature_enabled(
            tenant_id=tenant_id,
            endpoint_pattern="/v1/badges",
            method="POST",
        ):
            qr_code_value = secrets.token_urlsafe(24)
            badge_create = BadgeCreate(
                tenant_id=tenant_id,
                checkin_id=checkin_id,
                qr_code_value=qr_code_value,
                issued_at=now,
                expires_at=expires_at,
            )
            badge = await create_badge(badge_create)

        # Update checkin — persist the approver's internal note so it can
        # be shown on the check-in detail view and in the audit trail.
        clean_notes = (req.notes or "").strip()[:500] or None
        await update_checkin(
            checkin_id,
            CheckinUpdate(
                state=CheckinState.APPROVED,
                approved_by_user_id=principal.user_id,
                approved_at=now,
                approval_notes=clean_notes,
            ),
        )
        invalidate_tenant_dashboard_cache(tenant_id)

        # Sync path (bypasses the queued-write auto-audit) — record directly.
        from services.audit_service import record_audit_event

        await record_audit_event(
            actor_id=principal.user_id,
            actor_role=principal.role,
            action="checkin.approved",
            resource_type="checkin",
            resource_id=checkin_id,
            tenant_id=tenant_id,
            details={
                "visitor_name": visitor.full_name,
                **({"notes": clean_notes} if clean_notes else {}),
            },
        )

        # Fire notification
        try:
            from services.notification_service import notify_checkin_approved

            await notify_checkin_approved(
                tenant_id=tenant_id,
                checkin_id=checkin_id,
                badge_id=(badge.id if badge else None) or "",
                visitor_name=visitor.full_name,
                host_employee_id="",  # TODO: extract from context if available
                approved_by_user_id=principal.user_id,
            )
        except Exception as e:
            import logging

            logging.warning(f"Failed to send approval notification: {e}")

        # Issue 7: send the visitor a "your badge is ready" email when
        # the tenant has enabled it, the visitor supplied an address,
        # and a badge artifact was actually generated. All three gates
        # MUST pass — without the badge there's nothing useful to
        # link, and without the address we'd just toast a 400.
        try:
            await _send_visitor_badge_email_if_enabled(
                tenant_id=tenant_id,
                visitor=visitor,
                badge=badge,
                checkin_id=checkin_id,
            )
        except Exception as e:
            import logging

            logging.warning(f"Failed to dispatch visitor badge email: {e}")

        # Build response. On Free plan ``badge`` is None — return the
        # approval without a badge artifact so the receptionist UI can
        # render "approved, manual entry only".
        badge_payload: BadgePayload | None = None
        if badge is not None:
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
        invalidate_tenant_dashboard_cache(tenant_id)

        # Sync path (bypasses the queued-write auto-audit) — record directly.
        from services.audit_service import record_audit_event

        await record_audit_event(
            actor_id=principal.user_id,
            actor_role=principal.role,
            action="checkin.rejected",
            resource_type="checkin",
            resource_id=checkin_id,
            tenant_id=tenant_id,
            details={"reason": req.notes or "No reason provided"},
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


async def force_approve_pending_verification(
    checkin_id: str,
    *,
    actor_id: str,
    actor_role: str,
    request_id: Optional[str] = None,
) -> CheckinOut:
    """Manually unstick a check-in that's parked in PENDING_VERIFICATION.

    Used when a KYC widget never started, never completed, or its webhook
    failed to land — the check-in would otherwise stay invisible to the
    receptionist queue forever. Transitions to PENDING_APPROVAL so a
    receptionist can approve normally; does NOT skip the verification
    record on file (any KYC row keeps its current status). Records an
    audit event with the prior state so we can answer "who unstuck what
    and when?" later.
    """
    checkin = await get_checkin({"_id": checkin_id})
    if not checkin:
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)

    current_state = (
        checkin.state.value if hasattr(checkin.state, "value") else str(checkin.state)
    )
    if current_state != CheckinState.PENDING_VERIFICATION.value:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message=(
                f"Cannot force-approve check-in in state '{current_state}'. "
                "Only check-ins parked in 'pending_verification' are eligible."
            ),
        )

    updated = await update_checkin(
        checkin_id, CheckinUpdate(state=CheckinState.PENDING_APPROVAL)
    )
    invalidate_tenant_dashboard_cache(checkin.tenant_id)

    from services.audit_service import record_audit_event

    await record_audit_event(
        actor_id=actor_id,
        actor_role=actor_role,
        action="checkin.force_approved_pending",
        resource_type="checkin",
        resource_id=checkin_id,
        tenant_id=checkin.tenant_id,
        details={
            "from_state": current_state,
            "to_state": CheckinState.PENDING_APPROVAL.value,
            "reason": "manual_unstick",
        },
        request_id=request_id,
    )

    try:
        from repositories.visitor_repo import get_visitor
        from services.notification_service import notify_checkin_pending_approval

        visitor = await get_visitor({"_id": checkin.visitor_id})
        await notify_checkin_pending_approval(
            tenant_id=checkin.tenant_id,
            checkin_id=checkin_id,
            visitor_name=visitor.full_name if visitor else "Visitor",
            verified=bool(checkin.verified),
            purpose=checkin.purpose.purpose if checkin.purpose else "",
            host_employee_id="",
        )
    except Exception:
        logger.warning(
            "force_approve_pending_verification: notify failed checkin=%s",
            checkin_id,
            exc_info=True,
        )

    return updated


async def _resolve_verifier_identity(
    user_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """Best-effort (full_name, role) for the verifying system user.

    The auth principal only carries the user id + role, so the display
    name is fetched from the ``system_users`` collection. Returns
    ``(None, None)`` rather than raising if the lookup fails — a missing
    name must not block a legitimate verification."""
    from bson import ObjectId

    if not ObjectId.is_valid(user_id):
        return None, None
    try:
        from repositories.system_user_repo import get_system_user

        user = await get_system_user({"_id": ObjectId(user_id)})
    except Exception:
        return None, None
    if user is None:
        return None, None
    role = user.role.value if hasattr(user.role, "value") else (user.role or None)
    return user.full_name, role


async def manually_verify_checkin(
    checkin_id: str,
    principal,
    *,
    notes: Optional[str] = None,
    request_id: Optional[str] = None,
) -> CheckinWithVisitorOut:
    """Staff-vouched ("manual") verification of a check-in's visitor.

    Reception checks a physical ID by hand (walk-in, scan skipped, OCR
    failed) and flips ``verified=True`` on BOTH the check-in and the linked
    visitor, stamping a point-in-time attribution block (who vouched, their
    role, when) taken from the authenticated session — never from the body.

    Verification and approval are independent axes: this does NOT change the
    check-in's ``state`` and never auto-approves. Returns the enriched
    check-in so the receptionist UI can render the attribution line
    immediately."""
    tenant_id = principal.tenant_id or ""

    # Tenant isolation: scope the lookup to the caller's tenant so a
    # cross-tenant id leaks nothing — a miss is a 404, not a 403.
    checkin = await get_checkin({"_id": checkin_id, "tenant_id": tenant_id})
    if not checkin:
        raise resource_not_found(resource="Checkin", resource_id=checkin_id)

    # Guard the double-submit race — the frontend hides the Verify button
    # once verified, but two tabs / a slow network could still race.
    if bool(checkin.verified):
        raise AppException(
            status_code=409,
            code=ErrorCode.CONFLICT,
            message="Check-in is already verified.",
            details={"checkin_id": checkin_id},
        )

    now = int(time.time())
    verifier_name, verifier_role = await _resolve_verifier_identity(principal.user_id)
    clean_notes = (notes or "").strip() or None

    info = ManualVerificationInfo(
        manual=True,
        verified_by_user_id=principal.user_id,
        verified_by_name=verifier_name,
        verified_by_role=verifier_role or principal.role,
        verified_at=now,
        method=VerificationMethod.MANUAL.value,
        notes=clean_notes,
    )

    # 1. Flip the check-in. state is left untouched on purpose.
    updated = await update_checkin(
        checkin_id,
        CheckinUpdate(
            verified=True,
            verification_method=VerificationMethod.MANUAL,
            manual_verification=info,
        ),
    )

    # 2. Flip the linked visitor profile so the same attribution shows up
    #    on every read of the visitor (live-joined onto check-in rows).
    from repositories.visitor_repo import get_visitor, update_visitor
    from schemas.visitor_schema import VisitorUpdate

    visitor = await get_visitor({"_id": checkin.visitor_id, "tenant_id": tenant_id})
    if visitor is not None and visitor.id:
        await update_visitor(
            visitor.id,
            VisitorUpdate(verified=True, manual_verification=info),
        )

    invalidate_tenant_dashboard_cache(tenant_id)

    # 3. Audit — identity/PII mutation, reportable under NDPA.
    try:
        from services.audit_service import record_audit_event

        await record_audit_event(
            actor_id=principal.user_id,
            actor_role=principal.role,
            action="checkin.manual_verify",
            resource_type="checkin",
            resource_id=checkin_id,
            tenant_id=tenant_id,
            details={
                "visitor_id": checkin.visitor_id,
                "verified_by_name": verifier_name,
                "verified_by_role": verifier_role or principal.role,
                "notes": clean_notes,
            },
            request_id=request_id,
        )
    except Exception:
        logger.warning(
            "checkin.manual_verify audit record failed for checkin %s",
            checkin_id,
            exc_info=True,
        )

    enriched = await _enrich_checkins_with_visitors(tenant_id, [updated])
    return enriched[0]


async def list_checkins_analytics(
    tenant_id: str,
    state: Optional[str] = None,
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[CheckinWithVisitorOut], int]:
    """List check-ins for analytics with date range filtering. Enriched
    with visitor snapshots so the analytics screens can render visitor
    details without a second batch lookup."""
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
    enriched = await _enrich_checkins_with_visitors(tenant_id, checkins)
    return enriched, total
