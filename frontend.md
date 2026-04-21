# Frontend Integration Guide — Queued Writes, Optimistic Updates, Job Polling, Notifications

This guide explains how the frontend should adapt to the backend's new queued-
write architecture. Almost every business mutation (POST / PUT / PATCH / DELETE)
now returns `202 Accepted` instead of the final resource, and most GET lists are
served from a precomputed Redis cache. This changes three things for the UI:

1. Writes are no longer strongly consistent with the next read.
2. You must either **optimistically update** the UI from the `202` response,
   **poll** the job endpoint, or **listen** for a notification.
3. A few endpoints stay synchronous (auth, payments, streaming downloads).
   Don't apply this pattern to them.

> **Response envelope** is unchanged. Every response is
> `{ success, message, data, meta?, requestId }`. Field names are camelCase on
> the wire; the backend stores snake_case and the middleware converts both ways.

---

## 1. The three request categories

| Category            | Examples                                                                          | Response       | Frontend strategy                           |
| ------------------- | --------------------------------------------------------------------------------- | -------------- | ------------------------------------------- |
| **Queued writes**   | POST/PUT/PATCH/DELETE on most resources (visitors, departments, appointments, …)  | `202 Accepted` | Optimistic UI + job polling + notifications |
| **Cached reads**    | GET list endpoints (first page / no filters) for tenant-scoped resources          | `200 OK`       | Normal; payload may be up to ~60s stale     |
| **Synchronous**     | Auth, payment webhooks, signed-token emitters, streaming downloads, interactive lookups | `200 OK` / file | Behave exactly as before                    |

**Which endpoints are synchronous** (do NOT expect `202`):

- Auth: login, refresh, verify-otp, signup, logout, 2FA setup/verify/disable,
  password change, tenant bootstrap, account deletion.
- Payments: all `/v1/payments/webhooks/*`, Stripe/Flutterwave callbacks,
  `POST /v1/payments/{id}/refund`.
- Streaming / downloads: `GET /v1/dashboard/export`,
  `GET /v1/invoices/{id}/pdf`, `GET /v1/compliance/export`, badge PDFs.
- Signed tokens: `GET /v1/super-admin/registration-qr`, visitor registration QR.
- Interactive lookups: `GET /v1/visitor-profiles/search`,
  `POST /v1/discounts/validate`, the public visitor flow under
  `/v1/public/*` and `/v1/checkin-configs/{id}/checkins`.

Everything else that mutates data is queued. Check the `202` status code at the
HTTP layer rather than guessing by endpoint.

---

## 2. The anatomy of a queued write

### Request

```http
POST /v1/departments HTTP/1.1
Authorization: Bearer <access_token>
Content-Type: application/json

{ "name": "Security", "description": "Front desk" }
```

### Response — `202 Accepted`

```json
{
  "success": true,
  "message": "Department creation queued",
  "data": {
    "id": "66342cf9a1b2c3d4e5f6a7b8",
    "jobId": "a2c4e6f8-1234-4abc-8def-0123456789ab",
    "status": "queued"
  },
  "requestId": "f2fea974-8466-4fac-bf5b-1dc7681797b0"
}
```

- `data.id` — the resource's MongoDB `_id`. **Pre-assigned by the backend**
  before the worker ever runs, so you can route to `/departments/:id`
  immediately.
- `data.jobId` — the celery task id. Use this to poll
  `GET /v1/jobs/{jobId}` for the worker's verdict.
- `data.status` — always `"queued"` on this response.

