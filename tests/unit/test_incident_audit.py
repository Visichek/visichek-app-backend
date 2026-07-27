from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.queue.tasks import _AUTO_AUDIT_SKIP


@pytest.mark.unit
@pytest.mark.asyncio
class TestIncidentAudit:
    async def test_create_records_an_audit_event(self):
        from services.incident_writer import _incident_create

        created = AsyncMock(
            return_value=type("I", (), {"id": "i1", "tenant_id": "t1"})()
        )
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

    def test_writers_are_registered_as_self_auditing(self):
        """Both keys must be in the skip set or the dispatcher double-audits."""
        assert "incident.create" in _AUTO_AUDIT_SKIP
        assert "incident.update" in _AUTO_AUDIT_SKIP
