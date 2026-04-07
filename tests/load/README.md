# Visichek Load Testing with Locust

This directory contains load testing infrastructure for the Visichek backend using Locust, a modern load testing framework. The load tests simulate realistic user workflows including visitor check-in/check-out, dashboard queries, and appointment management.

## Prerequisites

Before running load tests, ensure the following are running:

1. **Visichek Backend** - FastAPI server running (default: `http://localhost:8000`)
2. **MongoDB** - Database for data persistence
3. **Redis** - Cache and queue broker
4. **Test Data** - Run `setup_load_test_data.py` to seed the database

## Installation

Install Locust in your Python environment:

```bash
pip install locust
```

This will be installed as part of the project's test dependencies if you run:

```bash
pip install -r requirements.txt
```

## Setup: Seed Test Data

Before running load tests, populate the database with test users and data:

```bash
python tests/load/setup_load_test_data.py
```

This script will:
- Create a test tenant
- Create 3-5 test departments
- Create a `super_admin` system user for authentication
- Create 50+ visitor profiles for realistic data
- Create sample appointments
- Print credentials and IDs for use in load tests

Sample output:
```
=== Load Test Data Seeded Successfully ===
Tenant ID: 64f1a2b3c4d5e6f7a8b9c0d1
Department IDs: [64f1a2b3c4d5e6f7a8b9c0d2, ...]
Super Admin User:
  Email: loadtest_super_admin@visichek.test
  Password: LoadTest@123
```

## Running Load Tests

### Interactive Web UI (Recommended for Development)

Start the Locust web interface:

```bash
cd tests/load
locust -f locustfile.py --host=http://localhost:8000
```

Then open your browser to `http://localhost:8089` and:

1. Enter the number of users to spawn (e.g., 50)
2. Enter the spawn rate (e.g., 5 users per second)
3. Click "Start swarming"
4. Monitor requests, response times, and errors in real-time
5. View charts and detailed statistics

### Headless CLI Mode

Run load tests without the web UI:

```bash
cd tests/load
locust -f locustfile.py --host=http://localhost:8000 --headless -u 50 -r 5 -t 60s
```

Parameters:
- `-u 50` - Spawn 50 concurrent users
- `-r 5` - Spawn rate of 5 users per second
- `-t 60s` - Run test for 60 seconds

### Common CLI Scenarios

**Light load test (5 users, 1 minute):**
```bash
locust -f locustfile.py --host=http://localhost:8000 --headless -u 5 -r 1 -t 60s
```

**Medium load test (25 users, 5 minutes):**
```bash
locust -f locustfile.py --host=http://localhost:8000 --headless -u 25 -r 5 -t 300s
```

**Heavy load test (100 users, 10 minutes):**
```bash
locust -f locustfile.py --host=http://localhost:8000 --headless -u 100 -r 10 -t 600s
```

**Stress test (ramp up to 500 users):**
```bash
locust -f locustfile.py --host=http://localhost:8000 --headless -u 500 -r 25 -t 600s
```

## Environment Variables

Configure load tests via environment variables:

```bash
export LOAD_TEST_HOST=http://localhost:8000
export LOAD_TEST_EMAIL=loadtest_super_admin@visichek.test
export LOAD_TEST_PASSWORD=LoadTest@123
```

Or set them inline:

```bash
LOAD_TEST_EMAIL=loadtest_super_admin@visichek.test LOAD_TEST_PASSWORD=LoadTest@123 \
locust -f locustfile.py --host=http://localhost:8000 --headless -u 50 -r 5 -t 300s
```

## Interpreting Results

### Key Metrics

1. **RPS (Requests Per Second)** - Overall throughput
   - Higher is better; watch for degradation under load
   - Healthy RPS: 100+ requests/sec for 50 concurrent users

2. **Response Time (ms)** - API latency
   - p50 (median): Should be <200ms for most endpoints
   - p95: Should be <500ms
   - p99: Should be <1000ms
   - Watch for spikes indicating bottlenecks

3. **Error Rate** - Request failures
   - Target: <1% errors
   - Monitor 5xx errors (server issues) vs 4xx errors (client issues)

4. **Users** - Number of active concurrent users
   - Should match your target load level
   - Watch for user spawn failures

### Example Output

