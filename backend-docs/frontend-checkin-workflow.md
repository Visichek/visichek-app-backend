# Visitor Check-In Workflow — Frontend Integration Guide

This document tells a frontend agent (web kiosk, receptionist dashboard, or security scanner) exactly how to implement the visitor check-in flow against the Visichek backend. It is written as a task manual, not a reference.

Scope: **HTTP contract only**. No transport, websocket, or rendering prescription.

---

## 0. Baseline Conventions

- **Base URL**: `{{ api_base }}/v1`. Every endpoint below is prefixed with `/v1`.
- **Response envelope**: every response — success or failure — has this shape:

```json
{
  "success": true,
  "message": "Human-readable message",
  "data": { ... }
}
```

Errors:

```json
{
  "success": false,
  "message": "Invalid token",
  "code": "AUTH_INVALID_TOKEN",
  "data": null
}
```

Always read `data`, never the raw top-level object. Treat anything where `success === false` as an error and surface `message` (plus `code` for machine logic).

- **Auth**: Bearer JWT in the `Authorization` header for authenticated calls. Public kiosk calls (sections 2–6) are unauthenticated.
- **Case conversion**: the server accepts both `snake_case` and `camelCase` request bodies. It always responds in `snake_case` by default. If your middleware converts to `camelCase` for rendering, keep a round-trip converter.
- **Pagination**: list endpoints accept `skip` / `limit` or `cursor` / `limit` depending on the endpoint. Where `next_cursor` is present, use it; otherwise increment `skip`.
- **Timestamps**: all timestamps are **Unix epoch seconds** (ints), not ISO strings — except `badge.expires_at`, which is also epoch seconds but must be rendered in the tenant's local timezone.

---

## 1. Flow at a Glance

```
QR scan → resolve config → (lookup returning visitor OR upload ID OR type bio) →
submit check-in → receptionist approves → badge issued → badge scanned at exit
```

Every code branch below implements one step.

---

## 2. QR Payload

The kiosk QR code encodes a JSON payload like:

```json
{ "checkin_config_id": "cfg_01HX9K2M7VQWZ3..." }
```

**Do not treat it as a URL.** Parse the JSON, extract `checkin_config_id`, then call the config endpoint.

---

## 3. Step 1 — Fetch Tenant Check-In Configuration

Call this immediately after parsing the QR payload. **Unauthenticated.**

### Request

```
GET /v1/checkin-configs/{checkin_config_id}
```

### Success — 200

```json
{
  "success": true,
  "message": "Check-in configuration retrieved",
  "data": {
    "checkin_config_id": "cfg_01HX9K2M7VQWZ3...",
    "tenant_id": "tnt_01HX...",
    "tenant_name": "Acme Corp",
    "logo_url": "https://cdn.example.com/tenants/acme/logo.png",
    "id_upload_enabled": true,
    "allow_returning_visitor_lookup": true,
    "required_fields": [
      { "key": "full_name", "label": "Full Name", "type": "text", "required": true, "category": "bio" },
      { "key": "email", "label": "Email", "type": "email", "required": true, "category": "bio" },
      { "key": "phone", "label": "Phone", "type": "tel", "required": true, "category": "bio" },
      { "key": "company", "label": "Company", "type": "text", "required": false, "category": "tenant_specific" },
      {
        "key": "host_employee_id",
        "label": "Who are you visiting?",
        "type": "select",
        "required": true,
        "category": "tenant_specific",
        "options_endpoint": "/v1/tenants/{tenant_id}/employees"
      }
    ]
  }
}
```

### Errors

| Status | `code` | Cause |
| --- | --- | --- |
| 404 | `RESOURCE_NOT_FOUND` | Unknown or revoked config id → show "This check-in link is no longer valid." |

### What the UI does with this

1. Paint the tenant logo + name at the top.
2. Split `required_fields` by `category`:
   - `bio` → reusable personal data (ask once, reuse on return).
   - `tenant_specific` → collected every visit.
