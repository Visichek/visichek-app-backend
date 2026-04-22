# Active Check-In Configuration — Setup & Frontend Integration Guide

The **check-in configuration** controls what fields the visitor-facing kiosk / public registration form asks for, whether ID upload is required, and whether returning visitors can shortcut the flow with a phone/email lookup.

This doc covers:
- Who can create / update the config (role requirements).
- The endpoints to call.
- The tenant-scoped public endpoints `GET /v1/public/tenants/{tenant_id}/active-checkin-config` (used by the kiosk to fetch the form definition) and `POST /v1/public/tenants/{tenant_id}/submit` (used by the kiosk to submit a check-in), and their **default-fallback** behavior when no config has been created yet.
- How the frontend should render the response.

Companion docs: [frontend-checkin-workflow.md](frontend-checkin-workflow.md) (full visitor check-in flow), [registration-qr-workflow.md](registration-qr-workflow.md) (registration QR flow).

---

## 1. Who can set it

| Endpoint | Allowed roles |
|----------|---------------|
| `POST /v1/checkin-configs` (create) | `super_admin` only |
| `PATCH /v1/checkin-configs/{checkin_config_id}` (update) | `super_admin` only |
| `GET /v1/checkin-configs` (list for current tenant) | `super_admin`, `dept_admin` |
| `GET /v1/checkin-configs/{checkin_config_id}` (read by config id) | Unauthenticated (kiosk-facing) |
| `GET /v1/public/tenants/{tenant_id}/active-checkin-config` (read active by tenant) | Unauthenticated (kiosk / registration form) |
| `POST /v1/public/tenants/{tenant_id}/submit` (submit a check-in by tenant) | Unauthenticated (kiosk) |
| `POST /v1/checkin-configs/{checkin_config_id}/submit` (submit a check-in by config id) | Unauthenticated (kiosk) |

**Short version:** Only a Tenant Super Admin can create or edit the check-in config. Department admins can view the list. The two public read endpoints require no auth — they are what the kiosk / public registration form call.

The `tenant_id` on create is **taken from the super_admin's auth token** — do not put it in the body, and do not expose a tenant picker in the UI. A super_admin can only manage their own tenant's config.

---

## 2. Where it lives

- **Collection:** `checkin_configs`
- **Schema:** [schemas/checkin_config_schema.py](../schemas/checkin_config_schema.py)
- **Service:** [services/checkin_config_service.py](../services/checkin_config_service.py)
- **Repo:** [repositories/checkin_config_repo.py](../repositories/checkin_config_repo.py)
- **Route:** [api/v1/checkin_config_route.py](../api/v1/checkin_config_route.py)
- **Public tenant-scoped route:** [api/v1/public_registration_route.py](../api/v1/public_registration_route.py)

Writes are queued (202 Accepted) — see the queued-write architecture notes in `CLAUDE.md` and poll `GET /v1/jobs/{job_id}` for the real resource id + completion status.

---

## 3. Default behavior (no config yet)

If a tenant super_admin has never created a check-in config, `GET /v1/public/tenants/{tenant_id}/active-checkin-config` **does not 404**. It returns a sensible default:

```json
{
  "success": true,
  "message": "Active check-in configuration retrieved",
  "data": {
    "checkin_config_id": "",
    "tenant_id": "t123",
    "tenant_name": "Acme Corp",
    "logo_url": "https://cdn.example.com/tenants/acme/logo.png",
    "id_upload_enabled": true,
    "allow_returning_visitor_lookup": true,
    "required_fields": [
      { "key": "full_name", "label": "Full Name",        "type": "text",  "required": true,  "category": "bio" },
      { "key": "email",     "label": "Email",            "type": "email", "required": true,  "category": "bio" },
      { "key": "phone",     "label": "Phone",            "type": "tel",   "required": true,  "category": "bio" },
      { "key": "company",   "label": "Company",          "type": "text",  "required": false, "category": "bio" },
      { "key": "purpose",   "label": "Purpose of Visit", "type": "text",  "required": true,  "category": "tenant_specific" }
    ]
  }
}
```

### Detecting "defaults mode" on the frontend

`checkin_config_id === ""` is the signal that the tenant has not customized their config. Use it to:
- Render a dismissible banner on the super_admin dashboard: *"You're using the default check-in form. Customize it in Settings → Check-In Form."*
- Skip any UI that references a specific config id (e.g. "Edit this config").

The only 404 from this endpoint is when the **tenant itself** does not exist:

```json
{ "success": false, "code": "RESOURCE_NOT_FOUND", "message": "Tenant not found" }
```

---

## 3a. Submitting a check-in (kiosk)

The kiosk has two equivalent submit paths, both unauthenticated and both accepting the same `multipart/form-data` body:

