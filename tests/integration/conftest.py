from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator, cast

import pytest_asyncio
import redis
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from fastapi import FastAPI

# Load test environment variables FIRST before importing any app modules
env_test_path = Path(__file__).parent.parent.parent / ".env.test"
if env_test_path.exists():
    load_dotenv(env_test_path, override=True)

# Override DB_TYPE to mongodb for integration tests
os.environ["DB_TYPE"] = "mongodb"
os.environ["DB_NAME"] = "visichek_test_integration"

# Add the backend root to the path
backend_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_root))

from core.settings import get_settings
from main import app
from schemas.tenant_schema import TenantCreate, TenantOut
from schemas.system_user_schema import SystemUserCreate, SystemUserOut
from repositories.tenant_repo import create_tenant
from repositories.system_user_repo import create_system_user
from schemas.imports import (
    SystemUserRole,
    AccountStatus,
    LawfulBasis,
    NoticeDisplayMode,
)
import core.database


settings = get_settings()


def _patch_db_everywhere(new_db: AsyncIOMotorDatabase) -> dict:
    """
    Replace the ``db`` binding in ``core.database`` **and** every loaded
    module that did ``from core.database import db``.

    Returns a dict of {module_name: original_db} so callers can restore.
    """
    originals: dict = {}

    # 1. Patch the canonical module attribute
    originals["core.database"] = core.database.db
    core.database.db = new_db  # type: ignore[assignment]

    # 2. Patch every already-imported module that grabbed a local reference
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if mod_name == "core.database":
            continue
        # Only look at project modules that are likely to have imported db
        if not (
            mod_name.startswith("repositories.")
            or mod_name.startswith("services.")
            or mod_name.startswith("security.")
            or mod_name.startswith("api.")
            or mod_name.startswith("core.")
            or mod_name == "main"
            or mod_name == "seed"
        ):
            continue
        existing_db = mod.__dict__.get("db")
        # Replace any db reference in project namespaces — loose match so we
        # also catch modules that captured a previous test's (now-closed) client.
        if existing_db is not None and hasattr(existing_db, "get_collection"):
            originals[mod_name] = existing_db  # type: ignore[attr-defined]
            mod.db = new_db  # type: ignore[attr-defined]

    return originals


def _restore_db_everywhere(originals: dict) -> None:
    """Restore all ``db`` bindings from the dict returned by _patch_db_everywhere."""
    for mod_name, original_db in originals.items():
        mod = sys.modules.get(mod_name)
        if mod is not None:
            mod.db = original_db  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def mongo_db() -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    """
    Fixture that connects to real MongoDB for integration tests.
    Creates a fresh Motor client bound to the current event loop and patches
    the ``db`` reference in all repository / service modules so the app
    uses this connection instead of the stale import-time one.
    """
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name = "visichek_test_integration"

    client: Any = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # Swap the db reference in every module that imported it
    originals = _patch_db_everywhere(db)

    yield db

    # Restore original references and cleanup
    _restore_db_everywhere(originals)
    await client.drop_database(db_name)
    client.close()


@pytest_asyncio.fixture
async def redis_client() -> AsyncGenerator[redis.Redis, None]:
    """
    Fixture that connects to real Redis for integration tests.
    Flushes test keys on teardown.
    """
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    client = redis.from_url(redis_url, decode_responses=True)

    # Flush any existing keys to start clean
    client.flushdb()

    yield client

    # Cleanup: flush test keys after each test
    client.flushdb()
    client.close()


@pytest_asyncio.fixture
async def integration_app(mongo_db: AsyncIOMotorDatabase) -> FastAPI:
    """
    Fixture that returns the FastAPI app configured with real MongoDB.
    Depends on mongo_db to ensure the database is patched before any
    requests hit the app.
    """
    return app


@pytest_asyncio.fixture
async def integration_client(
    integration_app: FastAPI,
) -> AsyncGenerator[AsyncClient, None]:
    """
    Fixture that provides an httpx AsyncClient for making requests to the FastAPI app.
    """
    async with AsyncClient(
        transport=ASGITransport(app=cast(object, integration_app)),  # type: ignore[arg-type]
        base_url="http://test",
        headers={"X-Response-Case": "snake"},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def seeded_tenant(
    mongo_db: AsyncIOMotorDatabase,
) -> AsyncGenerator[TenantOut, None]:
    """
    Fixture that creates a real tenant document in MongoDB and returns TenantOut.
    """
    tenant_data = TenantCreate(
        company_name=f"Test Company {int(time.time())}",
        lawful_basis=LawfulBasis("legitimate_interest"),
        notice_display_mode=NoticeDisplayMode("passive"),
        retention_days=1095,
    )

    tenant = await create_tenant(tenant_data)

    yield tenant

    # Cleanup handled by mongo_db fixture teardown


@pytest_asyncio.fixture
async def seeded_system_user(
    mongo_db: AsyncIOMotorDatabase,
    seeded_tenant: TenantOut,
) -> AsyncGenerator[tuple[SystemUserOut, str], None]:
    """
    Fixture that creates a real super_admin system user with hashed password in MongoDB.
    Returns tuple of (SystemUserOut, raw_password).
    """
    raw_password = f"TestPassword123!_{int(time.time())}"

    user_data = SystemUserCreate(
        tenant_id=seeded_tenant.id or "",
        full_name="Test Super Admin",
        email=f"admin_{int(time.time())}@test.example.com",
        role=SystemUserRole.SUPER_ADMIN,
        account_status=AccountStatus.ACTIVE,
        password_hash=raw_password,
    )

    user = await create_system_user(user_data)

    yield user, raw_password

    # Cleanup handled by mongo_db fixture teardown


@pytest_asyncio.fixture
async def auth_headers(
    integration_client: AsyncClient,
    seeded_system_user: tuple[SystemUserOut, str],
) -> AsyncGenerator[dict[str, str], None]:
    """
    Fixture that logs in the seeded system user and returns auth headers dict.
    """
    user, raw_password = seeded_system_user

    # Login to get access token
    response = await integration_client.post(
        "/v1/system-users/login",
        json={
            "email": user.email,
            "password": raw_password,
        },
    )

    assert response.status_code == 200, f"Login failed: {response.text}"

    response_data = response.json()
    assert response_data["success"] is True

    access_token = response_data["data"]["access_token"]

    headers = {"Authorization": f"Bearer {access_token}"}

    yield headers
