from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import AsyncGenerator

import pytest
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
from services.system_user_service import authenticate_system_user
from schemas.system_user_schema import SystemUserLogin
from schemas.imports import SystemUserRole, AccountStatus


settings = get_settings()


@pytest_asyncio.fixture
async def mongo_db() -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    """
    Fixture that connects to real MongoDB for integration tests.
    Yields the test database and drops it on teardown.
    """
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    db_name = "visichek_test_integration"

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    yield db

    # Cleanup: drop the test database after each test
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
async def integration_app() -> FastAPI:
    """
    Fixture that returns the FastAPI app configured with real MongoDB.
    """
    return app


@pytest_asyncio.fixture
async def integration_client(integration_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """
    Fixture that provides an httpx AsyncClient for making requests to the FastAPI app.
    """
    async with AsyncClient(transport=ASGITransport(app=integration_app), base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def seeded_tenant(mongo_db: AsyncIOMotorDatabase) -> AsyncGenerator[TenantOut, None]:
    """
    Fixture that creates a real tenant document in MongoDB and returns TenantOut.
    """
    tenant_data = TenantCreate(
        company_name=f"Test Company {int(time.time())}",
        lawful_basis="legitimate_interest",
        notice_display_mode="passive",
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
        tenant_id=seeded_tenant.id,
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
        }
    )

    assert response.status_code == 200, f"Login failed: {response.text}"

    response_data = response.json()
    assert response_data["success"] is True

    access_token = response_data["data"]["access_token"]

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    yield headers
