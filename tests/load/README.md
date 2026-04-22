# Visichek Load Testing with Locust

This directory contains load testing infrastructure for the Visichek backend using Locust, a modern load testing framework. The load tests simulate realistic user workflows including visitor check-in/check-out, dashboard queries, and appointment management.

## Prerequisites

Before running load tests, ensure the following are running:

1. **Visichek Backend** — the `web` container from `docker-compose.yml` (listens on `7861` inside the container; published at `http://<host>:7861`)
2. **MongoDB** — database for data persistence
3. **Redis** — cache and queue broker
4. **Celery workers** — `worker-writes`, `worker-precompute`, `worker-gates`, `worker` (started by `docker compose up`)
5. **A super_admin with the credentials the setup script expects** — see "Seed an initial super_admin" below

> **Port note:** earlier versions of this README referenced `http://localhost:7861`. The Docker compose stack actually exposes the backend on **`7861`**. Use that everywhere (both inside the container and from the host).

## Recommended path: run load tests from inside the `web` container

`httpx` is already installed in the `web` image via `fastapi[all]`, and `locust` is listed in `requirements.txt` (under `# Load testing`) so a freshly-built image already has both. The steps below assume you have `docker compose up -d` running.

```bash
# 1. Shell into the running web container
docker compose exec web bash
```

From here, every command in the remaining steps runs **inside the container**.

### Seed an initial super_admin

The load-test setup script defaults to `loadtest_super_admin@visichek.com` / `LoadTest@123`, and both it and Locust respect `LOAD_TEST_EMAIL` / `LOAD_TEST_PASSWORD` overrides. Seed a matching super_admin once per environment via `seed.py`:

```bash
SUPER_ADMIN_EMAIL=loadtest_super_admin@visichek.com \
SUPER_ADMIN_PASSWORD=LoadTest@123 \
python seed.py
```

`seed.py` is idempotent — re-running it is safe.

> **Don't use a `.test`, `.example`, `.invalid`, or `.localhost` TLD.** Pydantic's email validator rejects RFC 2606 reserved TLDs, so `loadtest_super_admin@visichek.test` fails at `SystemUserCreate` with "not a valid email address". Stick to a normal `.com`/`.io`/etc. domain — no DNS lookup happens, so the domain doesn't need to resolve.

### Install locust (only needed if your image pre-dates the requirements.txt update)

```bash
pip install locust
```

Add it to the compose image permanently by rebuilding:

```bash
docker compose build web worker-writes worker-precompute worker-gates worker
docker compose up -d
```

### Seed test data

```bash
LOAD_TEST_HOST=http://localhost:7861 \
python tests/load/setup_load_test_data.py
```

This script will:
- Create a test tenant
- Create 3-5 test departments
- Create a `super_admin` system user for authentication
- Create 50+ visitor profiles for realistic data
- Create sample appointments
- Print credentials and IDs for use in load tests

> **Writes are async.** POST `/v1/departments/`, `/v1/visitors`, etc. return `202 Accepted` with a pre-assigned `id`. The setup script already accepts `201 or 202`. If downstream reads look empty immediately after seeding, give `worker-writes` a few seconds to drain before starting Locust.

Sample output:
```
=== Load Test Data Seeded Successfully ===
Tenant ID: 64f1a2b3c4d5e6f7a8b9c0d1
Department IDs: [64f1a2b3c4d5e6f7a8b9c0d2, ...]
Super Admin User:
  Email: loadtest_super_admin@visichek.com
  Password: LoadTest@123
```

## Alternative: run locust from the host, backend in Docker

Useful when you want the Locust web UI without publishing extra ports from the compose stack.

1. Install `locust` on the host: `pip install --user locust`
2. Still seed data from inside the container using the steps above.
3. Point locust at the published port:
   ```bash
   LOAD_TEST_EMAIL=loadtest_super_admin@visichek.com \
   LOAD_TEST_PASSWORD=LoadTest@123 \
   locust -f tests/load/locustfile.py --host=http://localhost:7861
   ```
4. Open `http://localhost:8089` in your browser.

If you prefer the UI from inside the container, publish port `8089` by adding `- "8089:8089"` under the `web` service's `ports:` list in [docker-compose.yml](../../docker-compose.yml), then `docker compose up -d web`.

## Running Load Tests

### Interactive Web UI (Recommended for Development)

Start the Locust web interface:

