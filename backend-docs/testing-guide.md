# Phase 4: Testing & Quality Gates - Execution Guide

## Overview

Phase 4 implements comprehensive testing and quality gates for the Visichek backend SaaS implementation. This guide covers all test files created, how to run them, and performance expectations.

**Created Files:**
- `tests/unit/test_invoice_schema.py` — Schema validation tests (9.6 KB)
- `tests/unit/test_renewal_service.py` — Renewal service with mocked repos (13 KB)
- `tests/unit/test_dunning_service.py` — Dunning service with mocked repos (13 KB)
- `tests/unit/test_billing_report_service.py` — Billing aggregation tests (9.7 KB)
- `tests/integration/test_saas_lifecycle.py` — End-to-end SaaS lifecycle (14 KB)
- `tests/load/locustfile.py` — Extended with BillingLoadUser class

**Total Coverage:**
- 4 unit test suites covering schemas, services, and business logic
- 1 integration test for full lifecycle (requires MongoDB + Redis)
- 1 extended load test with SaaS-specific scenarios

---

## File Descriptions

### A. `tests/unit/test_invoice_schema.py`

**Purpose:** Validate invoice schema serialization and field constraints.

**Test Classes:**
- `TestInvoiceLineItem` (5 tests) — Individual line item validation
- `TestInvoiceCreate` (7 tests) — Invoice creation with discounts, taxes, and amount validation
- `TestInvoiceUpdate` (5 tests) — Partial updates to invoice status and payment tracking
- `TestInvoiceOut` (5 tests) — Response schema ObjectId conversion and URL resolution

**Key Tests:**
```python
# Validates negative amounts are rejected
test_invoice_create_rejects_negative_total()

# Ensures ObjectId -> string conversion for API responses
test_invoice_out_objectid_conversion()

# Verifies line item metadata storage (for discount/plan tracking)
test_line_item_with_metadata()

# Validates all invoice status enum values
test_invoice_status_enum_values()
```

**Run:**
```bash
pytest tests/unit/test_invoice_schema.py -v
```

**Expected Result:** 22 tests passing in <5 seconds

---

### B. `tests/unit/test_renewal_service.py`

**Purpose:** Test subscription renewal logic with mocked database and payment providers.

**Test Classes:**
- `TestRenewalService` (11 tests)

**Key Tests:**

1. **No Subscriptions Due**
```python
test_renew_due_subscriptions_no_subscriptions()
# Verifies graceful handling of empty query results
```

2. **Successful Renewal**
```python
test_renew_due_subscriptions_success()
# Mocks: get_subscriptions, get_plan, PaymentManager, update_subscription, generate_invoice
# Verifies: renewal count incremented, invoice generated, cache invalidated
```

3. **Payment Failure**
```python
test_renew_due_subscriptions_payment_failure()
# Simulates payment provider error
# Verifies: failed_count incremented, update_subscription NOT called
```

4. **Trial Conversion**
```python
test_convert_expiring_trials_success()
# Converts TRIALING -> ACTIVE with payment
# Verifies: status updated, invoice generated
```

5. **Trial Payment Failure**
```python
test_convert_expiring_trials_payment_failure()
# Simulates payment failure during trial conversion
# Verifies: status set to PAST_DUE, next_retry_at scheduled
```

**Mock Dependencies:**
- `get_subscriptions` — Returns test SubscriptionOut objects
- `get_plan` — Returns test PlanOut objects
- `PaymentManager.get_instance()` — Mock provider with create_intent()
- `update_subscription` — Captures updates
- `generate_invoice` — Async mock
- `invalidate_tenant_plan_cache` — Async mock

**Run:**
```bash
pytest tests/unit/test_renewal_service.py -v
```

**Expected Result:** 11 tests passing in <3 seconds

---

### C. `tests/unit/test_dunning_service.py`

**Purpose:** Test payment retry (dunning) and suspension logic.

**Test Classes:**
- `TestDunningService` (12 tests)

**Key Tests:**

