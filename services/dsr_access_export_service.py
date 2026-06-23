"""Right-of-Access (DSR) fulfilment: gather a visitor's data, package it as a
ZIP of CSVs, store it, and email the subject a time-limited download link.

This is the engine behind ``POST /v1/dsr/{dsr_id}/fulfil-access``. The route
enforces the identity-verification gate synchronously, then enqueues the
``dsr.fulfil_access`` writer, which calls :func:`run_access_fulfilment` here.

Design notes:
* The email transport has NO attachment support, so the subject receives a
  presigned download link (7-day TTL) rather than the ZIP itself.
* CSV cells are written through :func:`core.csv_export.csv_buffer`, which
  escapes formula-injection prefixes (CWE-1236) per cell — we never reuse the
  unsafe ``compliance_service._records_to_csv``.
* Each category is gathered best-effort and scoped by ``tenant_id`` (IDOR
  defence). Field allowlists keep internal-only keys (ID image keys, raw IPs,
  badge tokens) out of a subject-facing package.
* If the subject has no email on file the job RAISES — the DSR is NOT marked
  completed, so the DPO learns nothing was sent (surfaced via the job poll).
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from typing import Any, Dict, List
from uuid import uuid4

from bson import ObjectId

from core.csv_export import csv_buffer
from core.email.types import EmailDispatchRequest
from core.errors import AppException, ErrorCode
from core.storage.manager import DocumentStorageManager
from schemas.data_subject_request_schema import DSRUpdate
from schemas.imports import DSRStatus
from services.storage_url_service import resolve_download_url

logger = logging.getLogger(__name__)

# Secure download link lifetime for the access package. Long enough for the
# subject to retrieve their data, short enough that a leaked URL expires.
ACCESS_EXPORT_TTL_SECONDS = 7 * 24 * 3600

# Per-category field allowlists. Deliberately EXCLUDE internal-only / sensitive
# keys (id_image_object_key, photo_object_key, client_ip, user_agent,
# badge_qr_token, actor ids) from a subject-facing export.
_PROFILE_COLS = [
    "id",
    "full_name",
    "phone",
    "email_address",
    "company",
    "id_type",
    "verification_status",
    "verification_method",
    "profiling_preference",
    "date_created",
    "last_visit_date",
    "total_visits",
]
_VISIT_COLS = [
    "id",
    "status",
    "visitor_name_snapshot",
    "company_snapshot",
    "purpose",
    "check_in_time",
    "check_out_time",
    "verification_status",
    "verification_method",
    "consent_granted",
    "consent_method",
    "consent_timestamp",
    "department_id",
]
_APPT_COLS = ["id", "purpose", "status", "scheduled_datetime", "department_id"]
_CONSENT_COLS = [
    "id",
    "consent_granted",
    "consent_method",
    "consent_timestamp",
    "privacy_notice_version_id",
    "lawful_basis_at_time",
    "consent_withdrawal_at",
]
_AUDIT_COLS = ["id", "action", "resource_type", "timestamp"]
_DELETION_COLS = ["id", "entity_type", "reason", "action", "timestamp"]
_DSR_COLS = ["id", "request_type", "status", "received_at", "resolved_at", "resolution"]

# (category key, output filename, column allowlist, human description)
_SECTIONS: list[tuple[str, str, list[str], str]] = [
    ("profile", "profile.csv", _PROFILE_COLS, "Your visitor profile record."),
    (
        "visit_sessions",
        "visit_sessions.csv",
        _VISIT_COLS,
        "Every check-in / check-out we recorded for you.",
    ),
    ("appointments", "appointments.csv", _APPT_COLS, "Appointments scheduled for you."),
    (
        "consent_records",
        "consent_records.csv",
        _CONSENT_COLS,
        "Consent you granted or withdrew at check-in.",
    ),
    ("audit_trail", "activity_log.csv", _AUDIT_COLS, "Actions taken on your data."),
    (
        "deletion_logs",
        "deletion_log.csv",
        _DELETION_COLS,
        "Records of data scheduled for or removed by erasure.",
    ),
    (
        "data_subject_requests",
        "requests_history.csv",
        _DSR_COLS,
        "Your data-subject requests and their outcomes.",
    ),
]


def _dump(obj: Any) -> Dict[str, Any]:
    """Normalise a repo result (model or dict) to a plain dict with an ``id``."""
    if hasattr(obj, "model_dump"):
        d = obj.model_dump(mode="json", by_alias=False)
    else:
        d = dict(obj)
    if not d.get("id") and d.get("_id"):
        d["id"] = d["_id"]
    return d


async def gather_visitor_data(
    tenant_id: str, visitor_profile_id: str
) -> Dict[str, List[Dict[str, Any]]]:
    """Collect every record we hold for ``visitor_profile_id`` across collections.

    Each category is fetched in its own ``try`` so one failing collection can't
    sink the whole export. All queries are tenant-scoped (IDOR defence).
    """
    oid_valid = ObjectId.is_valid(visitor_profile_id)
    out: Dict[str, List[Dict[str, Any]]] = {key: [] for key, _, _, _ in _SECTIONS}
    if not oid_valid:
        return out

    # Profile -------------------------------------------------------------
    try:
        from repositories.visitor_profile_repo import get_visitor_profile

        profile = await get_visitor_profile(
            {"_id": ObjectId(visitor_profile_id), "tenant_id": tenant_id}
        )
        out["profile"] = [_dump(profile)] if profile else []
    except Exception:
        logger.warning("dsr access export: profile gather failed", exc_info=True)

    # Visit sessions ------------------------------------------------------
    try:
        from repositories.visit_session_repo import get_visit_sessions

        sessions = await get_visit_sessions(
            {"tenant_id": tenant_id, "visitor_profile_id": visitor_profile_id},
            0,
            10000,
        )
        out["visit_sessions"] = [_dump(r) for r in sessions]
    except Exception:
        logger.warning("dsr access export: visit_sessions gather failed", exc_info=True)

    # Appointments --------------------------------------------------------
    try:
        from repositories.appointment_repo import get_appointments

        appts = await get_appointments(
            {"tenant_id": tenant_id, "visitor_profile_id": visitor_profile_id},
            0,
            10000,
        )
        out["appointments"] = [_dump(r) for r in appts]
    except Exception:
        logger.warning("dsr access export: appointments gather failed", exc_info=True)

    # Consent records (keyed by visitor_id) -------------------------------
    try:
        from repositories.consent_record_repo import get_consent_records

        consents = await get_consent_records(
            {"tenant_id": tenant_id, "visitor_id": visitor_profile_id},
            skip=0,
            limit=10000,
        )
        out["consent_records"] = [_dump(r) for r in consents]
    except Exception:
        logger.warning("dsr access export: consent gather failed", exc_info=True)

    # Audit trail (resource pointing at this profile) ---------------------
    try:
        from repositories.audit_log_repo import get_audit_logs

        audits = await get_audit_logs(
            {
                "tenant_id": tenant_id,
                "resource_type": "visitor_profile",
                "resource_id": visitor_profile_id,
            },
            0,
            10000,
        )
        out["audit_trail"] = [_dump(r) for r in audits]
    except Exception:
        logger.warning("dsr access export: audit gather failed", exc_info=True)

    # Deletion logs -------------------------------------------------------
    try:
        from repositories.deletion_log_repo import get_deletion_logs

        deletions = await get_deletion_logs(
            {
                "tenant_id": tenant_id,
                "entity_type": "visitor_profile",
                "entity_id": visitor_profile_id,
            },
            0,
            10000,
        )
        out["deletion_logs"] = [_dump(r) for r in deletions]
    except Exception:
        logger.warning("dsr access export: deletion gather failed", exc_info=True)

    # DSR history ---------------------------------------------------------
    try:
        from repositories.data_subject_request_repo import get_dsrs

        dsr_rows = await get_dsrs(
            {"tenant_id": tenant_id, "visitor_profile_id": visitor_profile_id},
            0,
            10000,
        )
        out["data_subject_requests"] = [_dump(r) for r in dsr_rows]
    except Exception:
        logger.warning("dsr access export: dsr history gather failed", exc_info=True)

    return out


def _section_csv(records: List[Dict[str, Any]], cols: List[str]) -> bytes:
    rows: List[List[Any]] = [list(cols)]
    for rec in records:
        rows.append([rec.get(c) for c in cols])
    return csv_buffer(rows)


def build_access_zip(
    data: Dict[str, List[Dict[str, Any]]],
    *,
    dsr_id: str,
    visitor_profile_id: str,
    generated_at: int,
) -> bytes:
    """Render the gathered data into a ZIP: one CSV per category + README."""
    buffer = io.BytesIO()
    readme_lines = [
        "Subject Access Request — data export",
        "=" * 38,
        "",
        f"Data subject (visitor profile) id: {visitor_profile_id}",
        f"Request id: {dsr_id}",
        f"Generated at (unix seconds): {generated_at}",
        "",
        "This package contains the personal data we hold about you, one CSV "
        "file per category. Timestamps are Unix epoch seconds.",
        "",
        "Files in this package:",
    ]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for key, filename, cols, description in _SECTIONS:
            records = data.get(key, []) or []
            zf.writestr(filename, _section_csv(records, cols))
            readme_lines.append(
                f"  - {filename} ({len(records)} row(s)) — {description}"
            )
        zf.writestr("README.txt", "\n".join(readme_lines) + "\n")
    buffer.seek(0)
    return buffer.getvalue()


async def _resolve_tenant_name(tenant_id: str) -> str:
    try:
        from services.tenant_service import retrieve_tenant_by_id

        tenant = await retrieve_tenant_by_id(tenant_id)
        return getattr(tenant, "company_name", None) or "VisiChek"
    except Exception:
        return "VisiChek"


async def run_access_fulfilment(dsr_id: str, tenant_id: str) -> Dict[str, Any]:
    """Gather → package → store → presign → email → stamp the DSR completed.

    Raises ``AppException`` (re-raised by the dispatcher as RuntimeError so the
    job is marked failed) when identity is unverified, the profile is missing,
    or the subject has no email — in those cases the DSR stays open.
    """
    from services.data_subject_request_service import (
        retrieve_dsr_by_id,
        update_dsr_by_id,
    )

    dsr = await retrieve_dsr_by_id(dsr_id=dsr_id, tenant_id=tenant_id)
    # Defence in depth: the route already gated this, but time has passed and
    # the worker runs out of process.
    if not dsr.identity_verified:
        raise AppException(
            status_code=409,
            code=ErrorCode.VALIDATION_FAILED,
            message="Identity must be verified before fulfilling an access request",
            details={"code": "DSR_IDENTITY_NOT_VERIFIED"},
        )

    visitor_profile_id = dsr.visitor_profile_id
    data = await gather_visitor_data(tenant_id, visitor_profile_id)

    profile_rows = data.get("profile") or []
    if not profile_rows:
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="Visitor profile not found for this request",
            details={"code": "DSR_ACCESS_NO_PROFILE"},
        )
    # Deliver to BOTH the requester email captured on the request and the
    # linked visitor profile's email (deduplicated, requester-first). At least
    # one valid address is required, otherwise there's nobody to send to.
    profile_email = profile_rows[0].get("email_address")
    recipients: List[str] = []
    seen: set[str] = set()
    for candidate in (dsr.requester_email, profile_email):
        if candidate and "@" in candidate and candidate not in seen:
            seen.add(candidate)
            recipients.append(candidate)
    if not recipients:
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="No email address on file for the data subject; cannot deliver the export",
            details={"code": "DSR_ACCESS_NO_EMAIL"},
        )

    generated_at = int(time.time())
    zip_bytes = build_access_zip(
        data,
        dsr_id=dsr_id,
        visitor_profile_id=visitor_profile_id,
        generated_at=generated_at,
    )

    object_key = f"dsr-access-exports/{tenant_id}/{dsr_id}/{uuid4().hex}.zip"
    DocumentStorageManager.get_instance().provider.upload_bytes(
        object_key=object_key, payload=zip_bytes, mime_type="application/zip"
    )

    expires_at = generated_at + ACCESS_EXPORT_TTL_SECONDS
    download_url = resolve_download_url(
        object_key, expires_in=ACCESS_EXPORT_TTL_SECONDS
    )

    tenant_name = await _resolve_tenant_name(tenant_id)
    visitor_name = profile_rows[0].get("full_name") or "there"

    from core.email.manager import EmailManager
    from services.branding_service import get_email_branding_context

    # Data-subject-facing email → carry the tenant's brand (logo + accent).
    email_brand = await get_email_branding_context(tenant_id)

    manager = EmailManager.get_instance()
    for recipient in recipients:
        await manager.send_template(
            EmailDispatchRequest(
                to_email=recipient,
                template_key="dsr_access_package",
                context={
                    "visitor_name": visitor_name,
                    "tenant_name": tenant_name,
                    "download_url": download_url,
                    "expires_at": expires_at,
                    **email_brand,
                },
                dispatch="auto",
            )
        )

    emailed_to = ", ".join(recipients)
    upd = DSRUpdate(
        status=DSRStatus.COMPLETED,
        resolution=(
            f"Access request fulfilled — personal-data export emailed to {emailed_to}. "
            "Secure download link expires in 7 days."
        ),
        access_export_object_key=object_key,
        access_export_expires_at=expires_at,
        access_export_emailed_to=emailed_to,
        access_export_generated_at=generated_at,
    )
    await update_dsr_by_id(dsr_id=dsr_id, tenant_id=tenant_id, dsr_data=upd)

    return {
        "id": dsr_id,
        "status": "completed",
        "object_key": object_key,
        "emailed_to": emailed_to,
        "expires_at": expires_at,
    }