```
Type     Name                          # reqs      # fails |    Avg     Min     Max    Med   | req/s  failures/s
-----    ----                          ------      ------- |    ---     ---     ---    ---   | -----  -----------
POST     /v1/appointments               250         3  |    145      54     892    130   | 4.17    0.05
POST     /v1/departments/ (create)       50         0  |    320     210     675    310   | 0.83    0.00
GET      /v1/dashboard/stats            350         1  |    120      45     520    110   | 5.83    0.02
GET      /v1/dashboard/visitors         180         0  |    135      60     450    125   | 3.00    0.00
POST     /v1/system-users/login         150         2  |    250     120     890    240   | 2.50    0.03
GET      /v1/visitors/active            420         4  |    110      35     680     95   | 7.00    0.07
POST     /v1/visitors/check-in          300         5  |    200      80     950    180   | 5.00    0.08
GET      /v1/visitors/sessions          150         2  |    130      55     620    115   | 2.50    0.03
POST     /v1/visitors/check-out         280         3  |    180      70     800    165   | 4.67    0.05
-----    ----                          ------      ------- |    ---     ---     ---    ---   | -----  -----------
         Aggregated                    2130        20     |    148      35     950    125   | 35.50   0.33
```

### What to Look For

- **Check-in endpoint bottleneck?** Response times spike for POST `/v1/visitors/check-in`
- **Dashboard slow?** GET `/v1/dashboard/stats` and `/v1/dashboard/visitors` have high p99
- **Auth scaling issue?** POST `/v1/system-users/login` fails under load
- **Database contention?** Errors increase with concurrent users; add read replicas or optimize queries

## Scaling Tips

### Increase Load Gradually

Don't jump to 500 users; ramp up:
1. Start with 10 users for 60s
2. Increase to 25 users for 120s
3. Increase to 50 users for 300s
4. Identify bottlenecks at each level

### Adjust User Wait Time

The default wait time is 1-3 seconds between requests. To simulate busier users:

Edit `locustfile.py` and change:
```python
wait_time = between(0.5, 1)  # More aggressive
```

### Disable Specific Task Groups

Use Locust tags to run subsets of tasks:

```bash
locust -f locustfile.py --tags check_in dashboard -u 50 -r 5 -t 300s
```

Available tags:
- `check_in` - Visitor check-in operations
- `check_out` - Visitor check-out operations
- `list` - List/query operations (active visitors, sessions)
- `dashboard` - Dashboard stats and logs
- `appointment` - Appointment CRUD operations

### Monitor Backend Metrics

While running load tests, monitor:

1. **CPU & Memory** - Is the server saturating?
   ```bash
   top
   ```

2. **MongoDB Performance** - Check slow queries
   ```bash
   db.currentOp()
   db.system.profile.find().sort({ts:-1}).limit(5)
   ```

3. **Redis Memory** - Cache hit/miss rates
   ```bash
   redis-cli INFO stats
   ```

4. **Network I/O** - Is bandwidth the constraint?
   ```bash
   iftop
   ```

## Troubleshooting

### "Connection refused" errors

Ensure the backend is running:
```bash
curl http://localhost:8000/docs
```

### Authentication failures

Verify test credentials exist:
```bash
python tests/load/setup_load_test_data.py
```

Check that you're using the correct password set by the seed script.

### High error rates during load test

1. **Check server logs** for 5xx errors
2. **Verify database connectivity** (MongoDB must be running)
3. **Check Redis** is available for rate limiting
4. **Reduce user count** and try again
5. **Profile slow endpoints** with a smaller load (5-10 users)

### Memory leaks or memory growth

Monitor the backend process during load tests:
```bash
watch -n 1 'ps aux | grep python | grep main.py'
```

Increase backend memory limit or optimize hot paths.

## Advanced: Custom Locust Scenarios

You can extend `locustfile.py` with custom scenarios:

```python
@task(5)
@tag("custom")
def my_custom_workflow(self) -> None:
    """Example: Complex multi-step workflow."""
    # 1. Check in visitor
    # 2. Query active visitors
    # 3. Update dashboard filters
    # 4. Check out visitor
    pass
```

Then run it:
```bash
locust -f locustfile.py --tags custom -u 20 -r 2 -t 120s
```

## References

- [Locust Documentation](https://docs.locust.io/)
- [Locust API Reference](https://docs.locust.io/en/stable/api.html)
- [HTTP Client (HttpLocust)](https://docs.locust.io/en/stable/clients.html)