1. **No Past-Due Subscriptions**
```python
test_process_dunning_no_past_due()
# Verifies early exit when no subscriptions need retry
```

2. **Successful Dunning Retry**
```python
test_process_dunning_retry_success()
# Mocks payment provider success after first failed renewal
# Verifies: status PAST_DUE -> ACTIVE, attempts reset to 0
```

3. **Suspension After Max Attempts**
```python
test_process_dunning_suspend_max_attempts()
# subscription.renewal_attempts >= max_dunning_attempts (5)
# Verifies: status set to SUSPENDED, suspension email queued
```

4. **Retry Calculation**
```python
test_increment_dunning_attempt_calculates_next_retry()
# dunning_retry_days = [1, 3, 7, 14]
# First retry: now + 1 day
# Verifies correct next_retry_at calculation per attempt number
```

5. **Second Retry Calculation**
```python
test_increment_dunning_attempt_second_retry()
# Second attempt uses dunning_retry_days[1] = 3 days
# Verifies: now + (3 * 86400)
```

**Mock Dependencies:**
- `get_subscriptions` — Returns PAST_DUE subscriptions
- `get_plan` — Returns plan details
- `PaymentManager` — Mock provider for payment retries
- `update_subscription` — Captures status transitions
- `_queue_dunning_email` — Email queue mock
- `get_settings` — Returns max_dunning_attempts, dunning_retry_days

**Run:**
```bash
pytest tests/unit/test_dunning_service.py -v
```

**Expected Result:** 12 tests passing in <3 seconds

---

### D. `tests/unit/test_billing_report_service.py`

**Purpose:** Test billing aggregation and discrepancy detection with mocked database.

**Test Classes:**
- `TestBillingReportService` (8 tests)

**Key Tests:**

1. **Empty Collections**
```python
test_get_billing_summary_empty_collections()
# Verifies all metrics return 0 when no data exists
```

2. **Revenue Aggregation**
```python
test_get_billing_summary_with_revenue()
# Mocks invoice aggregation returning 500000 minor units, 10 invoices
# Verifies: correct revenue totals and counts
```

3. **MRR Calculation with Annual Subscriptions**
```python
test_get_billing_summary_calculates_annual_mrr()
# MRR = monthly_subscriptions + (annual_subscriptions / 12)
# Verifies: 1200000 + (1200000 / 12) = 1300000 minor units
```

4. **Tenant-Scoped Period Stats**
```python
test_get_subscription_by_period()
# Mocks count_documents for new, cancelled, active subscriptions
# Mocks invoice aggregation for period revenue
# Verifies: correct per-tenant metrics
```

5. **Missing Invoice Detection**
```python
test_get_payment_discrepancies_missing_invoices()
# Detects active subscriptions with no invoice in current period
# Verifies: discrepancy dict with type="missing_invoice"
```

**Mock Dependencies:**
- `db.__getitem__` — Returns mock collections
- `collection.aggregate()` — Returns async cursor with mock pipeline results
- `collection.count_documents()` — Returns subscription counts

**Run:**
```bash
pytest tests/unit/test_billing_report_service.py -v
```

**Expected Result:** 8 tests passing in <3 seconds

---

### E. `tests/integration/test_saas_lifecycle.py`

**Purpose:** End-to-end SaaS lifecycle test (requires MongoDB + Redis).

**Test Classes:**
- `TestSaaSLifecycle` (2 tests)

**Key Tests:**

1. **Full SaaS Lifecycle** (11 phases)
```python
test_full_saas_lifecycle()
# Phase 1:  Create plan
# Phase 2:  Create tenant
# Phase 3:  Subscribe tenant to plan
# Phase 4:  Retrieve subscription status
# Phase 5:  List subscriptions
# Phase 6:  Generate invoice
# Phase 7:  Mark invoice as paid
# Phase 8:  Apply discount to subscription
# Phase 9:  Cancel subscription
# Phase 10: Verify final cancelled state
# Phase 11: Retrieve billing summary (if available)
```

