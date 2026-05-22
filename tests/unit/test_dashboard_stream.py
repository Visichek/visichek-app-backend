"""Unit tests for the live-dashboard SSE stream (DB-free).

Covers the SSE frame format, the best-effort publish nudge (never raises on a
Redis outage), and the generator's initial-snapshot + clean-teardown path with
a fully faked Redis pub/sub."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, patch

from security.principal import AuthPrincipal
from services.dashboard_stream_service import (
    _format_sse,
    publish_dashboard_refresh,
    stream_dashboard_live,
    unified_stream,
)


class _FakePubSub:
    def __init__(self) -> None:
        self.subscribed = None
        self.unsubscribed = False

    async def subscribe(self, channel):
        self.subscribed = channel

    async def get_message(self, **kwargs):
        return None

    async def unsubscribe(self, channel):
        self.unsubscribed = True

    async def aclose(self):
        pass


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list = []

    def pubsub(self):
        return _FakePubSub()

    async def publish(self, channel, message):
        self.published.append((channel, message))


class _FakeRequest:
    """Reports connected for the snapshot, then disconnected so the loop exits."""

    def __init__(self) -> None:
        self._checks = 0

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks >= 1


def test_format_sse_frame_shape():
    frame = _format_sse(7, "dashboard.live", {"counters": {"x": 1}})
    assert "id: 7\n" in frame
    assert "event: dashboard.live\n" in frame
    assert frame.endswith("\n\n")
    # data line is valid JSON
    data_line = [ln for ln in frame.splitlines() if ln.startswith("data: ")][0]
    assert json.loads(data_line[len("data: ") :])["counters"]["x"] == 1


class TestPublishBestEffort:
    @pytest.mark.asyncio
    async def test_publish_nudges_admin_and_tenant_channels(self):
        fake = _FakeRedis()
        with patch(
            "services.dashboard_stream_service._get_async_redis", return_value=fake
        ):
            await publish_dashboard_refresh("tenant-1")
        channels = [c for c, _ in fake.published]
        assert "dash:events:admin" in channels
        assert "dash:events:tenant:tenant-1" in channels

    @pytest.mark.asyncio
    async def test_publish_never_raises_on_redis_outage(self):
        with patch(
            "services.dashboard_stream_service._get_async_redis",
            side_effect=RuntimeError("redis down"),
        ):
            # Must swallow the error — a write path can never be broken by a
            # failed dashboard nudge.
            await publish_dashboard_refresh("tenant-1")


class TestStreamGenerator:
    @pytest.mark.asyncio
    async def test_emits_initial_absolute_snapshot_then_exits(self):
        async def _compute():
            return {"counters": {"currentlyActive": 3}, "lastUpdated": 123}

        with patch(
            "services.dashboard_stream_service._get_async_redis",
            return_value=_FakeRedis(),
        ):
            frames = []
            async for frame in stream_dashboard_live(
                _FakeRequest(),
                channel="dash:events:tenant:t1",
                compute=_compute,
                jwt_token="tok",
            ):
                frames.append(frame)

        # Exactly the one snapshot frame (request disconnects after it).
        assert len(frames) == 1
        assert "event: dashboard.live" in frames[0]
        assert '"currentlyActive": 3' in frames[0]


class TestUnifiedStreamBranching:
    @pytest.mark.asyncio
    async def test_admin_role_gets_admin_slice_with_scope_meta(self):
        principal = AuthPrincipal(
            user_id="a1",
            role="admin",
            access_token_id="x",
            jwt_token="t",
            tenant_id=None,
        )
        with (
            patch(
                "services.dashboard_stream_service.compute_admin_live_slice",
                new=AsyncMock(
                    return_value={"counters": {"openIncidents": 2}, "lastUpdated": 1}
                ),
            ),
            patch(
                "services.dashboard_stream_service._get_async_redis",
                return_value=_FakeRedis(),
            ),
        ):
            frames = [
                f async for f in unified_stream(_FakeRequest(), principal=principal)
            ]
        assert len(frames) == 1
        assert '"scope": "admin"' in frames[0]
        assert '"openIncidents": 2' in frames[0]

    @pytest.mark.asyncio
    async def test_tenant_role_gets_tenant_slice_with_plan_meta(self):
        principal = AuthPrincipal(
            user_id="u1",
            role="receptionist",
            access_token_id="x",
            jwt_token="t",
            tenant_id="tenant-9",
        )
        with (
            patch(
                "services.dashboard_stream_service.compute_tenant_live_slice",
                new=AsyncMock(
                    return_value={"counters": {"currentlyActive": 1}, "lastUpdated": 1}
                ),
            ),
            patch(
                "services.dashboard_stream_service._tenant_plan_tier",
                new=AsyncMock(return_value="free"),
            ),
            patch(
                "services.dashboard_stream_service._get_async_redis",
                return_value=_FakeRedis(),
            ),
        ):
            frames = [
                f async for f in unified_stream(_FakeRequest(), principal=principal)
            ]
        assert len(frames) == 1
        assert '"scope": "tenant"' in frames[0]
        assert '"planTier": "free"' in frames[0]
        assert '"isFreeFallback": true' in frames[0]
        assert '"currentlyActive": 1' in frames[0]