> **Caveat for writers that assign their own id.** A handful of writers
> (notably `subscription.create`, where the subscription is derived from the
> tenant's existing state) assign the real id inside the worker. For those,
> the `id` in the 202 is speculative. Always read the final id from the
> polling result (`data.result.id`) for `subscription.*`. Everything else —
> departments, visitors, appointments, incidents, branding, plans, etc. —
> uses the pre-assigned id and it's safe.

### The job status endpoint — `GET /v1/jobs/{jobId}`

```json
{
  "success": true,
  "message": "Job status fetched successfully",
  "data": {
    "taskId": "a2c4e6f8-1234-4abc-8def-0123456789ab",
    "taskKey": "db.write:department.create",
    "resourceType": "department",
    "resourceId": "66342cf9a1b2c3d4e5f6a7b8",
    "status": "queued" | "processing" | "succeeded" | "failed",
    "result": { "id": "66342cf9a1b2c3d4e5f6a7b8", "name": "Security" } | null,
    "error": "AppException: Department name already exists" | null,
    "tenantId": "...",
    "actorId": "...",
    "actorRole": "...",
    "requestId": "...",
    "dateCreated": 1713700000,
    "lastUpdated": 1713700002
  }
}
```

The four `status` values:

| Status       | Meaning                                                         | UI state                         |
| ------------ | --------------------------------------------------------------- | -------------------------------- |
| `queued`     | Task sitting on the celery broker, no worker has picked it up   | "Saving…"                         |
| `processing` | A worker is actively running it                                 | "Saving…"                         |
| `succeeded`  | Writer ran, DB is updated, `result` populated                   | Confirm / replace optimistic row |
| `failed`     | Writer raised; `error` contains the exception summary           | Roll back + show error           |

Auth: any token; the server filters by tenant/actor so a user only sees their
own jobs (application admins see everything).

---

## 3. The recommended UI pattern — optimistic update + polling

The happy path is fast (~50–300 ms end-to-end under normal load). Plan for it,
but always have a failure path.

### 3.1 Create

1. User submits the form.
2. Call the endpoint, get back `{ id, jobId, status: "queued" }` in a 202.
3. **Insert a local row keyed by `id`** into your list cache with a
   `_pending: true` flag (or whatever convention your state store uses).
4. Navigate to `/<resource>/:id` if that's the flow. The detail page should
   accept a freshly-created record whose only data is what the user just typed.
5. Start polling `GET /v1/jobs/{jobId}`.
6. On `succeeded`: mark the row as confirmed, merge `result` into it. If your
   list is precomputed (see §5), invalidate your local copy and refetch —
   the next GET will include the new row.
7. On `failed`: drop the local row, surface `error` as a toast.

### 3.2 Update / Patch

1. Capture the existing row (for rollback).
2. Apply the user's edits optimistically.
3. Fire the PATCH, get `{ id, jobId, status: "queued" }`.
4. Poll the job.
5. On `succeeded`: merge the authoritative `result` back in — don't just trust
   your optimistic state, the backend may have normalised fields, stamped
   `lastUpdated`, or resolved derived state (e.g. a plan tier change will
   affect feature flags).
6. On `failed`: restore the captured row and show the error.

### 3.3 Delete

1. Hide the row locally (strike-through or disappear, your call).
2. Fire the DELETE, get `{ id, jobId, status: "queued" }`.
3. Poll.
4. On `succeeded`: remove permanently.
5. On `failed`: restore the row and show the error (e.g. tried to delete a
   department that still has users attached).

### 3.4 Polling strategy

Keep it simple. Exponential-ish with a cap:

```ts
// poll(jobId) resolves when the job reaches a terminal state,
// or rejects after timeoutMs.
export async function pollJob(
  jobId: string,
  { timeoutMs = 30_000 }: { timeoutMs?: number } = {},
): Promise<JobLog> {
  const start = Date.now();
  let delay = 250;
  while (true) {
    const res = await fetch(`/v1/jobs/${jobId}`, { credentials: "include" });
    const { data } = await res.json();
    if (data.status === "succeeded" || data.status === "failed") return data;
    if (Date.now() - start > timeoutMs) {
      throw new Error(`Job ${jobId} timed out in '${data.status}'`);
    }
    await sleep(delay);
    delay = Math.min(delay * 1.5, 2_000); // cap at 2s between polls
  }
}
```

- Start at **250 ms**, cap at **2 s** between polls. Jobs almost always finish
  in the first second; a tight initial poll means the UI "confirms" quickly.
- Cap total wait at **30 s**. If the job hasn't terminated by then it's stuck
  on the queue — fall back to "Saving…" and let the notification deliver.
- Always `credentials: "include"` (or whatever your auth convention is).
- One poll loop per jobId. Don't run multiple loops for the same job.

### 3.5 Pseudocode (framework-agnostic)

```ts
async function createDepartment(input: DepartmentCreate) {
  // 1. Optimistic insert.
  const tempRow = { ...input, _pending: true };
  store.departments.insert(tempRow);

  // 2. Enqueue.
  let enqueued;
  try {
    enqueued = await api.post("/v1/departments", input); // 202
  } catch (err) {
    store.departments.remove(tempRow);
    toast.error(err.message);
    throw err;
  }

  // Replace temp row with the real id right away.
  const realId = enqueued.data.id;
  store.departments.replace(tempRow, { ...tempRow, id: realId });

  // 3. Poll.
  try {
    const job = await pollJob(enqueued.data.jobId);
    if (job.status === "succeeded") {
      store.departments.merge(realId, { ...job.result, _pending: false });
    } else {
      store.departments.remove(realId);
      toast.error(parseJobError(job));
    }
  } catch {
    // Timeout. Leave the row as _pending; the notification will finish the job.
    store.departments.mark(realId, { _pending: true, _stuck: true });
  }
}
```

### 3.6 Error payloads

`data.error` is a short string like `"AppException: Cannot delete an active discount. Disable it first."`. It's not translatable and may include a type prefix (e.g. `"ValueError:"`, `"AppException:"`). Strip the prefix before showing to users:

```ts
function parseJobError(job: JobLog): string {
  const raw = job.error ?? "Something went wrong";
  return raw.replace(/^[A-Z][A-Za-z]+Exception?:\s*/, "");
}
```

For richer errors (the backend's `AppException.details`), you currently can't
get them off the job endpoint — only the message. If you need the full error
envelope, fall back to a subsequent GET on the resource and inspect state.

---

## 4. Notifications as a fallback for failed jobs

Every failed queued write fires a notification to the user who enqueued it
(`actor_id` on the job log). The notification has:

- `type: "error"`
- `title: "Department could not be created"` (auto-formatted from the writer key)
- `body`: the same string you'd get from `data.error` on the job endpoint.
- `link: "/app/jobs/{task_id}"` — points at the job detail view.

### What the frontend needs to do

1. **Keep your existing notifications list / bell** hooked up to
   `GET /v1/notifications`.
2. **Subscribe to the unread count**: `GET /v1/notifications/unread-count`
   (this is precomputed per user; refresh every 15–30 s or on focus).
3. **Render `type: "error"` notifications differently** — red icon, not the
   usual blue/green. Clicking them should deep-link to whatever page owns the
   resource. If you prefer, deep-link to a "Recent activity" / "Jobs" page
   that lists the last N queue_job_log rows for the user.
4. **If the user abandons the page before polling terminates**, they'll still
   get the failure notification. This is the safety net — without it, a stuck
   job becomes a silent data loss.

### Trigger coverage

The backend already notifies on:

| Event                           | Writer keys                                    |
| ------------------------------- | ---------------------------------------------- |
| Any queued write failure        | All `db.write:*` via `notify_job_failure`      |
| Visitor check-in                | `notify_visitor_check_in`                      |
| Appointment reminder            | `notify_appointment_reminder`                  |
| Incident deadline               | `notify_incident_deadline`                     |
| DSR submitted                   | `notify_dsr_submitted`                         |
| Subscription alert              | `notify_subscription_alert`                    |
| New user added                  | `notify_new_user_added`                        |

No frontend action is needed to wire any of these up — they all surface
through `GET /v1/notifications`.

### Notifications API quick reference

```
GET    /v1/notifications?skip=0&limit=20&read=false
GET    /v1/notifications/unread-count        → { count: number }
PATCH  /v1/notifications/{id}/read
POST   /v1/notifications/read-all
DELETE /v1/notifications/{id}
GET    /v1/notifications/preferences
PUT    /v1/notifications/preferences
```

---

## 5. Reads are precomputed — what changes

Most tenant-scoped list endpoints (dashboards, `.list` endpoints for the hot
resources) are now served from Redis. The celery `worker-precompute` process
refreshes each registered resource every ~60 seconds per active tenant / active
user. **Implications:**

- A GET that follows immediately after a write may not see the new row. Don't
  refetch and expect it to appear — rely on your optimistic update or on the
  job polling result.
- GET lists can be up to ~60 s stale at worst. For most UIs that's fine; for
  anything realtime (active visitors on the front desk), optimistic updates
  should be the source of truth until the user navigates away.
- Filtered / paginated / searched lists still hit MongoDB directly — only the
  first page / no-filter case is precomputed. Filtering queries stay strongly
  consistent.

### Pattern: "snapshot + delta"

```ts
// Snapshot from the precomputed endpoint.
const { data: rows } = await api.get("/v1/departments");
store.departments.hydrate(rows);

// Apply any queued writes the user has in flight locally.
store.departments.applyPendingWrites();
```

Your store should keep a list of in-flight write jobs and merge them on top of
every GET response until the job finalises.

---

## 6. Putting it together — a TypeScript client skeleton

```ts
type JobStatus = "queued" | "processing" | "succeeded" | "failed";

type JobLog<T = any> = {
  taskId: string;
  taskKey: string;
  resourceType: string;
  resourceId: string;
  status: JobStatus;
  result: T | null;
  error: string | null;
  // …tenantId, actorId, actorRole, requestId, dateCreated, lastUpdated
};

type EnqueueResult = { id: string; jobId: string; status: "queued" };

async function enqueueAndConfirm<T>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<{ id: string; result: T | null }> {
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status !== 202) {
    // Either a synchronous endpoint or an auth/validation error.
    if (!res.ok) throw await res.json();
    return { id: "", result: (await res.json()).data as T };
  }
  const { data } = (await res.json()) as { data: EnqueueResult };
  const job = await pollJob<T>(data.jobId);
  if (job.status === "failed") throw new Error(parseJobError(job));
  return { id: data.id, result: job.result };
}

async function pollJob<T>(jobId: string): Promise<JobLog<T>> {
  const deadline = Date.now() + 30_000;
  let delay = 250;
  for (;;) {
    const res = await fetch(`/v1/jobs/${jobId}`, { credentials: "include" });
    const { data } = (await res.json()) as { data: JobLog<T> };
    if (data.status === "succeeded" || data.status === "failed") return data;
    if (Date.now() > deadline) return data; // caller decides what to do
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 1.5, 2_000);
  }
}
```

Bind the above to your state store (Redux / Zustand / Pinia / whatever) so the
pending row is visible the whole time `pollJob` is running.

---

## 7. Migration checklist (for retrofitting an existing frontend)

For every existing mutation call site:

1. Check the response status. If it's `202`, treat it as queued and follow §3.
   If it's `200` or `201`, it's on the synchronous list — leave it alone.
2. Remove "refetch the list after mutating" patterns on precomputed endpoints
   — they'll briefly show stale data and flicker. Prefer merging the job
   result into your local cache.
3. Add a `_pending` flag to each entity row in your store and render a spinner
   / opacity-50 until the flag clears.
4. Ensure your list views don't sort-or-filter in a way that makes `_pending`
   rows jump around mid-animation.
5. Wire the notifications bell to `GET /v1/notifications/unread-count` and
   show error-type notifications prominently — they are the safety net.
6. On navigation away from a page with pending jobs, keep a global "in-flight
   writes" tracker so the polling keeps running in the background (or just
   rely on the notification to deliver the bad news).
7. For the Jobs detail page (`/app/jobs/:taskId`), render the full job log so
   a user can debug their own failed write without a support ticket.

---

## 8. Things that WILL bite you

- **Do not retry a failed write by re-sending the same POST.** You'll get a
  different `id` and `jobId` each time. If you want idempotent retries,
  pass your own request-scoped idempotency key in the body and let the
  backend deduplicate (not currently implemented, but planned).
- **Do not rely on the `id` for `subscription.create`.** It's speculative.
  Read the real id from `result.id` after polling.
- **Do not poll faster than ~200 ms.** You'll DoS the jobs endpoint during a
  bulk action.
- **Do not refetch the list GET inside the 1s after a write.** The precompute
  may not have caught up. Merge the job result into your local list instead.
- **Do not assume synchronous behaviour on anything that returned 200 before.**
  Check the response status — a handful of endpoints flipped to 202 as part
  of the migration and will keep flipping. The `202` status code is the
  contract, not the endpoint path.
- **Do not treat `queued` and `processing` as different to the user.** Both
  are "Saving…". The only terminal states are `succeeded` and `failed`.
- **Errors from the job log are summaries, not full structured errors.** If
  you need the `details` dict from an `AppException`, you won't get it here
  — you'll need to re-fetch the resource or inspect a subsequent sync call.

---

## 9. Debug paths

If something looks stuck:

- **`GET /v1/jobs/{jobId}`** always returns the current log row. If it says
  `queued` for more than ~5 s under normal load, the worker isn't picking it
  up — escalate to backend on-call.
- **`GET /v1/jobs`** lists recent queued writes for the caller's tenant
  (paginated via `start` / `stop`). Good for a "My recent activity" debug
  drawer.
- **Flower** (backend-internal) shows celery-level state. If the job log says
  `queued` but Flower says `FAILED`, it's a backend bug — file it.
- **`X-Request-ID` header** on the original 202 and the `requestId` field in
  the envelope correlate the FE request to the backend structured logs. Log
  both in your frontend error reporter so backend ops can grep by request id.
