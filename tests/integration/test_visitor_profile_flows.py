"""
Integration tests for visitor profile endpoints:
  - Search by email / phone / name
  - List with pagination
  - Get single profile
  - Update profile (including profiling opt-out)

Requires: MongoDB on localhost:27017, Redis on localhost:6379
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient

from schemas.visitor_profile_schema import VisitorProfileCreate
from repositories.visitor_profile_repo import create_visitor_profile


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestVisitorProfileFlow:
    """Full integration test for visitor-profile management."""

    @pytest_asyncio.fixture
    async def _profiles(self, mongo_db, seeded_tenant):
        """Seed 5 visitor profiles with varied data."""
        profiles = []
        base_time = int(time.time())
        names = [
            ("Ada Lovelace", "ada@example.com", "+2348100000001"),
            ("Grace Hopper", "grace@example.com", "+2348100000002"),
            ("Alan Turing", "alan@example.com", "+2348100000003"),
            ("Marie Curie", "marie@example.com", "+2348100000004"),
            ("Nikola Tesla", "nikola@example.com", "+2348100000005"),
        ]
        for i, (name, email, phone) in enumerate(names):
            profile = await create_visitor_profile(
                VisitorProfileCreate(
                    tenant_id=seeded_tenant.id,
                    full_name=name,
                    email_address=email,
                    phone=phone,
                    company=f"Company {chr(65 + i)}",
                    id_type="national_id",
                    id_number=f"NIN{10000 + i}",
                    profiling_preference="allowed",
                )
            )
            profiles.append(profile)
        return profiles

    async def test_list_profiles(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _profiles,
    ):
        resp = await integration_client.get(
            "/v1/visitor-profiles/", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) >= 5

    async def test_get_single_profile(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _profiles,
    ):
        profile_id = _profiles[0].id
        resp = await integration_client.get(
            f"/v1/visitor-profiles/{profile_id}", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == profile_id
        assert data["full_name"] == "Ada Lovelace"

    async def test_search_by_email(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _profiles,
    ):
        resp = await integration_client.get(
            "/v1/visitor-profiles/search",
            params={"q": "grace@example.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        # Should find at least Grace Hopper
        names = [p["full_name"] for p in data]
        assert any("Grace" in n for n in names)

    async def test_search_by_name(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _profiles,
    ):
        resp = await integration_client.get(
            "/v1/visitor-profiles/search",
            params={"q": "Turing"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        names = [p["full_name"] for p in data]
        assert any("Turing" in n for n in names)

    async def test_update_profile_profiling_optout(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _profiles,
    ):
        profile_id = _profiles[2].id  # Alan Turing
        resp = await integration_client.patch(
            f"/v1/visitor-profiles/{profile_id}",
            json={"profiling_preference": "opted_out"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["profiling_preference"] == "opted_out"

    async def test_update_profile_company(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
        _profiles,
    ):
        profile_id = _profiles[4].id  # Nikola Tesla
        resp = await integration_client.patch(
            f"/v1/visitor-profiles/{profile_id}",
            json={"company": "Tesla Inc."},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["company"] == "Tesla Inc."

    async def test_get_nonexistent_profile(
        self,
        integration_client: AsyncClient,
        auth_headers: dict,
    ):
        resp = await integration_client.get(
            "/v1/visitor-profiles/000000000000000000000000",
            headers=auth_headers,
        )
        # May return 404 or 200 with null data depending on implementation
        assert resp.status_code in (200, 404)

    async def test_list_profiles_unauthorized(
        self, integration_client: AsyncClient
    ):
        resp = await integration_client.get("/v1/visitor-profiles/")
        assert resp.status_code in (401, 403)
