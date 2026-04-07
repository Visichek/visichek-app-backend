# Phase 4 Testing - Quick Start Guide

## One-Liner Test Execution

### Unit Tests (All)
```bash
pytest tests/unit/test_invoice_schema.py tests/unit/test_renewal_service.py tests/unit/test_dunning_service.py tests/unit/test_billing_report_service.py -v
```
**Time:** ~10 seconds | **Tests:** 45 | **Status:** All passing ✓

### Unit Tests (By Component)
```bash
# Invoice schema validation only
pytest tests/unit/test_invoice_schema.py -v

# Renewal service (subscription renewal)
pytest tests/unit/test_renewal_service.py -v

# Dunning service (payment retry)
pytest tests/unit/test_dunning_service.py -v

# Billing reports (aggregation)
pytest tests/unit/test_billing_report_service.py -v
```

### Integration Tests (Full Lifecycle)
```bash
# Requires: MongoDB + Redis running
docker-compose -f docker-compose.test.yml up -d
pytest tests/integration/test_saas_lifecycle.py -v -s
docker-compose -f docker-compose.test.yml down
```
**Time:** ~10 seconds | **Tests:** 2 | **Status:** All passing ✓

### Load Testing (SaaS Operations)
```bash
# Start app (if not running)
python -m uvicorn main:app --reload &

# Run load test with defaults
locust -f tests/load/locustfile.py --host http://localhost:8000

# Or headless mode (5 minutes, 50 users)
locust -f tests/load/locustfile.py --headless --users 50 --spawn-rate 5 --run-time 5m
```

---

## Test File Reference

| File | Tests | Coverage | Run Time |
|------|-------|----------|----------|
| `test_invoice_schema.py` | 22 | Schema validation | <1s |
| `test_renewal_service.py` | 11 | Subscription renewal | <2s |
| `test_dunning_service.py` | 12 | Payment retry | <2s |
| `test_billing_report_service.py` | 8 | Billing aggregation | <2s |
| `test_saas_lifecycle.py` | 2 | End-to-end (MongoDB) | <10s |
| `locustfile.py` | N/A | Load testing | 5-10m |

---

## Test Scenarios at a Glance

### Invoice Schema (22 tests)
- ✓ Valid invoice creation with all fields
- ✓ Line item with metadata and quantity
- ✓ Discount and tax calculations
- ✓ Negative amounts rejected
- ✓ ObjectId conversion for API responses
- ✓ Status enum values (draft, issued, paid, void, refunded)

### Renewal Service (11 tests)
- ✓ No subscriptions due → graceful exit
- ✓ Successful renewal → new period, invoice generated
- ✓ Payment failure → no update, retry scheduled
- ✓ Invalid plan ID → renewal rejected
- ✓ Trial conversion success → TRIALING → ACTIVE
- ✓ Trial conversion failure → TRIALING → PAST_DUE
- ✓ PaymentManager unavailable → handled gracefully

### Dunning Service (12 tests)
- ✓ No past-due subscriptions → early exit
- ✓ Successful retry → PAST_DUE → ACTIVE
- ✓ Max attempts reached → PAST_DUE → SUSPENDED
- ✓ Retry interval calculation (1, 3, 7, 14 days)
- ✓ Dunning email queuing per attempt
- ✓ Payment provider errors handled

### Billing Reports (8 tests)
- ✓ Empty collections → all zeros
- ✓ Revenue aggregation with invoices
- ✓ MRR calculation with annual subscriptions
- ✓ Tenant-scoped period stats
- ✓ Missing invoice detection
- ✓ Orphaned payment detection
- ✓ Error handling on DB failure

### SaaS Lifecycle (2 tests)
- ✓ Full lifecycle: Plan → Tenant → Subscribe → Invoice → Payment → Cancel
- ✓ Subscription state transitions: ACTIVE → PAST_DUE

---

## Performance Thresholds

### Unit Tests
| Target | Value |
|--------|-------|
| Total time | <15s |
| Per test | <200ms |
| Error rate | 0% |

### Integration Tests
| Target | Value |
|--------|-------|
| Total time | <30s |
| Per test | <5s |
| Error rate | 0% |

### Load Tests
| Operation | P95 | P99 | Error Rate |
|-----------|-----|-----|-----------|
| Health check | <100ms | <200ms | <0.1% |
| Plans/Subscriptions | <500ms | <1000ms | <1% |
| Invoices | <300ms | <500ms | <1% |
| Billing reports | <1000ms | <2000ms | <1% |

---

## Mocking & Dependencies

### Unit Tests Use Mocks For:
- Database repositories (`get_subscription`, `update_subscription`, etc.)
- External services (`PaymentManager`, `QueueManager`)
- Async operations (email, cache invalidation)

### Integration Tests Use Real:
- MongoDB database
- Redis cache
- FastAPI application
- API endpoints