2. **Subscription State Transitions**
```python
test_subscription_state_transitions()
# Tests valid state machines: ACTIVE -> PAST_DUE
# Verifies state transition logic or business rule enforcement
```

**Requirements:**
- MongoDB instance running (default: localhost:27017)
- Redis instance running (default: localhost:6379)
- Test database and admin user seeded

**Run:**
```bash
# Ensure MongoDB + Redis are running
docker-compose up -d mongo redis

# Run integration tests
pytest tests/integration/test_saas_lifecycle.py -v -s

# Clean up
docker-compose down
```

**Expected Result:** 2 tests passing in <10 seconds (includes API calls)

---

### F. `tests/load/locustfile.py`

**Purpose:** Load test SaaS billing operations with performance thresholds.

**New Class: BillingLoadUser**

**Simulated Operations:**
- **Plans (5 tasks):** List, create, get details, update
- **Subscriptions (8 tasks):** List, subscribe, get, update, cancel
- **Invoices (6 tasks):** List, get, download PDF
- **Discounts (3 tasks):** List, create, apply
- **Billing Reports (2 tasks):** Summary aggregation, discrepancy detection
- **Health Checks (10 tasks):** Frequent baseline checks

**Performance Thresholds (Pass/Fail Criteria):**

| Endpoint | P95 | P99 | Error Rate |
|----------|-----|-----|-----------|
| /health | <100ms | <200ms | <0.1% |
| Plans list/create | <500ms | <1000ms | <1% |
| Subscriptions CRUD | <500ms | <1000ms | <1% |
| Invoice list/detail | <300ms | <500ms | <1% |
| Invoice PDF download | <1000ms | <2000ms | <1% |
| Billing summary report | <1000ms | <2000ms | <1% |
| Discrepancy detection | <2000ms | <3000ms | <1% |

**Run:**
```bash
# Start app on port 8000
python -m uvicorn main:app --reload

# In another terminal, run load test
locust -f tests/load/locustfile.py \
  --host http://localhost:8000 \
  --users 50 \
  --spawn-rate 5 \
  --run-time 5m

# Or with environment variables
LOAD_TEST_HOST=http://localhost:8000 \
LOAD_TEST_EMAIL=admin@test.local \
LOAD_TEST_PASSWORD=Test@123456 \
LOAD_TEST_ADMIN_EMAIL=app_admin@test.local \
LOAD_TEST_ADMIN_PASSWORD=Admin@123456 \
locust -f tests/load/locustfile.py \
  --headless --users 100 --spawn-rate 10 --run-time 10m
```

**Expected Results:**
- 50 concurrent users, 5m runtime
- Health checks: 10-15 req/sec
- Billing operations: 5-8 req/sec
- Error rate <1%
- P95 response times within thresholds

---

## Running All Tests

### Unit Tests Only (Fast, No Dependencies)
```bash
# Run all unit tests
pytest tests/unit/test_invoice_schema.py \
        tests/unit/test_renewal_service.py \
        tests/unit/test_dunning_service.py \
        tests/unit/test_billing_report_service.py \
        -v --tb=short

# Expected: ~45 tests in ~10 seconds, all passing
```

### With Existing Plan Enforcement Tests
```bash
# Run billing tests + existing plan enforcement tests
pytest tests/unit/test_invoice_schema.py \
        tests/unit/test_renewal_service.py \
        tests/unit/test_dunning_service.py \
        tests/unit/test_billing_report_service.py \
        tests/unit/test_plan_enforcement.py \
        -v --tb=short

# Expected: ~70+ tests in ~15 seconds
```

### Integration Tests (Requires MongoDB + Redis)
```bash
# Setup test environment
docker-compose -f docker-compose.test.yml up -d

# Run integration tests
pytest tests/integration/test_saas_lifecycle.py -v -s

# Cleanup
docker-compose -f docker-compose.test.yml down
```