3. If `id_upload_enabled` is `false`, hide the "Scan my ID" option.
4. If `allow_returning_visitor_lookup` is `false`, skip the lookup step (section 4) entirely.
5. For any field with `options_endpoint`, call that endpoint (authenticated or using a public variant the tenant provides) to populate the dropdown.

---

## 4. Step 2 — Returning Visitor Lookup (optional)

Offer the user a "I've been here before" button. **Unauthenticated.**

### Request

```
GET /v1/checkin-configs/{checkin_config_id}/visitors/lookup?email={email}&phone={phone}
```

- At least one of `email` or `phone` is required; both is fine and more specific.
- URL-encode both. Phone should be E.164 if possible (`+2348012345678`).

### Success — 200 (found)

```json
{
  "success": true,
  "message": "Visitor found",
  "data": {
    "found": true,
    "visitor": {
      "id": "vst_01HX...",
      "tenant_id": "tnt_01HX...",
      "full_name": "Jane Doe",
      "email": "jane@example.com",
      "phone": "+2348012345678",
      "verified": true,
      "bio_data": {
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "+2348012345678",
        "date_of_birth": "1990-04-12",
        "nationality": "NGA"
      }
    }
  }
}
```

### Success — 200 (not found)

```json
{ "success": true, "message": "Visitor not found", "data": { "found": false } }
```

### Errors

| Status | `code` | Cause |
| --- | --- | --- |
| 400 | `VALIDATION_FAILED` | Neither `email` nor `phone` supplied. |

### UI logic

- If `found === true`, **skip the bio form** and jump straight to the tenant-specific fields + purpose (section 6). Pre-fill everything from `visitor.bio_data`. Remember `visitor.id` — you must pass it as `visitor_id` on submit.
- If `found === false`, offer the user either the "Scan ID" path (section 5) or "Enter manually" path.

---

## 5. Step 3 — Optional: Scan ID (Google Document AI)

Two HTTP calls, in order:

### 5.1 Upload the ID image (existing document endpoint)

Use the project's existing document upload endpoint. It returns:

```json
{
  "success": true,
  "data": {
    "document_id": "doc_01HX...",
    "mime_type": "image/jpeg",
    "size_bytes": 245312
  }
}
```

> Do **not** re-upload the file when calling extraction — pass the `document_id` instead.

### 5.2 Extract ID fields

```
POST /v1/id-extractions
Content-Type: application/json

{
  "document_id": "doc_01HX...",
  "id_type": "passport",
  "provider": "google_document_ai"
}
```

`id_type` is one of `"passport"`, `"drivers_license"`, `"national_id"`. `provider` is optional; defaults to `google_document_ai`.

### Success — 200

```json
{
  "success": true,
  "message": "ID extracted",
  "data": {
    "id_extraction_id": "idx_01HX...",
    "document_id": "doc_01HX...",
    "provider": "google_document_ai",
    "id_type": "passport",
    "extracted_fields": {
      "full_name": "JANE MARY DOE",
      "date_of_birth": "1990-04-12",
      "nationality": "NGA",
      "address": "123 Main St",
      "portrait_url": "https://storage.example.com/portraits/idx_01HX....jpg"
    },
    "unmatched_required_fields": ["phone", "email"],
    "confidence": 0.96,
    "verified": true
  }
}
```

### Errors

| Status | `code` | UI action |
| --- | --- | --- |
| 400 | `VALIDATION_FAILED` | Bad `document_id` or unsupported `id_type`. Ask the user to retry the upload. |
| 422 | `VALIDATION_FAILED` | Extraction returned nothing useful (blurry scan, wrong document). Fall back to manual entry. |
| 502 | `UPSTREAM_ERROR` | Document AI is down. Retry once with backoff; if still failing, fall back to manual entry. |

### UI logic