```bash
cd tests/load
locust -f locustfile.py --host=http://localhost:7861
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
locust -f locustfile.py --host=http://localhost:7861 --headless -u 50 -r 5 -t 60s
```

Parameters:
- `-u 50` - Spawn 50 concurrent users
- `-r 5` - Spawn rate of 5 users per second
- `-t 60s` - Run test for 60 seconds

### Common CLI Scenarios

**Light load test (5 users, 1 minute):**
```bash
locust -f locustfile.py --host=http://localhost:7861 --headless -u 5 -r 1 -t 60s
```

**Medium load test (25 users, 5 minutes):**
```bash
locust -f locustfile.py --host=http://localhost:7861 --headless -u 25 -r 5 -t 300s
```

**Heavy load test (100 users, 10 minutes):**
```bash
locust -f locustfile.py --host=http://localhost:7861 --headless -u 100 -r 10 -t 600s
```

**Stress test (ramp up to 500 users):**
```bash
locust -f locustfile.py --host=http://localhost:7861 --headless -u 500 -r 25 -t 600s
```

## Environment Variables

Configure load tests via environment variables:

```bash
export LOAD_TEST_HOST=http://localhost:7861
export LOAD_TEST_EMAIL=loadtest_super_admin@visichek.com
export LOAD_TEST_PASSWORD=LoadTest@123
```

Or set them inline:

```bash
LOAD_TEST_EMAIL=loadtest_super_admin@visichek.com LOAD_TEST_PASSWORD=LoadTest@123 \
locust -f locustfile.py --host=http://localhost:7861 --headless -u 50 -r 5 -t 300s
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
docker compose ps             # web should be "Up (healthy)"
curl http://localhost:7861/docs
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

## Headless CLI Runbook (copy-paste, one command at a time)

Run these in order from your server shell. Everything after step 2 happens **inside the `web` container**. Steps 3 and 4 are one-time setup per environment; steps 5 and 6 are what you run each time you want to load-test.

```bash
# 1. (host) Confirm the stack is up and the web container is healthy.
docker compose ps
```

```bash
# 2. (host) Open a shell inside the web container. Your prompt should change to something like `root@<hash>:/app#`.
docker compose exec web bash
```

```bash
# 3. (container, one-time) Seed the super_admin the load script expects.
SUPER_ADMIN_EMAIL=loadtest_super_admin@visichek.com \
SUPER_ADMIN_PASSWORD=LoadTest@123 \
python seed.py
```

```bash
# 4. (container, one-time per image) Install locust if it isn't in your image yet.
pip install locust
```

```bash
# 5. (container) Seed the load-test dataset (tenant, departments, visitor profiles, appointments).
LOAD_TEST_HOST=http://localhost:7861 \
python tests/load/setup_load_test_data.py
```

```bash
# 6. (container) Run Locust headless: 25 users, ramp 5/sec, for 2 minutes.
LOAD_TEST_HOST=http://localhost:7861 \
LOAD_TEST_EMAIL=loadtest_super_admin@visichek.com \
LOAD_TEST_PASSWORD=LoadTest@123 \
locust -f tests/load/locustfile.py \
  --host=http://localhost:7861 \
  --headless -u 25 -r 5 -t 120s
```

Heavier runs — swap step 6 for one of these:

```bash
# Medium (50 users, 5 min)
LOAD_TEST_HOST=http://localhost:7861 \
LOAD_TEST_EMAIL=loadtest_super_admin@visichek.com \
LOAD_TEST_PASSWORD=LoadTest@123 \
locust -f tests/load/locustfile.py --host=http://localhost:7861 --headless -u 50 -r 5 -t 300s
```

```bash
# Heavy (100 users, 10 min)
LOAD_TEST_HOST=http://localhost:7861 \
LOAD_TEST_EMAIL=loadtest_super_admin@visichek.com \
LOAD_TEST_PASSWORD=LoadTest@123 \
locust -f tests/load/locustfile.py --host=http://localhost:7861 --headless -u 100 -r 10 -t 600s
```

```bash
# Stress (500 users, 10 min) — expect errors; compare against medium baseline.
LOAD_TEST_HOST=http://localhost:7861 \
LOAD_TEST_EMAIL=loadtest_super_admin@visichek.com \
LOAD_TEST_PASSWORD=LoadTest@123 \
locust -f tests/load/locustfile.py --host=http://localhost:7861 --headless -u 500 -r 25 -t 600s
```

```bash
# 7. (container) Exit the container when done.
exit
```
