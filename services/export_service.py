from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.csv_export import csv_buffer
from core.database import db
from repositories.audit_log_repo import get_audit_logs
from repositories.visit_session_repo import get_visit_sessions
from services.audit_service import retrieve_audit_logs_with_summary

VISITOR_LOG_HEADER = [
    "Visitor Name",
    "Company",
    "Department",
    "Host",
    "Check-In Time",
    "Check-Out Time",
    "Duration (min)",
    "Status",
    "Verification Status",
    "Check-In Method",
    "Receptionist",
    "Purpose",
]


def _checkin_visitor_name(doc: Dict[str, Any]) -> str:
    """Best-effort visitor name from the kiosk form answers."""
    tsd = doc.get("tenant_specific_data") or {}
    for key in ("full_name", "name", "visitor_name"):
        value = tsd.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _checkin_purpose(doc: Dict[str, Any]) -> str:
    """Purpose text from a raw ``checkins`` doc.

    Unlike ``visit_sessions.purpose`` (a plain string snapshot), a
    ``checkins`` document stores ``purpose`` as the nested
    ``CheckinPurpose`` object (``{"purpose": ..., "purpose_details": ...,
    "expected_duration_minutes": ...}``) — see ``schemas.checkin_schema``.
    Reading ``doc.get("purpose")`` directly would put that whole dict into
    the export cell instead of the human-readable purpose string.
    """
    purpose = doc.get("purpose")
    if isinstance(purpose, dict):
        value = purpose.get("purpose")
        return value if isinstance(value, str) else ""
    if isinstance(purpose, str):
        return purpose
    return ""


async def _visitor_log_rows(
    tenant_id: str,
    department_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
) -> List[List[Any]]:
    """Merged visitor-log rows across BOTH visit models, oldest first.

    ``visit_sessions`` covers the receptionist and public-registration
    flows; ``checkins`` covers the kiosk/checkin-config flow, which creates
    no visit_sessions row. Exporting only the former silently omitted every
    kiosk visitor.
    """
    filter_dict: Dict[str, Any] = {"tenant_id": tenant_id}
    if department_id:
        filter_dict["department_id"] = department_id
    if date_from or date_to:
        time_filter: Dict[str, Any] = {}
        if date_from:
            time_filter["$gte"] = date_from
        if date_to:
            time_filter["$lte"] = date_to
        filter_dict["check_in_time"] = time_filter

    sessions = await get_visit_sessions(filter_dict=filter_dict, start=0, stop=10000)

    rows: List[tuple] = []
    for s in sessions:
        duration = round(s.visit_duration / 60, 1) if s.visit_duration else ""
        rows.append(
            (
                s.check_in_time or 0,
                [
                    s.visitor_name_snapshot or "",
                    s.company_snapshot or "",
                    s.department_name_snapshot or "",
                    s.host_name_snapshot or "",
                    _format_timestamp(s.check_in_time) if s.check_in_time else "",
                    _format_timestamp(s.check_out_time) if s.check_out_time else "",
                    duration,
                    s.status or "",
                    s.verification_status or "",
                    s.check_in_method or "",
                    s.receptionist_name_snapshot or "",
                    s.purpose or "",
                ],
            )
        )

    checkin_filter: Dict[str, Any] = {"tenant_id": tenant_id}
    if department_id:
        checkin_filter["department_id"] = department_id
    if date_from or date_to:
        ci_time: Dict[str, Any] = {}
        if date_from:
            ci_time["$gte"] = date_from
        if date_to:
            ci_time["$lte"] = date_to
        checkin_filter["date_created"] = ci_time

    cursor = db.checkins.find(checkin_filter).limit(10000)
    async for doc in cursor:
        created = doc.get("date_created") or 0
        checked_out = doc.get("checked_out_at")
        duration_min: Any = ""
        if created and checked_out:
            duration_min = round((checked_out - created) / 60, 1)
        rows.append(
            (
                created,
                [
                    _checkin_visitor_name(doc),
                    "",
                    doc.get("department_name") or "",
                    doc.get("host_name") or "",
                    _format_timestamp(created) if created else "",
                    _format_timestamp(checked_out) if checked_out else "",
                    duration_min,
                    doc.get("state") or "",
                    "verified" if doc.get("verified") else "unverified",
                    "kiosk",
                    "",
                    _checkin_purpose(doc),
                ],
            )
        )

    rows.sort(key=lambda pair: pair[0])
    return [row for _, row in rows]


async def export_visitor_log_csv(
    tenant_id: str,
    department_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
) -> bytes:
    rows = await _visitor_log_rows(tenant_id, department_id, date_from, date_to)
    # csv_buffer (core.csv_export) escapes leading =, +, -, @, tab and CR
    # per OWASP CWE-1236. The previous raw csv.writer did not, so a
    # visitor-supplied name could execute as a formula in the spreadsheet
    # the tenant opens.
    return csv_buffer([VISITOR_LOG_HEADER, *rows])


async def export_visitor_log_xlsx(
    tenant_id: str,
    department_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
) -> bytes:
    try:
        from openpyxl import Workbook  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError("openpyxl is required for Excel export")

    rows = await _visitor_log_rows(tenant_id, department_id, date_from, date_to)

    wb = Workbook()
    ws = wb.active
    ws.title = "Visitor Log"
    ws.append(VISITOR_LOG_HEADER)

    for row in rows:
        ws.append(row)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


_AUDIT_HEADERS = [
    "Timestamp (UTC)",
    "Action",
    "Resource Type",
    "Resource ID",
    "Resource Label",
    "Actor ID",
    "Actor Name",
    "Actor Email",
    "Actor Role",
    "Tenant",
    "Request ID",
    "Details (JSON)",
]