- Pre-fill the bio form with `extracted_fields`.
- Show `unmatched_required_fields` as the only editable fields — the rest should be read-only with a small "from your ID" note.
- Remember `id_extraction_id` — pass it on submit.
- If `verified === false`, still let the user continue but treat the flow like manual entry.

---

## 6. Step 4 — Submit the Check-In

Whether the user came from lookup, ID scan, or manual entry, the submit call is the same.

### Request

```
POST /v1/checkin-configs/{checkin_config_id}/checkins
Content-Type: application/json

{
  "visitor_id": "vst_01HX...",            // null if first-time
  "id_extraction_id": "idx_01HX...",      // null if manual entry
  "bio_data": {
    "full_name": "Jane Mary Doe",
    "email": "jane@example.com",
    "phone": "+2348012345678",
    "date_of_birth": "1990-04-12",
    "nationality": "NGA"
  },
  "tenant_specific_data": {
    "company": "Globex Ltd.",
    "host_employee_id": "emp_01HX..."
  },
  "purpose": {
    "purpose": "Meeting",
    "purpose_details": "Quarterly review with finance team",
    "expected_duration_minutes": 60
  }
}
```

Rules the backend enforces — the UI must enforce them too, or show the returned error:

- Every `required: true` field in the config must be present in `bio_data` (category `bio`) or `tenant_specific_data` (category `tenant_specific`).
- A visitor can only have one `pending_approval` check-in at a time.
- If `id_extraction_id` is omitted, the check-in is stored with `verified: false`.

### Success — 201

```json
{
  "success": true,
  "message": "Check-in submitted",
  "data": {
    "id": "chk_01HX...",
    "tenant_id": "tnt_01HX...",
    "visitor_id": "vst_01HX...",
    "state": "pending_approval",
    "verified": true,
    "date_created": 1745230000
  }
}
```

### Errors

| Status | `code` | UI |
| --- | --- | --- |
| 400 | `VALIDATION_FAILED` | Missing required fields. Show which one. |
| 404 | `RESOURCE_NOT_FOUND` | Invalid config / visitor / extraction id. Restart the flow. |
| 409 | `VALIDATION_FAILED` | "You already have a pending check-in — please see the receptionist." |

### What to render next

A waiting screen: "Please wait, the receptionist is approving your check-in." Poll `GET /v1/checkins/{id}` every 3–5 seconds (see section 7) until `state` flips out of `pending_approval`, then branch:

- `approved` → navigate to the badge screen. The badge is returned on the receptionist's confirm call, **not on this poll**, so the kiosk itself usually just shows "You're in — please collect your badge at reception." A dedicated badge-display screen is driven by a push from the receptionist side.
- `rejected` → show the rejection reason.

---

## 7. Receptionist Dashboard

All routes below require a `Bearer` token for a user with role `receptionist`, `dept_admin`, or `super_admin` within the tenant.

### 7.1 List pending check-ins

```
GET /v1/tenants/{tenant_id}/checkins?state=pending_approval&limit=20&skip=0
Authorization: Bearer <token>
```

Valid `state` values: `pending_approval`, `approved`, `rejected`, `checked_out`.

### Success — 200

```json
{
  "success": true,
  "message": "Check-ins retrieved",
  "data": [
    {
      "id": "chk_01HX...",
      "visitor_id": "vst_01HX...",
      "visitor_name": "Jane Mary Doe",
      "verified": true,
      "purpose": "Meeting",
      "host_employee_id": "emp_01HX...",
      "date_created": 1745230000,
      "state": "pending_approval"
    }
  ],
  "meta": { "total": 3, "skip": 0, "limit": 20 }
}
```

Use a realtime or polling strategy (every ~5s) to keep the queue fresh. If/when the push channel is available, the backend emits `checkin.pending_approval` notifications; see section 9.

### 7.2 Check-in detail

```
GET /v1/checkins/{checkin_id}
Authorization: Bearer <token>
```

