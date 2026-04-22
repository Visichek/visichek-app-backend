# Registration QR Workflow — Frontend Integration Guide

This document describes the **end-to-end registration QR flow**: how a receptionist or super_admin mints a tenant-bound QR code, what happens when a visitor scans it, and how the visit ends with a printed badge and a clean checkout.

Audience: the web kiosk frontend, the receptionist dashboard, and any visitor-facing PWA that lives at `/public/register/...`.

Scope: **HTTP contract only**. Same envelope/conventions as [frontend-checkin-workflow.md](frontend-checkin-workflow.md) — read section 0 there if you haven't.

---

## Flow at a glance

```
[A] Receptionist mints QR    →  POST /v1/visitors/registration-qr
[B] Render QR / poster       →  encode `signed_token` (= `qr_data`) into a deep link
[C] Visitor scans, lands on  →  /public/register/{tenant_id}?token=<signed_token>
[D] Frontend pre-flight      →  GET  /v1/public/register/verify?token=…
                                GET  /v1/public/register/{tenant_id}/info
                                GET  /v1/public/register/{tenant_id}/privacy-notice
                                GET  /v1/public/register/{tenant_id}/departments   (optional)
[E] Returning-visitor path   →  POST /v1/public/register/{tenant_id}/lookup       (optional)
[F] OCR ID scan path         →  POST /v1/public/register/{tenant_id}/id-scan      (optional)
[G] Submit registration      →  POST /v1/public/register/{tenant_id}    → 202, REGISTERED
[H] Visitor approaches desk  →  receptionist types their code on the kiosk
[I] Finalize + badge issue   →  POST /v1/public/register/{tenant_id}/finalize → CHECKED_IN + badge_qr_token
[J] Exit                     →  POST /v1/public/checkout    → CHECKED_OUT
```

Steps D, G, I are mandatory. Everything else is conditional.

---

## Step A — Mint the registration QR (receptionist / super_admin)

**Endpoint**

```
POST /v1/visitors/registration-qr
Authorization: Bearer <access_token>
Content-Type: application/json
```

Allowed roles: `receptionist`, `dept_admin`, `super_admin`. The `tenant_id` is taken from the auth token, not the body.

**Body** (both fields optional)

```json
{
  "department_id": "69e3745dc94eac772905b4e0",
  "branch_id": null
}
```

If `department_id` is supplied, the visitor's department choice will be **locked** to it on the form (the token encodes the scope and the server overrides any client-supplied value).

**Success — 201**

```json
{
  "success": true,
  "message": "Registration QR token generated",
  "data": {
    "registrationUrl": "/public/register/69e35ec9c27723b4b442bcb1",
    "signedToken": "NjllMzVlYzljMjc3MjNiNGI0NDJiY2IxfDY5ZTM3NDVkYzk0ZWFjNzcyOTA1YjRlMHx8MTc3OTQwOTg2NXw5OWNhZmE2NjdjMWRmYzU0YTA0NmRhMjcwZjY5NzdlYzhiN2UyOTVhOTcwM2EwYjFiNzQ4NWI1NjgyZmNhNTgx",
    "qrData": "NjllMzVlYzljMjc3MjNiNGI0NDJiY2IxfDY5ZTM3NDVkYzk0ZWFjNzcyOTA1YjRlMHx8MTc3OTQwOTg2NXw5OWNhZmE2NjdjMWRmYzU0YTA0NmRhMjcwZjY5NzdlYzhiN2UyOTVhOTcwM2EwYjFiNzQ4NWI1NjgyZmNhNTgx",
    "tenantId": "69e35ec9c27723b4b442bcb1",
    "departmentId": "69e3745dc94eac772905b4e0",
    "branchId": null
  }
}
```

### Field meanings

| Field | What it is | Use it for |
|-------|------------|------------|
| `registrationUrl` | A **relative path hint** — `/public/register/{tenant_id}`. **Not** a backend URL. | A human-friendly fallback link. Prepend your own frontend origin before showing it. |
| `signedToken` | Base64url-encoded HMAC string carrying `tenant_id \| department_id \| branch_id \| expiry`. **30-day TTL.** | The thing the public form must send back on `/verify` and on `/register`. |
| `qrData` | Identical to `signedToken`. Provided as a separate alias so the renderer doesn't have to think about which field name to use. | Encode this directly into the QR image. |
| `tenantId` / `departmentId` / `branchId` | Echo of the bound scope. | Display on the receptionist's "what did I just print?" preview. |