def _audit_filter(
    *,
    tenant_id: Optional[str],
    actor_id: Optional[str],
    action: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    date_from: Optional[int],
    date_to: Optional[int],
) -> Dict[str, Any]:
    filter_dict: Dict[str, Any] = {}
    if tenant_id:
        filter_dict["tenant_id"] = tenant_id
    if actor_id:
        filter_dict["actor_id"] = actor_id
    if action:
        filter_dict["action"] = action
    if resource_type:
        filter_dict["resource_type"] = resource_type
    if resource_id:
        filter_dict["resource_id"] = resource_id
    if date_from or date_to:
        ts_filter: Dict[str, Any] = {}
        if date_from:
            ts_filter["$gte"] = date_from
        if date_to:
            ts_filter["$lte"] = date_to
        filter_dict["timestamp"] = ts_filter
    return filter_dict


def _audit_row(log: Any) -> list:
    """Flatten a single ``AuditLogWithSummaryOut`` into a spreadsheet row.

    Pulls a human-readable label out of ``resource_summary`` for whichever
    polymorphic shape it carries (tenant company_name, user full_name,
    department/branch name, etc.), falling back to the bare id."""
    actor_summary = getattr(log, "actor_summary", None)
    tenant_summary = getattr(log, "tenant_summary", None)
    resource_summary = getattr(log, "resource_summary", None)

    resource_label: Optional[str] = None
    if resource_summary is not None:
        for attr in (
            "company_name",
            "full_name",
            "name",
            "display_name",
            "invoice_number",
        ):
            value = getattr(resource_summary, attr, None)
            if value:
                resource_label = str(value)
                break
        if resource_label is None:
            resource_label = getattr(resource_summary, "id", None)

    timestamp_str = (
        _format_timestamp(log.timestamp) if getattr(log, "timestamp", None) else ""
    )
    details = getattr(log, "details", None) or {}
    details_str = (
        json.dumps(details, default=str, ensure_ascii=False) if details else ""
    )

    return [
        timestamp_str,
        getattr(log, "action", "") or "",
        getattr(log, "resource_type", "") or "",
        getattr(log, "resource_id", "") or "",
        resource_label or "",
        getattr(log, "actor_id", "") or "",
        (actor_summary.full_name if actor_summary and actor_summary.full_name else ""),
        (actor_summary.email if actor_summary and actor_summary.email else ""),
        getattr(log, "actor_role", "") or "",
        (
            tenant_summary.company_name
            if tenant_summary and tenant_summary.company_name
            else (getattr(log, "tenant_id", "") or "")
        ),
        getattr(log, "request_id", "") or "",
        details_str,
    ]


async def export_audit_logs_xlsx(
    *,
    tenant_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    limit: int = 10000,
) -> bytes:
    """Render audit-trail rows into an XLSX with frozen headers.

    Mirrors the visitor-log export: columns are bolded, header row is
    frozen via ``ws.freeze_panes = 'A2'``, autofilter is enabled across
    every column, and column widths are sized to the longest cell value
    so the spreadsheet opens looking like a finished report rather than a
    raw dump."""
    try:
        from openpyxl import Workbook  # type: ignore[import-untyped]
        from openpyxl.styles import Alignment, Font, PatternFill  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover — declared in requirements
        raise RuntimeError("openpyxl is required for Excel export") from exc

    filter_dict = _audit_filter(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        date_from=date_from,
        date_to=date_to,
    )
    logs = await retrieve_audit_logs_with_summary(filter_dict, start=0, stop=limit)

    wb = Workbook()
    ws = wb.active
    if ws is None:
        ws = wb.create_sheet("Audit Logs")
    ws.title = "Audit Logs"

    ws.append(_AUDIT_HEADERS)

    # Header styling: bold white-on-slate with center alignment, then
    # freeze the first row so the labels stay visible on scroll.
    header_font = Font(bold=True, color="FFFFFFFF")
    header_fill = PatternFill("solid", fgColor="FF334155")
    header_align = Alignment(horizontal="center", vertical="center")
    for col_idx in range(1, len(_AUDIT_HEADERS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
    ws.freeze_panes = "A2"

    # Body rows.
    for log in logs:
        ws.append(_audit_row(log))

    # Autofilter across the populated range so users can sort/filter
    # without touching the data — the receptionist's expected workflow.
    last_col_letter = chr(ord("A") + len(_AUDIT_HEADERS) - 1)
    last_row = max(ws.max_row, 1)
    ws.auto_filter.ref = f"A1:{last_col_letter}{last_row}"

    # Column-width sizing — bounded so a giant details JSON doesn't blow
    # the spreadsheet up to 200+ characters wide.
    width_caps = {
        "Details (JSON)": 80,
        "Resource ID": 30,
        "Actor ID": 30,
        "Request ID": 32,
    }
    for col_idx, header in enumerate(_AUDIT_HEADERS, start=1):
        column_letter = chr(ord("A") + col_idx - 1)
        max_len = len(header)
        for row in ws.iter_rows(
            min_row=2, min_col=col_idx, max_col=col_idx, values_only=True
        ):
            value = row[0]
            if value is None:
                continue
            value_len = len(str(value))
            if value_len > max_len:
                max_len = value_len
        cap = width_caps.get(header, 50)
        ws.column_dimensions[column_letter].width = min(max(max_len + 2, 10), cap)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# Stop linters complaining about the import that's only used for type
# coverage in a sibling module — kept here to keep the public surface
# of this module flat.
_ = get_audit_logs


def _format_timestamp(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