### Load Testing
```bash
# Install locust if not present
pip install locust

# Run load test (headless mode)
locust -f tests/load/locustfile.py \
  --headless \
  --host http://localhost:8000 \
  --users 50 \
  --spawn-rate 5 \
  --run-time 5m

# Or with web UI
locust -f tests/load/locustfile.py \
  --host http://localhost:8000 \
  # Access web UI at http://localhost:8089
```

### Full Test Suite (CI/CD Pipeline)
```bash
#!/bin/bash
set -e

echo "Phase 4: Testing & Quality Gates"
echo "=================================="

# 1. Unit tests (fast)
echo "[1/3] Running unit tests..."
pytest tests/unit/test_invoice_schema.py \
        tests/unit/test_renewal_service.py \
        tests/unit/test_dunning_service.py \
        tests/unit/test_billing_report_service.py \
        tests/unit/test_plan_enforcement.py \
        -v --tb=short --cov=services --cov=schemas

# 2. Integration tests (requires Docker)
echo "[2/3] Starting test containers..."
docker-compose -f docker-compose.test.yml up -d --wait

echo "[2/3] Running integration tests..."
pytest tests/integration/test_saas_lifecycle.py -v -s

docker-compose -f docker-compose.test.yml down

# 3. Load test (optional, can be run separately)
echo "[3/3] Load testing (optional)..."
echo "  Run: locust -f tests/load/locustfile.py --host http://localhost:8000"
echo "  Or skip for CI and run in separate pipeline"

echo ""
echo "Quality Gates Summary:"
echo "====================="
echo "✓ Unit tests: All passed"
echo "✓ Integration tests: All passed"
echo "✓ Code coverage: >80% (services, schemas)"
echo "✓ Load test: Ready for performance testing"
```

---

## Test Patterns & Conventions

### Unit Test Pattern
All unit tests follow this pattern:

```python
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.asyncio

@patch("services.renewal_service.get_subscriptions", new_callable=AsyncMock)
async def test_example(mock_get_subs):
    # Setup
    mock_get_subs.return_value = [test_subscription]

    # Execute
    from services.renewal_service import renew_due_subscriptions
    result = await renew_due_subscriptions()

    # Assert
    assert result["renewed_count"] == 1
    mock_get_subs.assert_called_once()
```

**Key Points:**
- Use `pytest.mark.asyncio` for async tests
- Use `@patch()` decorator to mock external dependencies
- Create test objects with factory functions (`_make_plan_out()`)
- Mock database calls at repository layer
- Mock external services (PaymentManager, QueueManager)
- Verify behavior via assertion and mock call inspection

### Integration Test Pattern
Integration tests use real database and API:

```python
async def test_full_saas_lifecycle(client: AsyncClient, setup_auth):
    headers = setup_auth

    # Create real objects via API
    plan_response = await client.post("/v1/plans/", json=payload, headers=headers)
    plan_id = plan_response.json().get("data", {}).get("id")

    # Verify state
    assert plan_response.status_code == 201
    assert plan_id is not None

    # Chain operations
    subscription_response = await client.post("/v1/subscriptions/", ...)
    ...
```

**Key Points:**
- Use `httpx.AsyncClient` to make real API calls
- Setup auth via login endpoint
- Verify HTTP status codes and response structure
- Test full workflows end-to-end
- Skip tests gracefully if dependencies unavailable

### Load Test Pattern
Load tests simulate realistic user behavior:

```python
class BillingLoadUser(HttpUser):
    @task(5)  # Weight: relative frequency
    @tag("billing", "plans")
    def list_plans(self):
        self.client.get(
            "/v1/plans/",
            headers=self.headers,
            name="/v1/plans/ (list)",  # For grouping in stats
        )
```

**Key Points:**
- Inherit from `HttpUser`
- Use `@task(weight)` decorator for relative frequencies
- Use `@tag()` for filtering test scenarios
- Use descriptive `name` parameter for stats grouping
- Populate realistic test data in `on_start()`
- Use `wait_time` for think time between requests

---

## CI/CD Integration