### How the QR should actually be encoded

The backend does not prescribe the QR payload shape because it doesn't render it. The recommended encoding is a deep link your frontend can route on:

```
https://app.visichek.com/public/register/{tenantId}?token={qrData}
```

That way:
- Scanning with any phone camera opens the registration form directly.
- The frontend reads `token` from the query string and runs Step D.
- Pasting the link into a browser also works (no special QR app required).

If you want the QR to be the bare token (e.g. for an in-app reader), encode just `qrData` — but then the *something* that scans it has to know to navigate to `/public/register/{tenant_id}`. The deep-link form is almost always what you want.

---

## Step B — Print / display

The QR is durable for 30 days. Common surfaces:

- **Poster at reception** — print once a month, replace before expiry.
- **Lobby tablet** — render server-side or fetch on tablet boot; refresh on a 12h timer to stay well clear of expiry.
- **Email signature for hosts** — appointment-bound flow (see Step G).

There's no revocation endpoint. If a poster is compromised, mint a new one — the old token continues to work until its `expiry`. Sensitive deployments should mint short-lived tokens (custom `expiry_hours` is not currently exposed at the route layer; ask backend if you need it).

---

## Step C — Visitor scans, lands on `/public/register/{tenant_id}`

The frontend route handler reads:
- `tenant_id` from the path
- `token` from the query string (or wherever you stashed it after parsing the QR)

Hold both in memory for the rest of the session — every public call below either takes `tenant_id` in the path or `registration_token` in the body.

---

## Step D — Pre-flight (mandatory)

Run these **before** showing any input field. They each gate a different concern.

### D.1 Verify the token

```
GET /v1/public/register/verify?token=<signedToken>
```

**Success — 200**

```json
{
  "success": true,
  "message": "Registration token verified",
  "data": {
    "valid": true,
    "tenant_id": "69e35ec9c27723b4b442bcb1",
    "department_id": "69e3745dc94eac772905b4e0",
    "branch_id": null,
    "company_name": "Acme Corp"
  }
}
```

If `valid === false`, the token is expired or tampered. Stop the flow, show "This QR has expired — please ask reception for a new one." Do not proceed to collect any PII.

If `department_id` / `branch_id` are non-null, the form's department/branch fields must be **rendered as read-only** (or hidden entirely) and pre-filled from this response. The server enforces the lock anyway, but you want the UX to match.

### D.2 Tenant info (for header/branding)

```
GET /v1/public/register/{tenant_id}/info
```

Returns `{ tenant_id, company_name }`. Use it for the page title.

### D.3 Privacy notice (legally required before data capture)

```
GET /v1/public/register/{tenant_id}/privacy-notice
```

Returns `{ notice_id, title, content, version }`. Render the notice **before** the visitor types anything. If `notice_id` is `null`, the tenant has not configured one — display a generic placeholder and continue.

Capture the visitor's acceptance and remember `notice_id` and `version` — you'll send `privacy_notice_version_id` on the register call so the audit trail shows what they agreed to.

### D.4 Departments (only if not locked by token)

```
GET /v1/public/register/{tenant_id}/departments
```

Skip this entirely if the token returned a `department_id`. Otherwise, render the returned list as a dropdown.

```json
{ "data": [{ "id": "dept123", "name": "Engineering" }, { "id": "dept456", "name": "Sales" }] }
```

---

## Step E — Returning-visitor lookup (optional, recommended)

If your form offers a "Been here before?" affordance, call this **before** asking for the visitor's full name:

```
POST /v1/public/register/{tenant_id}/lookup
Content-Type: application/json

{ "phone": "+2348012345678" }
```

(Or `email` instead of `phone`. At least one is required.)

**Match — 200**

```json
{
  "data": {
    "found": true,
    "profile_id": "507f1f77bcf86cd799439012",
    "full_name_masked": "J*** D***",
    "company": "Acme Corp",
    "last_visit_ago_days": 12,
    "id_verified_recently": true
  }
}
```

Show the masked name as a confirmation prompt: *"Welcome back, J*** D*** — is that you?"* If yes, hold `profile_id` in memory and use the **returning-visitor shortcut** on Step G (you can omit `full_name`).

