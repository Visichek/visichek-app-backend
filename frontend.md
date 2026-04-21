# Support Cases — Frontend Integration Guide

This document describes what the frontend needs to build to drive the new
**Support Cases** feature. The colloquial name is "incidents" / "tickets" /
"cases" in UI copy — internally everything is called a **support case** to
avoid collision with the NDPC security incident log.

> **Response envelope:** every endpoint below returns the standard envelope
> `{ success, message, data, meta?, requestId }`. The `data` field holds the
> payloads shown below. Camel-case conversion is done by middleware — fields
> shown in snake_case here arrive on the wire as camelCase (e.g.
> `support_tier` → `supportTier`, `case_id` → `caseId`).

## 1. High-level flow

1. A tenant user opens a case from the in-app "Support" page. Any of the 6
   tenant roles can open a case.
2. The POST returns `202 Accepted + { id, jobId, status: "queued" }`. The UI
   should optimistically render the new case using the returned `id` and
   poll `GET /v1/jobs/{jobId}` until it flips to `succeeded` (or show the
   error message when it's `failed`).
3. Cases live in one of seven statuses (`open`, `acknowledged`,
   `in_progress`, `awaiting_tenant`, `resolved`, `closed`, `reopened`). The
   UI should render status-specific primary actions:
   * `resolved` → "Confirm resolution" (closes the case) + "Reopen".
   * `awaiting_tenant` → "Reply".
4. Admins work the case from the admin console under
   `/v1/admins/support-cases`.
5. Tenants are capped at **10 open cases**. Attempting to open an 11th
   returns `429 QUOTA_EXCEEDED` — surface this inline.
6. Support tier is encoded on the tenant's plan
   (`NONE | STANDARD | PRIORITY`). The UI doesn't enforce it — the backend
   decides what gets paged — but showing `supportTier` on the tenant-facing
   case page helps set expectations.

## 2. Domain enums

| Enum                    | Values                                                                                              |
| ----------------------- | --------------------------------------------------------------------------------------------------- |
| `SupportCaseStatus`     | `open`, `acknowledged`, `in_progress`, `awaiting_tenant`, `resolved`, `closed`, `reopened`          |
| `SupportCasePriority`   | `low`, `medium`, `high`, `critical`                                                                  |
| `SupportCaseCategory`   | `billing`, `technical`, `account`, `feature_request`, `data_privacy`, `other`                        |
| `SupportCaseAuthorType` | `tenant`, `admin`, `system`                                                                          |
| `SupportTier`           | `none`, `standard`, `priority` (read from tenant plan)                                               |

## 3. Tenant-facing endpoints (`/v1/support-cases`)

All tenant roles (`super_admin`, `dept_admin`, `receptionist`, `auditor`,
`security_officer`, `dpo`) can call these. Auth header is the standard
`Authorization: Bearer <access_token>`.

### 3.1 `POST /v1/support-cases` — Open a case

**Request body**
```json
{
  "subject": "Cannot issue badges on the front desk tablet",
  "description": "Visitors are stuck at 'Processing…' after ID scan. Started around 10am today. 3 receptionists affected.",
  "category": "technical",
  "priority": "high"
}
```

Constraints:
* `subject` 5–200 chars.
* `description` ≥ 20 chars, ≤ 10 000.
* `category` and `priority` are enums (see §2). Omit for defaults
  (`other` / `medium`).

**Success — 202**
```json
{
  "success": true,
  "message": "Support case creation queued",
  "data": {
    "id": "652a9f...",
    "jobId": "a2c4e6f8-...",
    "status": "queued"
  }
}
```

Use `data.id` as the case id immediately (it's pre-assigned). Poll
`GET /v1/jobs/{jobId}` for the final outcome.

**Failure — 429 when cap hit**
```json
{
  "success": false,
  "message": "Tenant has reached the maximum of 10 open support cases. ...",
  "data": {
    "code": "QUOTA_EXCEEDED",
    "details": { "openCount": 10, "cap": 10 }
  }
}
```

### 3.2 `GET /v1/support-cases` — List my tenant's cases

Query params (all optional): `start`, `stop`, `status`, `priority`, `category`.
Omit all of them + default pagination → cached.

**Success — 200**
```json
{
  "success": true,
  "message": "Support cases fetched",
  "data": [
    {
      "_id": "652a9f...",
      "tenantId": "64f...",
      "openedBy": "64f...",
      "openedByRole": "super_admin",
      "subject": "Cannot issue badges...",
      "description": "Visitors are stuck at 'Processing…'...",
      "category": "technical",
      "priority": "high",
      "status": "in_progress",
      "assignedAdminId": "64f...",
      "lastMessageAt": 1712500123,
      "messageCount": 4,
      "attachmentCount": 1,
      "slaDueAt": 1712586521,
      "resolvedAt": null,
      "closedAt": null,
      "dateCreated": 1712499000,
      "lastUpdated": 1712500123
    }
  ],
  "meta": { "total": 1, "start": 0, "stop": 100 }
}
```

### 3.3 `GET /v1/support-cases/{id}` — Case detail + thread

Returns the case plus its message thread in a single call. Internal admin
notes are stripped.

**Success — 200**
```json
{
  "data": {
    "case": { ... same shape as list row ... },
    "messages": [
      {
        "_id": "...",
        "caseId": "...",
        "authorId": "...",
        "authorRole": "super_admin",
        "authorType": "tenant",
        "body": "Still stuck — screenshot attached.",
        "internalNote": false,
        "attachments": [
          {
            "documentId": "...",
            "fileName": "screenshot.png",
            "mimeType": "image/png",
            "size": 182344,
            "objectKey": "tenants/.../supportcases/.../screenshot.png"
          }
        ],
        "dateCreated": 1712500123
      }
    ]
  }
}
```

### 3.4 `GET /v1/support-cases/{id}/messages`

Message thread only. Same shape as `data.messages` above. Supports
`start` / `stop` query params (default 0 / 200).

### 3.5 `POST /v1/support-cases/{id}/messages` — Reply

**Request body**
```json
{
  "body": "I just tried again after restarting the tablet — still stuck.",
  "attachments": [
    {
      "documentId": "optional-document-id",
      "fileName": "screenshot-2.png",
      "mimeType": "image/png",
      "size": 203112,
      "objectKey": "tenants/.../supportcases/.../screenshot-2.png"
    }
  ]
}
```

* Max 10 attachments per message.
* Tenants cannot post internal notes — `internalNote` is silently forced
  to `false` server-side.
* If the case was in `awaiting_tenant`, posting a reply auto-transitions
  it back to `in_progress`.

**Success — 202** — standard job envelope.

### 3.6 `POST /v1/support-cases/{id}/close` — Close resolved case

No body. Only legal from `resolved`. Returns 202. Use this for the
"Confirm resolution" button on the tenant's case page.

### 3.7 `POST /v1/support-cases/{id}/reopen` — Reopen a resolved case

No body. Only legal from `resolved`. Returns 202. Use for the "Reopen"
button on the resolved-case view.

### 3.8 `POST /v1/support-cases/{id}/transition` — Generic transition

```json
{ "status": "closed" }
```

Only tenant-legal transitions go through (the service rejects illegal
actors/state pairs with `VALIDATION_FAILED`).

### 3.9 Attachments

**Step 1 — request upload URL**

`POST /v1/support-cases/{id}/attachments/intent`

```json
{
  "fileName": "screenshot.png",
  "mimeType": "image/png",
  "size": 182344
}
```

**Response — 200**
```json
{
  "data": {
    "uploadUrl": "https://s3.amazonaws.com/bucket/tenants/.../key?...signature",
    "objectKey": "tenants/.../supportcases/.../key",
    "method": "PUT",
    "headers": { "Content-Type": "image/png" },
    "expiresIn": 900
  }
}
```

**Step 2 — client PUTs the file directly to `uploadUrl`** with the
headers shown. This is a raw S3 upload, NOT through the VisiChek API.

**Step 3 — register the uploaded file**

`POST /v1/support-cases/{id}/attachments`

```json
{
  "body": "Screenshot of the stuck screen",
  "attachments": [
    {
      "documentId": "optional — server can assign",
      "fileName": "screenshot.png",
      "mimeType": "image/png",
      "size": 182344,
      "objectKey": "tenants/.../supportcases/.../key"
    }
  ]
}
```

Returns 202; the worker creates a message entry with the attachments and
bumps `attachmentCount`.

## 4. Admin-facing endpoints (`/v1/admins/support-cases`)

Application admin auth required
(`check_admin_account_status_and_permissions`). These are identical in
shape to the tenant endpoints but expose additional data and filters:

### 4.1 `GET /v1/admins/support-cases` — List every case

Query params: `start`, `stop`, `status`, `priority`, `category`,
`tenantId`, `assignedAdminId`, `supportTier`. Default page (no filters)
is served from the global precompute cache.

### 4.2 `GET /v1/admins/support-cases/approaching-sla`

Lists active cases whose `slaDueAt` falls in the next 24 hours. Drive the
admin dashboard warning ribbon from this endpoint.

### 4.3 `GET /v1/admins/support-cases/{id}`

Same shape as tenant detail, **but internal admin notes are included**.

### 4.4 `GET /v1/admins/support-cases/{id}/messages`

Full thread including internal notes.

### 4.5 `POST /v1/admins/support-cases/{id}/messages`

Admins can mark a message as an internal note that tenants never see:

```json
{
  "body": "Escalating to the badge printer vendor — possible firmware issue.",
  "internalNote": true,
  "attachments": []
}
```

Returns 202.

### 4.6 `POST /v1/admins/support-cases/{id}/assign`

```json
{ "adminId": "64f1a2..." }
```

The assigned admin will receive a per-event email on PRIORITY-tier
tenants.

### 4.7 `POST /v1/admins/support-cases/{id}/transition`

Same shape as the tenant variant. Admins can drive:
`open→acknowledged`, `acknowledged→in_progress`,
`in_progress→awaiting_tenant`, `awaiting_tenant→in_progress`,
`in_progress→resolved`, `reopened→in_progress`.

## 5. Job polling

Every `POST`/`PATCH`/`DELETE` returns:

```json
{
  "data": {
    "id": "resource_id",
    "jobId": "task-uuid",
    "status": "queued"
  }
}
```

Poll `GET /v1/jobs/{jobId}` to get the live status:

```json
{
  "data": {
    "taskId": "…",
    "taskKey": "db.write:support_case.create",
    "resourceType": "support_case",
    "resourceId": "…",
    "tenantId": "…",
    "status": "succeeded",
    "result": { "id": "…", "status": "open" },
    "error": null,
    "createdAt": 1712500000,
    "startedAt": 1712500001,
    "completedAt": 1712500001
  }
}
```

`status` is one of `queued`, `processing`, `succeeded`, `failed`. On
failure, `error.message` is safe to surface to the user.

## 6. Notifications

Use the existing notification endpoints for the in-app notification badge.
Support-case notifications carry `link = /app/support-cases/{id}` for
tenants and `/app/admin/support-cases/{id}` for admins.

The email portion of support-case notifications can be toggled per-user
via the existing preferences endpoint:

`PATCH /v1/notifications/preferences`

```json
{ "emailOnSupportCase": false }
```

## 7. Support tier hints

When rendering the admin case page, show the tenant's support tier
(visible on every list row and on `GET /v1/admins/support-cases/{id}`
in the future — right now, call `GET /v1/subscriptions/?tenantId=…` and
read `plan.supportTier`). This tells the responding admin how aggressively
they should expect to be paged and how quickly they should acknowledge.

Tier-to-expectation cheat sheet for the UI:

| Tier      | Tenant expectation                                    |
| --------- | ----------------------------------------------------- |
| NONE      | Best-effort response; no admin paging.                |
| STANDARD  | Ack within the SLA window; admins paged on open/SLA. |
| PRIORITY  | Ack within a few hours; admins paged on every event. |

## 8. Error shapes

| Code                      | HTTP | When                                                            |
| ------------------------- | ---- | --------------------------------------------------------------- |
| `QUOTA_EXCEEDED`          | 429  | Tenant has 10 open cases.                                        |
| `VALIDATION_FAILED`       | 400  | Illegal transition, closed-case mutation attempt, bad enum.      |
| `AUTH_PERMISSION_DENIED`  | 403  | Actor tried a transition they can't perform.                    |
| `RESOURCE_NOT_FOUND`      | 404  | Case id doesn't exist / not in your tenant.                     |
| `AUTH_INVALID_TOKEN`      | 401  | Missing or expired auth.                                         |

## 9. Rendering the thread

Render each entry based on `authorType`:

* `tenant` — right-aligned bubble in tenant's theme colour.
* `admin` — left-aligned, with "VisiChek Support" badge.
* `system` — subtle full-width event row ("Case auto-closed after 7 days
  of inactivity").

Internal notes (`internalNote: true`) appear only on the admin console;
give them a distinct style (e.g. yellow background) so admins remember
the tenant can't see them.

Attachments: use the `objectKey` to build a signed-download URL by
calling the existing documents endpoint, or surface the file name + mime
icon. (S3 downloads are not embedded in the message payload — the server
doesn't pre-sign on read to keep payloads small. Fetch a presigned URL
when the user clicks the attachment.)

## 10. Recommended UI routes

Tenant portal:

* `/app/support-cases` — list with filters (status, priority, category,
  search).
* `/app/support-cases/new` — form (subject, description, category, priority,
  optional attachments).
* `/app/support-cases/:id` — detail + thread + reply composer. Show "You've
  used X/10 open cases" banner near the top.

Admin console:

* `/app/admin/support-cases` — list with extra filters
  (tenant, support tier, assigned admin).
* `/app/admin/support-cases/sla-watch` — SLA-at-risk board fed by
  `approaching-sla`.
* `/app/admin/support-cases/:id` — detail + thread + internal-note toggle +
  assign panel + state-machine action buttons.
