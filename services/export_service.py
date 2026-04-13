from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from repositories.visit_session_repo import get_visit_sessions


async def export_visitor_log_csv(
    tenant_id: str,
    department_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
) -> bytes:
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

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
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
    )

    for s in sessions:
        check_in = _format_timestamp(s.check_in_time) if s.check_in_time else ""
        check_out = _format_timestamp(s.check_out_time) if s.check_out_time else ""
        duration = round(s.visit_duration / 60, 1) if s.visit_duration else ""

        writer.writerow(
            [
                s.visitor_name_snapshot or "",
                s.company_snapshot or "",
                s.department_name_snapshot or "",
                s.host_name_snapshot or "",
                check_in,
                check_out,
                duration,
                s.status or "",
                s.verification_status or "",
                s.check_in_method or "",
                s.receptionist_name_snapshot or "",
                s.purpose or "",
            ]
        )

    return output.getvalue().encode("utf-8")


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

    wb = Workbook()
    ws = wb.active
    ws.title = "Visitor Log"
    headers = [
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
    ws.append(headers)

    for s in sessions:
        check_in = _format_timestamp(s.check_in_time) if s.check_in_time else ""
        check_out = _format_timestamp(s.check_out_time) if s.check_out_time else ""
        duration = round(s.visit_duration / 60, 1) if s.visit_duration else ""

        ws.append(
            [
                s.visitor_name_snapshot or "",
                s.company_snapshot or "",
                s.department_name_snapshot or "",
                s.host_name_snapshot or "",
                check_in,
                check_out,
                duration,
                s.status or "",
                s.verification_status or "",
                s.check_in_method or "",
                s.receptionist_name_snapshot or "",
                s.purpose or "",
            ]
        )

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _format_timestamp(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