Notes:
- The endpoint never returns raw PII. The visitor must re-prove possession of the phone number on the actual register call.
- If `id_verified_recently === true`, you can skip the OCR ID scan in Step F. The window is governed by tenant setting `id_reverification_days` (default 30 days).

**No match — 200**

```json
{ "data": { "found": false } }
```

Continue with the normal first-time flow.

---

## Step F — OCR ID scan (optional)

Only if the tenant requires ID and you don't have the `id_verified_recently` shortcut from Step E.

```
POST /v1/public/register/{tenant_id}/id-scan
Content-Type: multipart/form-data

file: <ID image, max 8 MiB, image/jpeg or image/png>
```

**Success — 200**

```json
{
  "data": {
    "full_name": "John Doe",
    "id_number": "A12345678",
    "id_type": "national_id",
    "confidence": 0.93
  }
}
```

The image is processed in-memory and **not persisted**. Use the extracted fields to pre-fill the form for confirmation. The visitor can edit any of them before submitting. The actual ID record (if your tenant stores ID images) is created later through a separate authenticated flow at the desk — it is not part of this public path.

Errors worth handling:
- `400` — extraction failed (blurry photo, unsupported document) — let them retry or skip.
- `413` — image > 8 MiB — re-encode client-side before retry.
- `503` — OCR provider not configured for this deployment — silently skip the OCR step and fall through to manual entry.

---

## Step G — Submit registration

This is the only step that creates database state. Returns **202 Accepted** because the write is queued.

```
POST /v1/public/register/{tenant_id}
Content-Type: application/json
```

**Body — first-time visitor**

```json
{
  "full_name": "John Doe",
  "phone": "+2348012345678",
  "company": "Acme Corp",
  "email": "john@acme.com",
  "purpose": "Quarterly review",
  "department_id": "69e3745dc94eac772905b4e0",
  "appointment_id": null,
  "consent_granted": true,
  "consent_method": "digital_acceptance",
  "privacy_notice_version_id": "n123",
  "registration_token": "NjllMzVlYzljMjc3MjNiNGI0NDJiY2IxfDY5..."
}
```

**Body — returning visitor (after lookup match)**

```json
{
  "phone": "+2348012345678",
  "profile_id": "507f1f77bcf86cd799439012",
  "purpose": "Follow-up meeting",
  "consent_granted": true,
  "privacy_notice_version_id": "n123",
  "registration_token": "NjllMzVlYzljMjc3MjNiNGI0NDJiY2IxfDY5..."
}
```

When `profile_id` is supplied, `full_name` may be omitted — it is read from the profile. The phone **must match** the profile on file (proof of possession).

### Field rules

| Field | Required | Notes |
|-------|----------|-------|
| `phone` | always | E.164 preferred. Used to find/create the visitor profile. |
| `full_name` | when no `profile_id` | Free-form. |
| `registration_token` | strongly recommended | If present, server validates and uses the token's scope. The token's `department_id` overrides any client-supplied value. |
| `department_id` | required for badge | Either from the token's scope or chosen from Step D.4. |
| `appointment_id` | only if appointment-bound | If valid + scheduled, the appointment is auto-marked `fulfilled` and the host_id is pulled from it. |
| `consent_granted` | required when tenant lawful basis is `consent` | `400` otherwise. Read the lawful basis from tenant info if you want to know upfront. |
| `consent_method` | optional | Defaults to `digital_acceptance` server-side. |
| `privacy_notice_version_id` | optional but expected | Comes from Step D.3. Stored on the session for audit. |

### Success — 201 (note: the **status code is 201**, but the response is queued — see the queued-write architecture in CLAUDE.md)

```json
{
  "success": true,
  "message": "Visitor registered successfully",
  "data": {
    "session_id": "507f1f77bcf86cd799439011",
    "visitor_profile_id": "507f1f77bcf86cd799439012",
    "status": "registered",
    "message": "Registration successful. Please proceed to reception."
  }
}
```

**Hold `session_id` on the visitor's screen.** It must be readable by the receptionist at the desk in Step I — render it large, or as a small QR/code, or as a 6-digit derivative (your choice).

The status is **REGISTERED**. The visitor is **not** checked in yet — no badge, no audit row claiming presence. They have a draft session waiting on a human to accept them.

### Quota error — 429

