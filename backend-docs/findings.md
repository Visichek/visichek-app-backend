# Performance findings

Snapshot of what was done, what's likely still slow, and what to do about it.
Everything below is static analysis — no live profiler was run against a
production database, and I'm explicit about that so you can add measurements
later rather than trust my estimates.

## 1. What shipped in this pass

| Change                                                 | File(s)                              | Expected win |
| ------------------------------------------------------ | ------------------------------------ | ------------ |
| Startup index ensurer (idempotent, covers 40+ indexes) | `core/indexes.py` + `main.lifespan`  | Large — every query that previously did a COLLSCAN now has an IXSCAN. Specific win depends on collection size. |
| In-process LRU+TTL cache for `get_access_token`        | `core/token_cache.py`, `repositories/tokens_repo.py` | One Mongo round trip removed from every authenticated request (save ~5–30 ms). |
| Bulk tenant-plan resolver (from earlier pass)          | `services/plan_cache_service.resolve_tenant_plans_bulk` | Turns `GET /v1/tenants` from O(N) Redis+Mongo into O(1). |
| HTTP response cache middleware (from earlier pass)     | `core/http_cache.py`                 | Serves repeat GETs from Redis without touching the handler. |
| `fire_and_forget` background helper + shutdown drain   | `core/background_tasks.py`, lifespan | Foundation for moving non-critical work off the request path without losing it on graceful shutdown. |
| Audit events moved to fire-and-forget                  | `services/audit_service.record_audit_event` | Shaves 5–30 ms off every write endpoint (~20 call sites). Signature preserved so no call-site changes were needed. |
| Invoice PDF rendering moved to Celery worker           | `core/queue/tasks.py` (`invoice.generate_pdf`), `services/invoice_service.generate_invoice` | Removes 500 ms+ (`reportlab` + storage upload) from every successful checkout / renewal / trial-conversion request. |
| Plan-cache fan-out invalidation registered as a task   | `core/queue/tasks.py` (`cache.plan.invalidate_plan_fanout`) | Call sites can enqueue instead of running the Redis SCAN inline. Not yet wired at call sites — see §5.6. |
| Subscription list N+1 fix                              | `services/subscription_service.retrieve_subscriptions_with_details` | `GET /v1/subscriptions` was 2N Mongo round trips; now one `find` per related collection total. |

## 2. How to actually measure (do this before adding sub-tables)

The existing code exposes `X-Process-Time` on every response (see
`RequestTimingMiddleware` in `main.py`). That's the right signal. A lightweight
way to get a real picture:

1. Hit a representative set of endpoints (say 50 GETs + 20 POSTs) with a warm
   cache, record `X-Process-Time` per call.
2. Repeat with `Cache-Control: no-cache` so the HTTP cache is bypassed —
   this gives you the true service-layer cost.
3. Log the top-10 slowest endpoints. Everything outside the top 10 is noise.

A throwaway script (`scripts/perf_probe.py`) could do this against any
environment. I haven't written it because it depends on which endpoints you
care about most; ask if you want me to scaffold one.

Alternatively, turn on Mongo's profiler for a few minutes in staging
(`db.setProfilingLevel(1, { slowms: 20 })`) and let it surface the real hot
queries. That's faster and more accurate than either of us guessing.

## 3. Candidate sub-tables — read before adding any

Sub-tables (denormalized read models) are a big commitment: every write that
touches the source data has to also update the projection, or you get silent
drift between them. So the bar is high — only promote one of these if the
endpoint is measurably slow *after* indexes and caching are in place.

**Rule for this codebase:** projection updates go on the Celery worker, not
inline. The write endpoint persists the source, enqueues a task, returns.
The worker owns the read model. This matches what `services/renewal_service`
and `services/dunning_service` already do for billing.

### 3a. `tenant_summary_projection` — read model for `GET /v1/tenants`

- **Source tables:** `tenant_companies`, `subscriptions`, `plans`
- **Shape:** one doc per tenant with the fields `TenantWithSummaryOut`
  already returns — tenant basics + `plan_summary`.
- **Why:** even with the bulk resolver, list endpoints still do two Mongo
  round trips plus a Redis `MGET`. A projection collapses this to a single
  indexed `find`.
- **Maintenance:** worker task `projection.tenant_summary.refresh(tenant_id)`
  triggered by: `tenant.created`, `tenant.updated`, `subscription.created`,
  `subscription.updated`, `subscription.cancelled`, `plan.updated` (scan all
  subs on that plan).
- **Add when:** `GET /v1/tenants` `X-Process-Time` > 150 ms on warm cache.

### 3b. `tenant_usage_snapshot` — rollup of quota consumption

- **Source:** `usage_aggregates`, `subscriptions`, `plans`
- **Shape:** per-tenant `{collection, operation, used, limit, percent, reset_at}`.
- **Why:** the admin dashboard and tenant billing page both fan out to fetch
  plan + subscription + usage for every row; that's N+1 all over again.
