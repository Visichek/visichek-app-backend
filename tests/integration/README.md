# Integration Tests - Visichek Backend

This directory contains comprehensive integration tests for the Visichek backend. These tests work with **real MongoDB and Redis** instances, testing the full application flow including HTTP requests, database operations, and response envelope formatting.

## Overview

The integration tests are organized into multiple test classes that cover critical business flows:

- **TestAuthFlow**: System user authentication (signup, login, token refresh)
- **TestVisitorCheckInCheckOutFlow**: Complete visitor lifecycle (check-in, check-out, duration tracking)
- **TestTenantDepartmentFlow**: Tenant and department management
- **TestComplianceFlow**: Privacy notices and Data Subject Requests (DSRs)
- **TestAppointmentFlow**: Appointment creation and visitor fulfillment

## Files

### conftest.py

Provides pytest fixtures for integration testing:

- **mongo_db**: Connects to real MongoDB test database (auto-drops on teardown)
- **redis_client**: Connects to real Redis (auto-flushes on teardown)
- **integration_app**: FastAPI application instance configured for real MongoDB
- **integration_client**: AsyncClient for making HTTP requests to the app
- **seeded_tenant**: Creates a real tenant document in MongoDB
- **seeded_system_user**: Creates a real super_admin user with hashed password
- **auth_headers**: Logs in the seeded user and provides Authorization headers

All database fixtures use function-level scope to ensure clean state per test.

### test_flows.py

Contains integration test classes and methods:

#### TestAuthFlow
- `test_system_user_signup_login_refresh_flow`: Full auth cycle with token refresh
- `test_login_with_wrong_password_returns_401`: Invalid credentials handling
- `test_expired_token_rejected`: Invalid token handling

#### TestVisitorCheckInCheckOutFlow
- `test_full_visitor_lifecycle`: Complete check-in, verify, check-out flow
- `test_check_in_without_required_fields_fails`: Validation error handling
- `test_check_out_with_invalid_badge_token_fails`: 404 handling for invalid sessions

#### TestTenantDepartmentFlow
- `test_create_tenant_and_departments`: CRUD operations on departments
- `test_duplicate_tenant_name_rejected`: Conflict handling

#### TestComplianceFlow
- `test_privacy_notice_lifecycle`: Notice versioning and deactivation
- `test_dsr_lifecycle`: Data Subject Request creation and status updates

#### TestAppointmentFlow
- `test_appointment_create_and_fulfill`: Appointment creation and fulfillment

## Setup

### Prerequisites

1. **MongoDB**: Running on `mongodb://localhost:27017` (or set via `MONGO_URL` env var)
2. **Redis**: Running on `redis://localhost:6379` (or set via `REDIS_URL` env var)
3. **Python dependencies**: Install from `requirements.txt`

### Environment Configuration

Create a `.env.test` file in the project root (same level as `.env.example`):

```bash
# MongoDB (test database will be automatically created/dropped)
MONGO_URL=mongodb://localhost:27017
DB_NAME=visichek_test  # Will be overridden to visichek_test_integration

# Redis (test keys will be auto-flushed)
REDIS_URL=redis://localhost:6379

# JWT & Crypto
SECRET_KEY=test-secret-key-for-testing-only
SESSION_SECRET_KEY=test-session-secret-for-testing-only

# App Config
ENV=testing
CORS_ORIGINS=http://localhost:3000,http://test
```

The conftest.py will automatically:
- Override `DB_TYPE` to "mongodb"
- Override `DB_NAME` to "visichek_test_integration"
- Load variables from `.env.test`

## Running Tests

### Run all integration tests:
```bash
pytest tests/integration/ -v
```

### Run a specific test class:
```bash
pytest tests/integration/test_flows.py::TestAuthFlow -v
```

### Run a single test:
```bash
pytest tests/integration/test_flows.py::TestAuthFlow::test_system_user_signup_login_refresh_flow -v
```

### Run with detailed output:
```bash
pytest tests/integration/test_flows.py -vv -s
```

### Run with coverage:
```bash
pytest tests/integration/ --cov=. --cov-report=html
```

## Response Envelope Format

All API responses follow the standard envelope format:

### Success Response (201/200)
```json
{
  "success": true,
  "message": "Operation successful",
  "data": { /* resource object */ },
  "meta": { /* optional pagination metadata */ },
  "requestId": "unique-request-id"
}
```

### Error Response (4xx/5xx)
```json
{
  "success": false,
  "message": "Error description",
  "code": "ERROR_CODE",
  "requestId": "unique-request-id"
}
```

Tests verify both the envelope structure and the nested data.

## Key Testing Patterns

### 1. Using Seeded Fixtures
```python
async def test_something(
    integration_client: AsyncClient,
    seeded_tenant: TenantOut,
    auth_headers: dict[str, str],
):
    # seeded_tenant: pre-created tenant in MongoDB
    # auth_headers: authenticated headers {"Authorization": "Bearer <token>"}
    response = await integration_client.post(
        "/v1/some-endpoint",
        json={"data": "value"},
        headers=auth_headers,
    )
```

### 2. Database Verification
```python
# Make HTTP request
response = await integration_client.post(...)

# Verify HTTP response
assert response.status_code == 201
assert response.json()["success"] is True

# Verify database state
from repositories.some_repo import get_something
db_record = await get_something({"_id": ObjectId(resource_id)})
assert db_record is not None
assert db_record.status == "expected_status"
```

### 3. Testing Error Cases
```python
response = await integration_client.post(
    "/v1/endpoint",
    json={"incomplete": "payload"},  # Missing required fields
    headers=auth_headers,
)

assert response.status_code >= 400
data = response.json()
assert data["success"] is False
```

## Cleanup

- **MongoDB**: The test database `visichek_test_integration` is automatically dropped after each test (via `mongo_db` fixture)
- **Redis**: Test keys are automatically flushed after each test (via `redis_client` fixture)
- **No manual cleanup needed**: Fixtures handle all cleanup in teardown

## Debugging Failed Tests

### Check MongoDB Connection
```bash
mongosh "mongodb://localhost:27017"
# In mongosh: show databases
# You should NOT see visichek_test_integration (it gets dropped)
```

### Check Redis Connection
```bash
redis-cli
# In redis-cli: PING (should return PONG)
# DBSIZE (should be 0 after tests)
```

### Check Application Logs
```bash
pytest tests/integration/test_flows.py::TestAuthFlow -vv -s
# -s enables print statements and stdout
```

### Check Test Database State Before Teardown
Add a breakpoint or print the database state before the fixture yields:

```python
@pytest_asyncio.fixture
async def mongo_db() -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    # ... setup ...
    yield db
    # Add debug here if needed before drop
    # ...
```

## Common Issues

### "connection refused" on MongoDB
- Ensure MongoDB is running: `mongosh "mongodb://localhost:27017"`
- Check `MONGO_URL` environment variable

### "connection refused" on Redis
- Ensure Redis is running: `redis-cli PING`
- Check `REDIS_URL` environment variable

### "visichek_test_integration database not empty" after test
- Manual cleanup: `mongosh "mongodb://localhost:27017/visichek_test_integration" --eval "db.dropDatabase()"`

### Token/Auth failures in tests
- Verify `SECRET_KEY` is set in `.env.test`
- Ensure `seeded_system_user` fixture runs before `auth_headers`
- Check password hashing: `security.hash.hash_password()` is called in SystemUserCreate

## Contributing New Tests

When adding new integration tests:

1. Follow the existing test class structure
2. Use async/await throughout
3. Use `@pytest.mark.integration` and `@pytest.mark.asyncio` decorators
4. Verify both HTTP response AND database state
5. Check the response envelope format (success, message, data)
6. Use seeded fixtures for common setup (tenant, user, etc.)
7. Let fixtures handle cleanup (don't manually delete in test body)
8. Add docstrings explaining the test purpose

Example template:
```python
@pytest.mark.integration
@pytest.mark.asyncio
class TestNewFeature:
    async def test_feature_description(
        self,
        integration_client: AsyncClient,
        mongo_db: AsyncIOMotorDatabase,
        seeded_tenant: TenantOut,
        auth_headers: dict[str, str],
    ):
        """Full description of what this test verifies."""
        # Setup
        # ...

        # Execute
        response = await integration_client.post(...)

        # Assert HTTP response
        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True

        # Assert database state
        # ...
```