Returns the full `CheckinOut` including `bio_data`, `tenant_specific_data`, `purpose`, `verified`, `id_extraction_id`. Useful for the "confirm" modal.

### 7.3 Approve or reject

```
POST /v1/checkins/{checkin_id}/confirm
Authorization: Bearer <token>
Content-Type: application/json

{ "action": "approve", "notes": "Host confirmed expecting visitor" }
```

`action` is `"approve"` or `"reject"`. `notes` is optional; on reject it is stored as `rejection_reason`.

### Success — 200 (approve)

```json
{
  "success": true,
  "message": "Check-in approved",
  "data": {
    "checkin_id": "chk_01HX...",
    "state": "approved",
    "badge": {
      "badge_id": "bdg_01HX...",
      "qr_code_value": "BADGE-7fZ2KqX9...",
      "visitor_name": "Jane Mary Doe",
      "verified": true,
      "portrait_url": "https://storage.example.com/portraits/idx_01HX....jpg",
      "host_employee_name": "John Smith",
      "purpose": "Meeting",
      "issued_at": 1745230100,
      "expires_at": 1745279999
    }
  }
}
```

### Success — 200 (reject)

```json
{
  "success": true,
  "message": "Check-in rejected",
  "data": {
    "checkin_id": "chk_01HX...",
    "state": "rejected",
    "rejected_by_user_id": "rcp_01HX...",
    "rejected_at": 1745230100,
    "rejection_reason": "Host employee not reachable"
  }
}
```

### 7.4 Rendering the badge

On approve, the receptionist UI is responsible for:

1. Rendering `data.badge.qr_code_value` into an actual QR code image (use any client-side library — the backend never produces the image).
2. Printing `visitor_name`, `host_employee_name`, `purpose`, `portrait_url`, and the human-readable date from `expires_at`.

`expires_at` is always end-of-day in the tenant's timezone. The QR is inert after that — don't cache it past midnight.

---

## 8. Badge Validation (security / exit scanner)

**Unauthenticated.** Used whenever a QR is scanned at the exit gate or by security.

```
GET /v1/badges/validate?qr_code_value=BADGE-7fZ2KqX9...
```

### Success — 200 (valid)

```json
{
  "success": true,
  "message": "Badge valid",
  "data": {
    "valid": true,
    "badge_id": "bdg_01HX...",
    "checkin_id": "chk_01HX...",
    "visitor_name": "Jane Mary Doe",
    "expires_at": 1745279999
  }
}
```

### Success — 200 (invalid)

```json
{
  "success": true,
  "message": "Badge invalid",
  "data": { "valid": false, "reason": "expired" }
}
```

`reason` is one of `expired`, `not_found`, `revoked`. Treat all three the same way in UX (big red X, "Ask reception for assistance") but surface the string for the security log.

---

## 9. Notifications

Every user (receptionist, host, tenant admin) has a persisted notification inbox. Events relevant to this flow:

| `event` | Audience | Action on the client |
| --- | --- | --- |
| `checkin.pending_approval` | Every receptionist of the tenant, host employee | Receptionist: refresh pending list. Host: show "Your visitor has arrived". |
| `checkin.approved` | Host, approving receptionist, all other receptionists | Receptionist: drop from pending. Host: show badge-issued confirmation. |
| `checkin.rejected` | Host, other receptionists | Drop from pending; show reason to host. |

Use the existing notification endpoints to drive the bell UI:

```
GET    /v1/me/notifications?unread_only=true&limit=20
POST   /v1/me/notifications/{id}/read
POST   /v1/me/notifications/read-all
```

`payload` in each notification matches the schema in the spec (`checkin_id`, `visitor_name`, `host_employee_id`, `badge_id` where applicable). `requires_action: true` on a receptionist's `checkin.pending_approval` row → render a CTA button that opens the detail modal.

---

## 10. Analytics (Rejected / Historical)

Rejected check-ins are **not** surfaced in the operational list endpoints (section 7.1). They live in a separate analytics endpoint.