- **Maintenance:** worker recomputes every 5 minutes via a scheduled task
  `projection.tenant_usage_snapshot.refresh_all`. Don't try to update this
  live — quota counters tick too fast.
- **Add when:** admin or tenant dashboards breach ~300 ms on warm cache.

### 3c. `incident_feed_projection` — denormalized incident list

- **Source:** `incidents`, `system_users` (reporter snapshot), `tenant_companies`
- **Shape:** flat list with reporter name/role and tenant name inlined.
- **Why:** the security dashboard currently does a fan-out to name lookups.
- **Maintenance:** worker task on `incident.created` / `incident.updated` /
  `system_user.updated` (only name/role changes).
- **Add when:** `GET /v1/incidents` > 200 ms on warm cache.

### 3d. `visitor_timeline_projection` — today's visitors

- **Source:** `visitors`, `appointments`, `badges`, `system_users`
- **Shape:** per-day stream of `{visitor, host_snapshot, appointment_snapshot, status}`.
- **Why:** front-desk UI re-fetches this every few seconds. A projection
  lets us cheaply serve it from a single indexed read.
- **Maintenance:** worker task on `visitor.checked_in`, `visitor.checked_out`,
  `appointment.scheduled`. TTL the docs to 48 h so the collection doesn't grow
  unboundedly.
- **Add when:** receptionist dashboard latency shows in profiling.

### 3e. `billing_report_cache` — platform-admin revenue & churn aggregates

- **Source:** `invoices`, `subscriptions`, `payment_transactions`
- **Shape:** daily rollups: total revenue, new/cancelled/past_due subs, MRR.
- **Why:** `services/billing_report_service` currently aggregates on every
  request to `/v1/admins/dashboard/billing`.
- **Maintenance:** hourly scheduled task (already matches the cadence of
  renewal jobs). Recompute the current day + write to cache; older days
  are immutable after UTC midnight.
- **Add when:** billing dashboard is slow OR it's being hit by many admin
  tabs at once.

**Not recommending projections for:**

- `audit_trail` — already append-only and only queried by its indexes. A
  read model would drift and double writes for no clear win.
- `notifications` — per-user, small, and already indexed. Not worth it.
- `checkout_sessions` — per-tenant page is small; one indexed find suffices.

## 4. POST/PUT/PATCH/DELETE endpoints that should offload to the worker

Rule of thumb: if the endpoint's *own* value is "accept and persist", any
fan-out work (emails, PDFs, webhooks, OCR, audit cascades, cache warming)
belongs on the worker. The client should get a response as soon as the
primary write commits.

Registration happens via `@task("key")` decorators in `core/queue/tasks.py`,
enqueued with `await QueueManager.get_instance().enqueue("key", {...})`.
Payloads must be JSON-serializable — pass IDs and timestamps, never model
objects.

### 4a. Confirmed heavy endpoints (offload now)

| Endpoint                                                 | Work to offload                                                   | Suggested task keys |
| -------------------------------------------------------- | ----------------------------------------------------------------- | ------------------- |
| `POST /v1/admins/tenants/bootstrap`                      | Welcome emails; first-run audit events; tenant projection refresh | `email.tenant_welcome`, `audit.record`, `projection.tenant_summary.refresh` |
| `POST /v1/subscriptions` and `POST /v1/checkout/sessions/{id}` success path | Invoice generation (reportlab PDF), storage upload, welcome email, plan-cache invalidation of ALL related tenants | `invoice.generate_pdf`, `email.subscription_confirmation`, `cache.plan.invalidate_tenant` |
| `POST /v1/invoices/{id}/pdf` (if we generate on demand)  | `reportlab` call + S3/local storage upload                        | `invoice.generate_pdf` |
| `POST /v1/documents` (upload)                            | OCR extraction (`OCRManager.extract_from_image`), checksum, scan-for-face         | `document.extract_ocr`, `document.scan_face` |
| `POST /v1/checkins` / `POST /v1/visitor-verification/*`  | ID hash/dedupe; notify host; badge PDF render; audit trail        | `visitor.notify_host`, `badge.render`, `audit.record` |
| `POST /v1/incidents` + `PATCH` resolution                | NDPC notification email, escalation rules                          | `incident.notify_ndpc`, `incident.escalate` |
| `POST /v1/data-subject-requests`                         | Acknowledgement email; retention timer                            | `dsr.acknowledge`, `dsr.schedule_due_reminder` |
| `POST /v1/admins/tenants/{id}/offboard`                  | Data export, cleanup, payment provider customer deletion          | `tenant.offboard_export`, `tenant.cleanup_storage` |
| `DELETE /v1/account`                                     | Session revocation fan-out, cascade clean-up                      | `account.cleanup_cascades` |
| `POST /v1/plans/*/archive` / `/activate` / `PUT /v1/plans/{id}` | Invalidate plan cache for every affected tenant subscription      | `cache.plan.invalidate_plan_fanout` |
| `POST /v1/payments/webhooks/{provider}`                  | Invoice generation on success; renewal retries on failure         | `invoice.generate_pdf`, `billing.dunning_schedule` |
| `POST /v1/discount`, `PUT /v1/discount/{id}`             | Redemption counter reconciliation (if we ever add one)             | `discount.reconcile_redemptions` |

