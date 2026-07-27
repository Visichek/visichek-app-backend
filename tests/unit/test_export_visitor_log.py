from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.export_service import _visitor_log_rows, export_visitor_log_csv


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d

        return gen()


def _fake_db(checkin_docs):
    checkins = MagicMock()
    checkins.find = MagicMock(return_value=_Cursor(checkin_docs))
    fake = MagicMock()
    fake.checkins = checkins
    fake.__getitem__.return_value = checkins
    return fake


def _session(name, ts):
    s = MagicMock()
    s.visitor_name_snapshot = name
    s.company_snapshot = ""
    s.department_name_snapshot = ""
    s.host_name_snapshot = ""
    s.check_in_time = ts
    s.check_out_time = None
    s.visit_duration = None
    s.status = "checked_in"
    s.verification_status = "unverified"
    s.check_in_method = "manual"
    s.receptionist_name_snapshot = ""
    s.purpose = ""
    return s


@pytest.mark.unit
@pytest.mark.asyncio
class TestVisitorLogRows:
    async def test_merges_checkins_with_visit_sessions(self):
        fake = _fake_db(
            [{"_id": "c1", "tenant_specific_data": {"full_name": "Kiosk Kate"},
              "date_created": 200, "state": "approved", "host_name": "",
              "department_name": "", "check_out_method": None,
              "checked_out_at": None, "purpose": "", "verified": False}]
        )
        with patch("services.export_service.db", fake), patch(
            "services.export_service.get_visit_sessions",
            AsyncMock(return_value=[_session("Desk Dan", 100)]),
        ):
            rows = await _visitor_log_rows("t1", None, None, None)

        names = [r[0] for r in rows]
        assert "Desk Dan" in names, "visit_sessions rows must still appear"
        assert "Kiosk Kate" in names, "kiosk checkins were missing from the export"

    async def test_rows_are_sorted_by_check_in_time(self):
        fake = _fake_db(
            [{"_id": "c1", "tenant_specific_data": {"full_name": "Early Eve"},
              "date_created": 50, "state": "approved", "host_name": "",
              "department_name": "", "check_out_method": None,
              "checked_out_at": None, "purpose": "", "verified": False}]
        )
        with patch("services.export_service.db", fake), patch(
            "services.export_service.get_visit_sessions",
            AsyncMock(return_value=[_session("Later Lou", 100)]),
        ):
            rows = await _visitor_log_rows("t1", None, None, None)

        assert [r[0] for r in rows] == ["Early Eve", "Later Lou"]


@pytest.mark.unit
@pytest.mark.asyncio
class TestExportEscaping:
    async def test_formula_prefix_is_escaped(self):
        """A visitor-supplied name starting with = must not execute in Excel."""
        fake = _fake_db([])
        with patch("services.export_service.db", fake), patch(
            "services.export_service.get_visit_sessions",
            AsyncMock(return_value=[_session("=cmd|'/c calc'!A1", 100)]),
        ):
            payload = (await export_visitor_log_csv("t1")).decode("utf-8")

        assert "\n=cmd" not in payload
        assert ",=cmd" not in payload
        assert "cmd|" in payload, "the value itself must still be present"
