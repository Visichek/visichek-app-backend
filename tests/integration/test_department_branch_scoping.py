"""
Integration test for Task 16 (Phase 7 remediation): department branch
scoping.

Proves the headline capability end-to-end through the real queued-write
path: BON Hotel Imperial and BON Hotel Elvis (two branches of the same
tenant) may each run a department named "Front Office", and a duplicate
name within a SINGLE branch is still rejected.

Requires: MongoDB on localhost:27017, Redis on localhost:6379, and a Celery
worker draining the ``writes`` queue (see tests/integration/conftest.py and
.github/workflows/ci.yml for how CI wires this up).

Run with:
    pytest tests/integration/test_department_branch_scoping.py -v
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.integration.conftest import complete_write

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def tenant_with_two_branches(
    integration_client: AsyncClient, auth_headers: dict
) -> tuple[str, str]:
    """Create two branches under the seeded tenant and return their ids.

    ``seeded_tenant`` (tests/integration/conftest.py) creates a bare tenant
    document with no branches at all -- ``repositories/tenant_repo.py``'s
    ``create_tenant`` never inserts a branch -- so both branches here are
    built through the real ``POST /v1/branches`` queued-write path; there
    is no HQ branch to reuse for "branch A".
    """
    ts = int(time.time())

    resp_a = await integration_client.post(
        "/v1/branches",
        json={"name": f"BON Hotel Imperial {ts}"},
        headers=auth_headers,
    )
    job_a = await complete_write(integration_client, resp_a, auth_headers)

    resp_b = await integration_client.post(
        "/v1/branches",
        json={"name": f"BON Hotel Elvis {ts}"},
        headers=auth_headers,
    )
    job_b = await complete_write(integration_client, resp_b, auth_headers)

    return job_a["resource_id"], job_b["resource_id"]


async def test_same_department_name_allowed_in_two_branches(
    integration_client: AsyncClient,
    auth_headers: dict,
    tenant_with_two_branches: tuple[str, str],
) -> None:
    """Imperial and Elvis may each run their own "Front Office" department.

    This is the headline capability Tasks 12-15 built: departments carry a
    ``branch_id`` and the name/code uniqueness check in
    ``services/department_service.py::validate_department_create`` is
    scoped to ``{tenant_id, branch_id}``, so it does not collide across two
    branches of the same tenant.
    """
    branch_a, branch_b = tenant_with_two_branches

    resp_a = await integration_client.post(
        "/v1/departments",
        json={"name": "Front Office", "branch_id": branch_a},
        headers=auth_headers,
    )
    await complete_write(integration_client, resp_a, auth_headers)

    resp_b = await integration_client.post(
        "/v1/departments",
        json={"name": "Front Office", "branch_id": branch_b},
        headers=auth_headers,
    )
    await complete_write(integration_client, resp_b, auth_headers)

    listing_a = await integration_client.get(
        f"/v1/departments?branchId={branch_a}", headers=auth_headers
    )
    assert listing_a.status_code == 200, listing_a.text
    names_a = [d["name"] for d in listing_a.json()["data"]]
    assert names_a == ["Front Office"]

    listing_b = await integration_client.get(
        f"/v1/departments?branchId={branch_b}", headers=auth_headers
    )
    assert listing_b.status_code == 200, listing_b.text
    names_b = [d["name"] for d in listing_b.json()["data"]]
    assert names_b == ["Front Office"]


async def test_duplicate_name_within_one_branch_is_rejected(
    integration_client: AsyncClient,
    auth_headers: dict,
    tenant_with_two_branches: tuple[str, str],
) -> None:
    """A duplicate department name within the SAME branch is still rejected.

    Unlike most write-time constraint violations in this codebase,
    department name/code duplication is checked SYNCHRONOUSLY: the route
    (``api/v1/department_route.py::create_department_endpoint``) calls
    ``validate_department_create`` and lets it raise ``HTTPException(409)``
    *before* the payload is handed to ``enqueue_write``. So the duplicate
    here never becomes a queued job at all -- it is rejected with a plain
    409 on the POST response itself, and there is nothing to poll via
    ``complete_write``/``expect_write_failure``.
    """
    branch_a, _branch_b = tenant_with_two_branches

    resp = await integration_client.post(
        "/v1/departments",
        json={"name": "Housekeeping", "branch_id": branch_a},
        headers=auth_headers,
    )
    await complete_write(integration_client, resp, auth_headers)

    dup = await integration_client.post(
        "/v1/departments",
        json={"name": "Housekeeping", "branch_id": branch_a},
        headers=auth_headers,
    )
    assert dup.status_code == 409, dup.text