```json
{ "success": false, "code": "QUOTA_EXCEEDED", "message": "Monthly visitor limit reached" }
```

Show: "We can't accept new visitors right now — please ask reception." Do not retry.

---

## Step H — Visitor walks to the desk

Out of API scope. The desk staff sees the visitor's `session_id` (from the screen) and types their own **receptionist_code** into the kiosk. The receptionist code is the receptionist's `system_user.id` — every receptionist has one displayed in their dashboard profile.

Receptionists with role `receptionist` or `super_admin` can accept visitors. The next call enforces this.

---

## Step I — Finalize and issue the badge

```
POST /v1/public/register/{tenant_id}/finalize
Content-Type: application/json

{
  "session_id": "507f1f77bcf86cd799439011",
  "receptionist_code": "65f1c0d2e4b1a9f3c7e5b8a4"
}
```

The server:
1. Verifies the receptionist exists in this tenant and has an allowed role.
2. Loads the session, checks status is `REGISTERED` (or `PENDING_VERIFICATION`).
3. Validates required fields (visitor_name, department_id, host_id-or-purpose).
4. Generates a badge PDF + signed `badge_qr_token` (24h TTL).
5. Transitions the session to `CHECKED_IN`.
6. Records the audit event.

**Success**

```json
{
  "success": true,
  "message": "Check-in finalized",
  "data": {
    "session": { "status": "checked_in", "id": "507f1f77bcf86cd799439011", "...": "..." },
    "badge_qr_token": "VIS_..."
  }
}
```

Render the badge:
- The PDF is available at `GET /v1/visitors/{session_id}/badge.pdf` (authenticated) or via the badge object key on the session (presigned URL).
- The `badge_qr_token` is what the visitor will scan to check out at the end of the visit.

### Errors

| Status | Meaning | Likely cause |
|--------|---------|--------------|
| 400 | Invalid id format | One of the ids isn't a valid ObjectId |
| 400 | Cannot confirm check-in with status: X | Session was already checked in / cancelled / draft never submitted |
| 400 | Missing required field | Department or host/purpose missing — bounce back to a "complete details" screen |
| 404 | Receptionist not found | Wrong receptionist code, or that user belongs to a different tenant |

---

## Step J — Checkout

When the visitor leaves, they scan their badge at the exit kiosk:

```
POST /v1/public/checkout
Content-Type: application/json

{ "badge_qr_token": "VIS_..." }
```

**Success**

```json
{
  "data": {
    "session_id": "507f1f77bcf86cd799439011",
    "status": "checked_out",
    "visit_duration": 3725
  }
}
```

`visit_duration` is in seconds. If the badge is expired (>24h) or already-checked-out, the response is a `400` with a clear message — show "Please see reception" rather than retrying.

---

## Token & scope cheat-sheet

| Concern | Source | Lifetime | Where it lives in the contract |
|---------|--------|----------|-------------------------------|
| Registration QR token | `POST /v1/visitors/registration-qr` → `signedToken` | 30 days | Body field `registration_token` on `/public/register/{tenant_id}` |
| Badge QR token | `POST /v1/public/register/{tenant_id}/finalize` → `badge_qr_token` | 24 hours | Body field `badge_qr_token` on `/public/checkout` |
| Visitor profile id | Server-assigned on first registration | Permanent | `profile_id` in lookup response → re-supplied as `profile_id` on register |
| Session id | `POST /v1/public/register/{tenant_id}` → `session_id` | Per visit | Read at the desk; submitted by receptionist on finalize |

---

## Common mistakes

- **Treating `registrationUrl` as a backend URL.** It's a relative *frontend* path. The backend does not serve `/public/register/...` — your frontend does.
- **Encoding only `qrData` and forgetting the path.** A bare token doesn't tell the visitor's phone where to go. Use a deep link.
- **Skipping `/verify`.** A scratched/photographed/expired QR will look fine to the eye but fail at submit. Catch it before collecting any PII.
- **Ignoring `id_verified_recently` from lookup.** If true, you've already wasted the visitor's time by also asking for an OCR scan.
- **Writing your own auto-checkout.** Don't — there's a server-side scheduler. Just call `/public/checkout` from the badge.
- **Showing the receptionist code as a barcode.** It's a system_user id. Treat it like a low-sensitivity password — fine on the receptionist's own dashboard, not on a public-facing screen.