| Endpoint | Use when |
|----------|----------|
| `POST /v1/public/tenants/{tenant_id}/submit` | **Preferred.** The kiosk only knows the `tenant_id` (e.g. after reading it from a scanned QR deep link or the public registration URL). Server resolves the tenant's active config, or falls back to the default required-field set when none exists. |
| `POST /v1/checkin-configs/{checkin_config_id}/submit` | The kiosk already has a specific `checkin_config_id` (e.g. a per-location kiosk hardcoded against one config). |

Both call the same underlying logic — use whichever fits the kiosk's deployment model.

### Request — multipart/form-data

```
POST /v1/public/tenants/{tenant_id}/submit
Content-Type: multipart/form-data
```

| Form field | Required | Type | Notes |
|------------|----------|------|-------|
| `email` | yes | string | Visitor email |
| `phone` | yes | string | E.164 preferred |
| `purpose` | yes | string (JSON) | JSON-encoded `CheckinPurpose` — `{ "purpose": "...", "purpose_details": "...", "expected_duration_minutes": 30 }` |
| `bio_data` | no (default `{}`) | string (JSON) | Free-form bio fields keyed by `CheckinFieldDef.key` for `category === "bio"`. Must include any `required: true` bio keys. |
| `tenant_specific_data` | no (default `{}`) | string (JSON) | Free-form visit-specific fields keyed by `CheckinFieldDef.key` for `category === "tenant_specific"`. Must include any `required: true` tenant_specific keys. |
| `id_type` | no | enum | One of the `IDType` values. Required when `id_file` is uploaded. |
| `id_file` | no | file | ID document image (JPEG/PNG). When present, OCR + face verification run server-side and the visitor record is marked `verified=true`. |

### Success — 201

```json
{
  "success": true,
  "message": "Check-in submitted",
  "data": {
    "id": "507f1f77bcf86cd799439013",
    "tenant_id": "t123",
    "visitor_id": "v456",
    "checkin_config_id": "",
    "state": "pending_approval",
    "verified": true,
    "tenant_specific_data": { "purpose_text": "Q2 review" },
    "purpose": { "purpose": "meeting" },
    "date_created": 1712000000,
    "last_updated": 1712000000
  }
}
```

