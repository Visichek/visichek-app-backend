from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncGenerator, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

# Load test environment variables FIRST before importing any app modules
env_test_path = Path(__file__).parent.parent / ".env.test"
if env_test_path.exists():
    load_dotenv(env_test_path, override=True)

# Add the backend root to the path
backend_root = Path(__file__).parent.parent
sys.path.insert(0, str(backend_root))

from main import app
from security.principal import AuthPrincipal
from core.settings import get_settings


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
async def app_instance() -> FastAPI:
    return app


@pytest.fixture
async def client(app_instance: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=cast(object, app_instance)),  # type: ignore[arg-type]
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
def mock_db():
    mock = AsyncMock()
    return mock


@pytest.fixture
def mock_settings():
    mock = MagicMock()
    mock.DB_TYPE = "sqlite"
    mock.DB_NAME = "test_visichek"
    mock.ENV = "testing"
    mock.SECRET_KEY = "test-secret-key"
    mock.SESSION_SECRET_KEY = "test-session-secret-key"
    mock.REDIS_HOST = "localhost"
    mock.REDIS_PORT = 6379
    mock.CORS_ORIGINS = "http://localhost:3000"
    mock.STORAGE_BACKEND = "local"
    mock.STORAGE_LOCAL_ROOT = "/tmp/test_uploads"
    mock.PAYMENT_DEFAULT_PROVIDER = "stripe"
    return mock


@pytest.fixture
def auth_principal_admin() -> AuthPrincipal:
    from bson import ObjectId

    return AuthPrincipal(
        user_id=str(ObjectId()),
        role="admin",
        access_token_id=str(ObjectId()),
        jwt_token="test-token-admin",
        allow_expired=False,
    )


@pytest.fixture
def auth_principal_user() -> AuthPrincipal:
    from bson import ObjectId

    return AuthPrincipal(
        user_id=str(ObjectId()),
        role="user",
        access_token_id=str(ObjectId()),
        jwt_token="test-token-user",
        allow_expired=False,
    )


@pytest.fixture
def auth_principal_super_admin() -> AuthPrincipal:
    from bson import ObjectId

    return AuthPrincipal(
        user_id=str(ObjectId()),
        role="super_admin",
        access_token_id=str(ObjectId()),
        jwt_token="test-token-super-admin",
        allow_expired=False,
    )


@pytest.fixture
def auth_principal_receptionist() -> AuthPrincipal:
    from bson import ObjectId

    return AuthPrincipal(
        user_id=str(ObjectId()),
        role="receptionist",
        access_token_id=str(ObjectId()),
        jwt_token="test-token-receptionist",
        allow_expired=False,
    )