```
GET /v1/tenants/{tenant_id}/checkins/analytics?state=rejected&from={epoch}&to={epoch}&limit=50&skip=0
Authorization: Bearer <token>
```

Access: `receptionist`, `dept_admin`, `super_admin`, `auditor`.

Response items include `rejection_reason`, `rejected_by_user_id`, `rejected_at`. Omit `state` to get all terminal states (approved, rejected, checked_out) in one pull.

---

## 11. State Machine (for UI logic)

```
[pending_approval] --approve--> [approved] --checkout--> [checked_out]
         |
       reject
         |
         v
     [rejected]   (terminal; analytics-only)
```

- `approved` and `checked_out` appear in operational lists.
- `rejected` is terminal and only visible through the analytics endpoint.

---

## 12. Error Code Cheat Sheet

Relevant `code` values the UI may see from this flow:

| `code` | Meaning |
| --- | --- |
| `AUTH_INVALID_TOKEN` | Re-authenticate. |
| `AUTH_PERMISSION_DENIED` | Wrong role. Go back. |
| `RESOURCE_NOT_FOUND` | Bad id — restart. |
| `VALIDATION_FAILED` | Missing/invalid input. `message` explains which. |
| `UPSTREAM_ERROR` | Google Document AI or storage is down — retry/fallback. |
| `SUBSCRIPTION_REQUIRED` / `SUBSCRIPTION_INACTIVE` / `FEATURE_DISABLED` / `QUOTA_EXCEEDED` | Tenant plan gating. Show a "contact your admin" message — the kiosk can't self-recover. |

---

## 13. Empty State / Edge Cases

1. **Config fetch fails 404** → kiosk shows a permanent "This check-in page is no longer active" screen. Do not retry.
2. **Lookup disabled** → hide the "I've been here before" button entirely.
3. **ID upload disabled** → hide the scan option entirely; force manual bio entry.
4. **Confirm 409 on submit** → there's already a pending check-in for this visitor. Prompt the user to see the receptionist in person.
5. **Badge QR scanned twice by the exit scanner** → the scanner can call `/v1/badges/validate` as many times as needed; it is idempotent.
6. **Badge expired mid-visit** → backend will return `valid: false, reason: "expired"`. Visitor must check in again.

---

## 14. Suggested Client State Machine (pseudocode)

```text
parse_qr(payload) -> config_id
config = GET /checkin-configs/{config_id}
if user_taps("returning"):
    visitor = GET .../visitors/lookup?email=&phone=
    if visitor.found: skip to tenant_specific_form
if user_taps("scan_id") and config.id_upload_enabled:
    doc = upload_existing()
    extraction = POST /id-extractions { doc.id, id_type }
    prefill_bio(extraction.extracted_fields)
    focus(extraction.unmatched_required_fields)
else:
    render manual bio form

on user submits tenant_specific_form + purpose:
    POST /checkin-configs/{config_id}/checkins { visitor_id?, id_extraction_id?, bio_data, tenant_specific_data, purpose }
    -> show "waiting for approval" screen
    poll GET /checkins/{id} until state in {approved, rejected}
    approved  -> show "please collect badge at reception"
    rejected  -> show rejection_reason
```

---

## 15. Useful Reminders for the Agent

- **Never assume a URL is in the QR.** The QR is a JSON payload; parse it.
- **Never re-upload the ID file** when calling `/id-extractions`. Always reuse the `document_id` from the upload step.
- **Never store the raw `id_number`** client-side. The backend encrypts it at rest and only returns the portrait/name/DOB derivatives.
- **Never bypass `@document_response` envelopes.** If you see a raw shape without `success`/`data`, something is wrong; retry once then error out.
- **Never render the badge QR image on the server.** The server returns `qr_code_value`; your code encodes it into a QR.
- **Do localize `expires_at`** in the tenant timezone when printed on the badge.