### 4b. Endpoints that should stay inline

These need a synchronous result or have no meaningful fan-out:

- `POST /v1/admins/login`, `POST /v1/system-users/login`, `POST /v1/admins/verify-otp` — auth must return tokens inline.
- `POST /v1/checkout/sessions` — must return `checkout_url` inline.
- `POST /v1/branches`, `POST /v1/departments` — plain writes, no fan-out worth deferring.
- `GET` endpoints — caching, not offloading, is the answer.

### 4c. Task registry starter set (put in `core/queue/tasks.py`)

```python
@task("email.tenant_welcome")
async def email_tenant_welcome(tenant_id: str, super_admin_email: str) -> None: ...

@task("invoice.generate_pdf")
async def invoice_generate_pdf(invoice_id: str) -> None: ...

@task("projection.tenant_summary.refresh")
async def refresh_tenant_summary(tenant_id: str) -> None: ...

@task("projection.tenant_summary.refresh_for_plan")
async def refresh_tenant_summary_for_plan(plan_id: str) -> None: ...

@task("cache.plan.invalidate_tenant")
async def invalidate_tenant_plan(tenant_id: str) -> None: ...

@task("cache.plan.invalidate_plan_fanout")
async def invalidate_plan_fanout(plan_id: str) -> None: ...

@task("document.extract_ocr")
async def document_extract_ocr(document_id: str) -> None: ...

@task("visitor.notify_host")
async def visitor_notify_host(visitor_id: str, host_user_id: str) -> None: ...

@task("badge.render")
async def badge_render(badge_id: str) -> None: ...

@task("incident.notify_ndpc")
async def incident_notify_ndpc(incident_id: str) -> None: ...

@task("audit.record")
async def audit_record(
    actor_id: str, actor_role: str, action: str,
    resource_type: str, resource_id: str,
    tenant_id: str | None = None, details: dict | None = None,
) -> None: ...

@task("account.cleanup_cascades")
async def account_cleanup(user_id: str, user_type: str) -> None: ...
```

Each implementation wraps an already-existing service call — you're moving
the call, not rewriting it.

## 5. Concrete next steps, ordered by ROI

1. **Deploy and measure.** Capture `X-Process-Time` across the top-20
   endpoints for a day. The baseline this produces is what all further work
   aims at.
2. ~~Offload the webhook invoice-PDF path.~~ — **done** (task
   `invoice.generate_pdf`; caller changed in `services/invoice_service.generate_invoice`).
3. **Offload OCR.** `OCRManager.extract_from_image` over Google Document AI can
   spike into seconds. Callers to move: `services/id_extraction_service`,
   `services/public_registration_service`, `services/visitor_verification_service`.
   Note: the *request* must not block on OCR, but the caller often uses the OCR
   output (name, DOB) synchronously to build the response. The fix is to persist
   the raw image synchronously, return an ID to the caller, and have the worker
   fill in the extracted fields, which the frontend then polls for. Pick one
   endpoint and try it before rolling out widely.
4. **Wire the plan-cache fan-out task.** The `cache.plan.invalidate_plan_fanout`
   task exists but call sites (`services/plan_service.update_plan_by_id`,
   `archive_plan`, `activate_plan`) still call `invalidate_plan_cache` inline.
   Swap the call to `QueueManager.get_instance().enqueue("cache.plan.invalidate_plan_fanout", {"plan_id": plan_id})`
   once we've verified in staging that the sweep isn't being relied on
   synchronously (it isn't — the current code already has a try/except swallow).
5. **Re-measure.** If the top-10 is already in the sub-100 ms range after 2, 3, 4,
   **do not add sub-tables** — they aren't paying their way.
6. Only then — if `GET /v1/tenants` or the admin dashboards are still slow —
   promote one sub-table, start with `tenant_summary_projection`. Worker-owned,
   idempotent refresh task, backfill script once on deploy.

## 6. Gotchas to avoid

- **Don't cache auth for more than 30 s in-process without a Redis revocation
  cross-check.** If you raise the TTL, add a Redis-published "token revoked"
  set that the cache's get-path checks. `core/token_cache.py` has hooks for it.
- **Don't index `status` alone.** It has low cardinality (maybe 5 values).
  Always use it as the *second* field in a compound with `tenant_id` or similar.
- **Don't materialize a sub-table you haven't profiled.** Double-writes and
  silent drift cost more than the round trip you save.
- **Keep task payloads JSON-serializable.** No `datetime`, no Pydantic
  models, no `ObjectId` — pass strings and epoch ints and let the worker
  rehydrate them.
- **Do not offload work that the user needs to see the result of.** If the
  frontend polls for status, fine; if it expects the answer in the response,
  keep it inline.
