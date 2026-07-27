from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from core.queue.tasks import _AUTO_AUDIT_SKIP


def _fake_incident(**overrides: Any) -> SimpleNamespace:
    """Minimal double for ``IncidentLogOut``.

    Carries every attribute the incident writer handlers dereference on
    their DB result (``id``, ``tenant_id``, ``incident_type``, ``status``)
    so a handler that reads one more field than the previous version can't
    silently pass a test whose double only had two attributes on it.
    """
    defaults: dict[str, Any] = {
        "id": "i1",
        "tenant_id": "t1",
        "incident_type": "data_breach",
        "status": "open",
        "branch_id": "hq",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.unit
@pytest.mark.asyncio
class TestIncidentAudit:
    async def test_create_records_an_audit_event(self):
        from services.incident_writer import _incident_create

        created = AsyncMock(return_value=_fake_incident())
        with patch("services.incident_writer.add_incident", created), patch(
            "services.incident_writer.record_audit_event", AsyncMock()
        ) as mock_audit, patch(
            "services.incident_writer._enqueue_refresh"
        ), patch(
            "services.incident_writer._nudge_dashboard", AsyncMock()
        ):
            await _incident_create(
                "i1",
                {
                    "tenant_id": "t1",
                    "reported_by": "u1",
                    "incident_type": "data_breach",
                    "description": "test",
                    "_actor_id": "u1",
                    "_actor_role": "security_officer",
                },
            )

        mock_audit.assert_awaited_once()
        kwargs = mock_audit.await_args.kwargs
        assert kwargs["action"] == "incident.created"
        assert kwargs["resource_type"] == "incident"
        assert kwargs["resource_id"] == "i1"
        assert kwargs["tenant_id"] == "t1"

    async def test_update_records_an_audit_event_with_changes_diff(self):
        from services.incident_writer import _incident_update

        updated = AsyncMock(return_value=_fake_incident(status="investigating"))
        with patch(
            "services.incident_writer.update_incident_by_id", updated
        ), patch(
            "services.incident_writer.record_audit_event", AsyncMock()
        ) as mock_audit, patch(
            "services.incident_writer._enqueue_refresh"
        ), patch(
            "services.incident_writer._nudge_dashboard", AsyncMock()
        ):
            await _incident_update(
                "i1",
                {
                    "tenant_id": "t1",
                    "status": "investigating",
                    "description": "updated details",
                    "_actor_id": "u1",
                    "_actor_role": "security_officer",
                    "_request_id": "req-1",
                },
            )

        mock_audit.assert_awaited_once()
        kwargs = mock_audit.await_args.kwargs
        assert kwargs["action"] == "incident.updated"
        assert kwargs["resource_type"] == "incident"
        assert kwargs["resource_id"] == "i1"
        assert kwargs["tenant_id"] == "t1"
        assert kwargs["request_id"] == "req-1"

        # The changes diff must reflect exactly what the caller submitted —
        # not the reserved actor keys (stripped by _pop_actor before this
        # snapshot is taken) and not tenant_id (popped separately, it isn't
        # a field being "changed" on the incident).
        changes = kwargs["details"]["changes"]
        assert changes["status"] == "investigating"
        assert changes["description"] == "updated details"
        for reserved_key in ("_actor_id", "_actor_role", "_request_id", "tenant_id"):
            assert reserved_key not in changes

    def test_writers_are_registered_as_self_auditing(self):
        """Both keys must be in the skip set or the dispatcher double-audits."""
        assert "incident.create" in _AUTO_AUDIT_SKIP
        assert "incident.update" in _AUTO_AUDIT_SKIP