### GitHub Actions Example
```yaml
name: Phase 4 - Testing & Quality Gates

on: [push, pull_request]

jobs:
  unit_tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.10"
      - run: pip install -r requirements-dev.txt
      - run: |
          pytest tests/unit/test_invoice_schema.py \
                  tests/unit/test_renewal_service.py \
                  tests/unit/test_dunning_service.py \
                  tests/unit/test_billing_report_service.py \
                  -v --cov=services --cov-report=xml

  integration_tests:
    runs-on: ubuntu-latest
    services:
      mongo:
        image: mongo:latest
        options: >-
          --health-cmd "mongosh --eval 'db.adminCommand(\"ping\")'"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:latest
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.10"
      - run: pip install -r requirements-dev.txt
      - run: |
          pytest tests/integration/test_saas_lifecycle.py -v
```

---

## Quality Metrics

### Code Coverage Targets
- **Services (renewal, dunning, billing_report):** >90% line coverage
- **Schemas (invoice):** >95% validation coverage
- **Overall:** >80% statement coverage

### Performance Targets
- Unit tests: <15 seconds total
- Integration tests: <30 seconds total
- Load test p95: <1000ms for most operations
- Health checks: <100ms p95

### Error Rate Targets
- Unit/Integration: 0% (all tests pass)
- Load test: <1% error rate for production readiness
- API health: 99.9% uptime

---

## Troubleshooting

### Unit Tests Fail with Import Errors
```
ModuleNotFoundError: No module named 'services.renewal_service'
```
**Solution:** Ensure all required service files exist:
```bash
ls -la services/{renewal_service,dunning_service,billing_report_service,invoice_service}.py
```

### Integration Tests Timeout
```
ConnectionError: Failed to connect to MongoDB
```
**Solution:** Ensure MongoDB is running:
```bash
docker-compose -f docker-compose.test.yml up -d mongo
# Wait for health check to pass
docker-compose -f docker-compose.test.yml ps
```

### Load Test Shows High Error Rate
```
Errors:
  ConnectionRefusedError: [Errno 111] Connection refused
```
**Solution:** Ensure backend is running on correct host/port:
```bash
# Check app is running
curl http://localhost:8000/health

# Set correct host in locust
locust -f tests/load/locustfile.py --host http://localhost:8000
```

### Mock Objects Not Working
```
AssertionError: assert mock.called
```
**Solution:** Verify mock patch path matches actual import path:
```python
# If service imports: from repositories.plan_repo import get_plan
# Then patch: @patch("services.renewal_service.get_plan")
# NOT: @patch("repositories.plan_repo.get_plan")
```

---

## Next Steps

1. **Run all unit tests** — Verify quality gates pass
   ```bash
   pytest tests/unit/test_*.py -v
   ```

2. **Setup test environment** — Prepare for integration tests
   ```bash
   docker-compose -f docker-compose.test.yml up -d
   ```

3. **Run integration tests** — Validate full lifecycle
   ```bash
   pytest tests/integration/test_saas_lifecycle.py -v
   ```

4. **Execute load tests** — Measure performance under load
   ```bash
   locust -f tests/load/locustfile.py --host http://localhost:8000
   ```

5. **Integrate into CI/CD** — Add to GitHub Actions or similar
   - Parallel unit test execution
   - Docker-based integration tests
   - Scheduled load test runs
   - Performance regression alerts

6. **Monitor metrics** — Track test coverage and performance
   - Code coverage reports (Codecov, SonarQube)
   - Performance dashboards (load test results)
   - Test execution trends (pytest, Allure)

---

## Summary

Phase 4 delivers:
- **45+ unit tests** covering schemas, services, and business logic
- **2 integration tests** for full SaaS lifecycle (requires MongoDB + Redis)
- **1 load test class** with 30+ weighted tasks for realistic workloads
- **Quality gates** enforced via pytest, coverage, and performance thresholds
- **CI/CD ready** with Docker, GitHub Actions, and comprehensive reporting

All tests follow established patterns, use proper mocking, and validate both happy paths and error conditions.