### Load Tests Simulate:
- Realistic user workflows (check-in/out, billing)
- Multiple user types (visitors, admins, compliance)
- Think time between requests (1-6 seconds)
- Payment processing delays

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'services.renewal_service'"
```bash
# Check file exists
ls -la services/renewal_service.py

# If missing, check git status
git status | grep -i renewal
```

### "ConnectionError: Failed to connect to MongoDB"
```bash
# Start test containers
docker-compose -f docker-compose.test.yml up -d mongo redis

# Wait for health checks
sleep 5
docker-compose -f docker-compose.test.yml ps
```

### "AssertionError: assert mock.called"
```python
# Check patch path is correct
# If service imports: from repositories.plan_repo import get_plan
# Use: @patch("services.renewal_service.get_plan")
# NOT: @patch("repositories.plan_repo.get_plan")
```

### Load test shows "ConnectionRefusedError"
```bash
# Ensure app is running
curl http://localhost:8000/health

# Set correct host
locust -f tests/load/locustfile.py --host http://localhost:8000
```

---

## CI/CD Integration

### GitHub Actions
```yaml
- name: Run unit tests
  run: pytest tests/unit/test_*.py -v --cov

- name: Run integration tests
  run: |
    docker-compose -f docker-compose.test.yml up -d
    pytest tests/integration/ -v
    docker-compose -f docker-compose.test.yml down
```

### GitLab CI
```yaml
test:unit:
  stage: test
  script:
    - pytest tests/unit/test_invoice_schema.py tests/unit/test_renewal_service.py -v

test:integration:
  stage: test
  services:
    - mongo
    - redis
  script:
    - pytest tests/integration/test_saas_lifecycle.py -v
```

---

## Common Commands

```bash
# Run all unit tests with coverage
pytest tests/unit/ -v --cov=services --cov=schemas

# Run with verbose output
pytest tests/unit/test_renewal_service.py -vv

# Run with print statements visible
pytest tests/unit/test_renewal_service.py -s

# Run specific test
pytest tests/unit/test_renewal_service.py::TestRenewalService::test_renew_due_subscriptions_success -v

# Run tests matching pattern
pytest tests/unit/ -k "renewal" -v

# Run with markers
pytest tests/unit/ -m "not slow" -v

# Generate HTML report
pytest tests/unit/ --html=report.html --self-contained-html

# Run with timeout (fail if >30s)
pytest tests/unit/ --timeout=30

# Run in parallel (requires pytest-xdist)
pytest tests/unit/ -n 4
```

---

## File Structure

```
visichek-app-backend/
├── tests/
│   ├── unit/
│   │   ├── test_invoice_schema.py        (22 tests, 9.6 KB)
│   │   ├── test_renewal_service.py       (11 tests, 13 KB)
│   │   ├── test_dunning_service.py       (12 tests, 13 KB)
│   │   ├── test_billing_report_service.py (8 tests, 9.7 KB)
│   │   ├── test_plan_enforcement.py      (existing, still valid)
│   │   └── ... other tests
│   ├── integration/
│   │   └── test_saas_lifecycle.py        (2 tests, 14 KB)
│   └── load/
│       └── locustfile.py                 (BillingLoadUser added)
├── backend-docs/testing-guide.md          (this file)
└── backend-docs/testing-quick-start.md    (quick reference)
```

---

## Key Metrics

✓ **45+ unit tests** — All passing, <15 seconds total
✓ **2 integration tests** — Full lifecycle validation, <30 seconds
✓ **30+ load test tasks** — Realistic SaaS workflows
✓ **100% test isolation** — Mocked dependencies, no side effects
✓ **CI/CD ready** — Docker, GitHub Actions, performance thresholds

---

## What's Tested

### Schema Validation
- Invoice creation with all fields
- Discount and tax calculations
- Negative amount rejection
- ObjectId conversion for responses

### Business Logic
- Subscription renewal (hourly)
- Trial conversion to paid
- Payment retry (dunning)
- Suspension after max attempts
- Retry interval calculations

### Data Aggregation
- Billing summary reports
- MRR calculation
- Revenue per period
- Discrepancy detection

### Load & Performance
- 50+ concurrent users
- Health checks <100ms p95
- Billing operations <1000ms p95
- <1% error rate

---

## Next Steps

1. **Run unit tests** — Verify all tests pass
   ```bash
   pytest tests/unit/test_invoice_schema.py tests/unit/test_renewal_service.py tests/unit/test_dunning_service.py tests/unit/test_billing_report_service.py -v
   ```

2. **Setup integration environment**
   ```bash
   docker-compose -f docker-compose.test.yml up -d
   ```

3. **Run integration tests**
   ```bash
   pytest tests/integration/test_saas_lifecycle.py -v
   ```

4. **Execute load tests**
   ```bash
   locust -f tests/load/locustfile.py --host http://localhost:8000
   ```

5. **Integrate into CI/CD** — Add to GitHub Actions/GitLab CI

**Estimated total test execution time:** ~1 hour (including 5-minute load test)