`checkin_config_id === ""` on the response means the submit landed in default-mode (the tenant hasn't customized their config yet). This is a diagnostic signal — downstream reports that group check-ins by config id should treat `""` as "default config".

### Error codes

| Status | Meaning |
|--------|---------|
| 400 | Missing `email`/`phone`, malformed JSON in a form field, or a required field (per config / defaults) is missing from `bio_data` + `tenant_specific_data`. |
| 404 | Tenant not found. |
| 409 | Visitor already has a pending check-in. |
| 422 | ID verification failed — either retry with a clearer image or resubmit without `id_file` / `id_type` to fall back to manual entry. |

### Validation against the default field set

When no config exists for the tenant, the server validates `bio_data | tenant_specific_data` against the default required-field keys: `full_name`, `email`, `phone`, `purpose`. (`company` is in defaults but not required.) The kiosk should already be rendering these fields from the `GET .../active-checkin-config` response, so this is transparent to the caller.

---

## 4. Creating the config (super_admin UI)

### Request

```
POST /v1/checkin-configs
Authorization: Bearer <super_admin access_token>
Content-Type: application/json
```

```json
{
  "required_fields": [
    { "key": "full_name", "label": "Full Name", "type": "text",  "required": true,  "category": "bio" },
    { "key": "email",     "label": "Email",     "type": "email", "required": true,  "category": "bio" },
    { "key": "phone",     "label": "Phone",     "type": "tel",   "required": true,  "category": "bio" },
    { "key": "company",   "label": "Company",   "type": "text",  "required": false, "category": "bio" },
    {
      "key": "host_employee_id",
      "label": "Who are you visiting?",
      "type": "select",
      "required": true,
      "category": "tenant_specific",
      "options_endpoint": "/v1/tenants/{tenant_id}/employees"
    },
    { "key": "purpose", "label": "Purpose of Visit", "type": "text", "required": true, "category": "tenant_specific" }
  ],
  "id_upload_enabled": true,
  "allow_returning_visitor_lookup": true,
  "active": true
}
```

**Do not send** `tenant_id` — the server overrides it from the auth token.

### Response — 202 Accepted (queued write)

```json
{
  "success": true,
  "message": "Check-in configuration creation queued",
  "data": {
    "id": "507f1f77bcf86cd799439012",
    "job_id": "a2c4e6f8-1234-4abc-8def-0123456789ab",
    "status": "queued"
  }
}
```

Poll `GET /v1/jobs/{job_id}` to confirm `status: "succeeded"` before showing the new config to the user. The `id` field is the final config id (pre-assigned before the worker runs).

---

## 5. Updating the config

```
PATCH /v1/checkin-configs/{checkin_config_id}
Authorization: Bearer <super_admin access_token>
Content-Type: application/json
```

```json
{
  "id_upload_enabled": false,
  "required_fields": [ /* full replacement list */ ]
}
```

All fields on `PATCH` are optional — send only what changed. **`required_fields` is replaced wholesale**, not merged — if you omit it, existing fields stay; if you send it, the new list replaces the old one entirely.

Response: same 202 Accepted shape as create.

---

## 6. Field model reference

### `CheckinConfigBase`

| Field | Type | Description |
|-------|------|-------------|
| `required_fields` | `CheckinFieldDef[]` | Ordered list of fields to render on the form |
| `id_upload_enabled` | `bool` (default `true`) | Show the "Scan ID / upload ID" affordance |
| `allow_returning_visitor_lookup` | `bool` (default `true`) | Show the "I've been here before" lookup step |
| `active` | `bool` (default `true`) | Inactive configs are ignored by the public endpoint |

### `CheckinFieldDef`

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Stable identifier (sent back on submit). Choose human-readable snake_case. |
| `label` | `str` | UI label |
| `type` | `str` | HTML input type: `text`, `email`, `tel`, `select`, `textarea`, `date`, `number` |
| `required` | `bool` | Whether the form must capture this before submit |
| `category` | `"bio"` \| `"tenant_specific"` | **`bio`** = reusable personal data, saved on the visitor profile and pre-filled on return visits. **`tenant_specific`** = collected every visit (host, purpose, etc.). |
| `options_endpoint` | `str?` | For `type: "select"` — a URL the frontend can GET to populate the dropdown. `{tenant_id}` is substituted client-side. |
| `options` | `{label, value}[]?` | Static options for `select`/radio when a dynamic endpoint isn't needed. Mutually exclusive with `options_endpoint`. |

---

## 7. Frontend rendering recipe

1. **On kiosk / public form mount**, call `GET /v1/public/tenants/{tenant_id}/active-checkin-config`.
2. Paint header with `tenant_name` and `logo_url` (if present).
3. **Partition `required_fields` by `category`:**
   - `bio` fields → first step ("Tell us about yourself"). Skip this step entirely if the returning-visitor lookup matched.
   - `tenant_specific` fields → second step ("Your visit today").
4. For each field:
   - Render the input based on `type`.
   - Mark with asterisk / red border if `required: true`.
   - If `type === "select"`:
     - If `options` is set, use it directly.
     - Else if `options_endpoint` is set, call it (substituting `{tenant_id}`) and populate.
5. If `id_upload_enabled === false`, hide the "Scan my ID" button.
6. If `allow_returning_visitor_lookup === false`, skip the lookup step.
7. **Defaults banner (super_admin dashboard only):** if `checkin_config_id === ""`, show a prompt to customize.

---

## 8. Admin UI — suggested layout

**Settings → Check-In Form** (visible only to `super_admin`):

- Toggle: "Require ID upload" → `id_upload_enabled`
- Toggle: "Allow returning visitors to skip the form" → `allow_returning_visitor_lookup`
- Editable table of fields with columns: Label · Type · Required · Category
  - Row actions: edit, delete, reorder (drag handle)
  - "Add field" button opens a modal with the `CheckinFieldDef` shape
- Preview pane rendering the public form in real time
- "Save" button → `POST` if `checkin_config_id === ""`, else `PATCH`

Show the returned `job_id` as a small "Saving…" indicator; poll `/v1/jobs/{job_id}` once per second until `succeeded` or `failed`, then surface the result.

---

## 9. Common mistakes

- **Calling `/v1/checkin-configs/{checkin_config_id}` before the tenant has a config.** That endpoint 404s by design (it's keyed by config id). For first-load / defaults, call `/v1/public/tenants/{tenant_id}/active-checkin-config` instead.
- **Submitting against `/v1/checkin-configs/{checkin_config_id}/submit` when you only have a tenant_id.** Use `/v1/public/tenants/{tenant_id}/submit` — it resolves the active config (or defaults) server-side.
- **Treating the empty `checkin_config_id` as an error.** It's the default-mode signal. Render the default form and prompt the super_admin to customize.
- **Sending `tenant_id` in the POST body.** It's taken from the auth token — any value sent is overridden server-side.
- **Expecting `PATCH` to merge `required_fields`.** It replaces the array wholesale. Read the current list first, modify it, then PATCH the full array back.
- **Hiding the "Scan ID" button based on stale config.** The kiosk should re-fetch the config (or use a cache with short TTL) so toggles propagate.
