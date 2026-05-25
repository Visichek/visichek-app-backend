from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

from bson import ObjectId

from core.database import db
from security.scoping import apply_department_scope
from security.principal import AuthPrincipal

COLLECTION = "visit_sessions"


async def get_consent_log(
    tenant_id: str,
    principal: Optional[AuthPrincipal] = None,
    start_date: Optional[int] = None,
    end_date: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[dict]:
    """Return consent-related fields from visit sessions for compliance reporting.

    Supports optional department scoping for dept_admin users.

    Args:
        tenant_id: Tenant ID to filter by
        principal: AuthPrincipal for optional department scoping
        start_date: Optional Unix timestamp for filtering by start date
        end_date: Optional Unix timestamp for filtering by end date
        skip: Number of records to skip (pagination offset)
        limit: Maximum number of records to return

    Returns:
        List of consent log records with relevant fields
    """
    filter_dict: Dict[str, Any] = {"tenant_id": tenant_id}

    # Apply department scoping if principal provided and user is dept_admin
    if principal:
        filter_dict = apply_department_scope(filter_dict, principal)

    # Apply date range filtering on consent_timestamp if provided
    if start_date or end_date:
        date_filter: Dict[str, Any] = {}
        if start_date:
            date_filter["$gte"] = start_date
        if end_date:
            date_filter["$lte"] = end_date
        filter_dict["consent_timestamp"] = date_filter

    # Projection: only consent-related fields
    projection = {
        "_id": 1,
        "visitor_name_snapshot": 1,
        "consent_granted": 1,
        "consent_method": 1,
        "consent_timestamp": 1,
        "consent_captured_by_user_id": 1,
        "privacy_notice_version_id": 1,
        "lawful_basis_at_time": 1,
        "consent_withdrawal_at": 1,
        "department_id": 1,
    }

    # Fetch enough rows from each source to satisfy the page after merge.
    fetch = skip + limit
    cursor = (
        db[COLLECTION]
        .find(filter_dict, projection)
        .sort("consent_timestamp", -1)
        .limit(fetch)
    )

    results: List[dict] = []
    async for doc in cursor:
        if "_id" in doc and isinstance(doc["_id"], ObjectId):
            doc["_id"] = str(doc["_id"])
        doc.setdefault("source", "visit_session")
        results.append(doc)

    # Union the dedicated consent_records store (kiosk / public submit paths,
    # which have no visit_sessions row to carry consent on).
    cr_filter: Dict[str, Any] = {"tenant_id": tenant_id}
    if principal:
        cr_filter = apply_department_scope(cr_filter, principal)
    if start_date or end_date:
        cr_date: Dict[str, Any] = {}
        if start_date:
            cr_date["$gte"] = start_date
        if end_date:
            cr_date["$lte"] = end_date
        cr_filter["consent_timestamp"] = cr_date
    try:
        from repositories.consent_record_repo import get_consent_records

        cr_rows = await get_consent_records(cr_filter, skip=0, limit=fetch)
        for row in cr_rows:
            row.setdefault("source", "consent_record")
            results.append(row)
    except Exception:
        pass

    # Merge both sources newest-first, then apply the requested page window.
    results.sort(key=lambda r: r.get("consent_timestamp") or 0, reverse=True)
    return results[skip : skip + limit]


async def get_consent_log_count(
    tenant_id: str,
    principal: Optional[AuthPrincipal] = None,
    start_date: Optional[int] = None,
    end_date: Optional[int] = None,
) -> int:
    """Get total count of consent log records for a tenant.

    Useful for pagination metadata.
    """
    filter_dict: Dict[str, Any] = {"tenant_id": tenant_id}

    if principal:
        filter_dict = apply_department_scope(filter_dict, principal)

    if start_date or end_date:
        date_filter: Dict[str, Any] = {}
        if start_date:
            date_filter["$gte"] = start_date
        if end_date:
            date_filter["$lte"] = end_date
        filter_dict["consent_timestamp"] = date_filter

    session_count = await db[COLLECTION].count_documents(filter_dict)
    try:
        from repositories.consent_record_repo import count_consent_records

        consent_record_count = await count_consent_records(filter_dict)
    except Exception:
        consent_record_count = 0
    return session_count + consent_record_count


async def generate_compliance_export(tenant_id: str) -> bytes:
    """Generate a ZIP containing compliance CSVs."""
    import io
    import zipfile

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Consent log
        consent_records = await get_consent_log(tenant_id=tenant_id, limit=10000)
        csv_str = _records_to_csv(
            consent_records,
            [
                "_id",
                "visitor_name_snapshot",
                "consent_granted",
                "consent_method",
                "consent_timestamp",
                "privacy_notice_version_id",
                "lawful_basis_at_time",
            ],
        )
        zf.writestr("consent_log.csv", csv_str)

        # Audit trail
        audit_records = (
            await db.audit_trail.find({"tenant_id": tenant_id})
            .sort("timestamp", -1)
            .to_list(10000)
        )
        csv_str = _records_to_csv(
            audit_records,
            [
                "_id",
                "actor_id",
                "actor_role",
                "action",
                "resource_type",
                "resource_id",
                "timestamp",
            ],
        )
        zf.writestr("audit_trail.csv", csv_str)

        # DSR records
        dsr_records = await db.data_subject_requests.find(
            {"tenant_id": tenant_id}
        ).to_list(10000)
        csv_str = _records_to_csv(
            dsr_records,
            [
                "_id",
                "request_type",
                "status",
                "received_at",
                "sla_deadline",
                "resolved_at",
            ],
        )
        zf.writestr("dsr_records.csv", csv_str)

        # Deletion logs
        deletion_records = await db.deletion_logs.find(
            {"tenant_id": tenant_id}
        ).to_list(10000)
        csv_str = _records_to_csv(
            deletion_records,
            ["_id", "deleted_entity", "deleted_at", "deleted_by", "reason"],
        )
        zf.writestr("deletion_log.csv", csv_str)

        # Retention policies
        retention_records = await db.retention_policies.find(
            {"tenant_id": tenant_id}
        ).to_list(10000)
        csv_str = _records_to_csv(
            retention_records, ["_id", "entity_type", "retention_days", "action"]
        )
        zf.writestr("retention_policies.csv", csv_str)

        # Sub-processors
        sub_records = await db.sub_processors.find({"tenant_id": tenant_id}).to_list(
            10000
        )
        csv_str = _records_to_csv(
            sub_records,
            [
                "_id",
                "provider",
                "purpose",
                "jurisdiction",
                "dpa_signed",
                "uses_data_for_training",
            ],
        )
        zf.writestr("sub_processor_register.csv", csv_str)

        # Data processing register
        dpr_records = await db.data_processing_register.find(
            {"tenant_id": tenant_id}
        ).to_list(10000)
        csv_str = _records_to_csv(
            dpr_records,
            [
                "_id",
                "processing_purpose",
                "data_categories",
                "lawful_basis",
                "retention_period",
            ],
        )
        zf.writestr("data_processing_register.csv", csv_str)

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def _records_to_csv(records: list, fields: list) -> str:
    """Convert a list of records to CSV format."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        row: Dict[str, Any] = {}
        for f in fields:
            val = (
                record.get(f, "")
                if isinstance(record, dict)
                else getattr(record, f, "")
            )
            if hasattr(val, "__str__") and not isinstance(val, str):
                val = str(val)
            row[f] = (
                val if isinstance(val, str) else str(val) if val is not None else ""
            )
        writer.writerow(row)
    return output.getvalue()
