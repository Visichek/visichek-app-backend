"""Unit tests for the Phase 2 fix: recurring add-on rows must never be
touched by the plain expiry sweep, and the renewal lead window must pull
rows in ahead of their exact ``expires_at``.

Covers:
- ``expire_due_tenant_addons`` excludes ``recurring_snapshot=True`` rows
  from its ``update_many`` filter, even when their ``expires_at`` is past.
- ``list_due_tenant_ids_for_expiry`` applies the same exclusion.
- ``list_due_recurring_tenant_addons`` selects rows due within the 2-hour
  lead window (``expires_at <= now + lead``), not just strictly-past rows.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.asyncio


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _FakeCursor(_AsyncIter):
    def limit(self, *a, **kw):
        return self


class _FakeCollection:
    def __init__(self):
        self.update_many_calls: list[dict] = []
        self.distinct_calls: list[dict] = []
        self.find_calls: list[dict] = []

    async def update_many(self, filt, update, *a, **kw):
        self.update_many_calls.append(filt)
        return AsyncMock(modified_count=0)

    async def distinct(self, field, filt, *a, **kw):
        self.distinct_calls.append(filt)
        return []

    def find(self, filt=None, *a, **kw):
        self.find_calls.append(filt or {})
        return _FakeCursor([])


class _FakeDB:
    def __init__(self, collection: _FakeCollection):
        self._collection = collection

    def __getitem__(self, name):
        return self._collection


@pytest.fixture()
def fake_collection(monkeypatch) -> _FakeCollection:
    collection = _FakeCollection()
    monkeypatch.setattr("repositories.tenant_addon_repo.db", _FakeDB(collection))
    return collection


async def test_expire_due_tenant_addons_excludes_recurring_rows(
    fake_collection,
) -> None:
    from repositories.tenant_addon_repo import expire_due_tenant_addons

    await expire_due_tenant_addons()

    assert len(fake_collection.update_many_calls) == 1
    filt = fake_collection.update_many_calls[0]
    assert filt.get("recurring_snapshot") == {"$ne": True}


async def test_list_due_tenant_ids_for_expiry_excludes_recurring_rows(
    fake_collection,
) -> None:
    from repositories.tenant_addon_repo import list_due_tenant_ids_for_expiry

    await list_due_tenant_ids_for_expiry()

    assert len(fake_collection.distinct_calls) == 1
    filt = fake_collection.distinct_calls[0]
    assert filt.get("recurring_snapshot") == {"$ne": True}


async def test_list_due_recurring_tenant_addons_uses_lead_window(
    fake_collection,
) -> None:
    from repositories.tenant_addon_repo import (
        _RENEWAL_LEAD_SECONDS,
        list_due_recurring_tenant_addons,
    )

    now = int(time.time())
    await list_due_recurring_tenant_addons(now)

    assert len(fake_collection.find_calls) == 1
    filt: dict[str, Any] = fake_collection.find_calls[0]
    assert filt["recurring_snapshot"] is True
    assert filt["expires_at"]["$lte"] == now + _RENEWAL_LEAD_SECONDS
    assert _RENEWAL_LEAD_SECONDS == 2 * 60 * 60
