# Geofencing

## TL;DR

1. **Opt-in per tenant.** Geofencing is off by default. Super admins turn
   it on in tenant settings and pick a radius and (optionally) a fixed
   reference coordinate.
2. **Ingestion is free.** Every authenticated request that includes an
   `X-User-Location: <lat>,<lng>[,<accuracy_m>]` header piggybacks a
   fire-and-forget location write through the existing gate check —
   there is no new endpoint to call and no new collection.
3. **Storage is ephemeral.** Locations live in Redis with a 10-minute
   TTL. If a staff member stops hitting the API, their presence
   disappears. Nothing is persisted to MongoDB, and there is no location
   history.
4. **Enforcement is at visitor submit.** The public `POST /v1/public/tenants/{tenant_id}/submit`
   and `/submit-by-visitor-id` endpoints check the visitor's lat/lng
   against the tenant's configured radius before creating the check-in.
5. **Two enforcement modes** (configured via tenant settings):
   - **Reference-point mode** — visitor must be within `radius_m` of a
     fixed office coordinate. Deterministic, recommended.
   - **Approver-proximity mode** — visitor must be within `radius_m` of
     at least one active approver (`receptionist`, `super_admin`, or
     `dept_admin`) who has reported a location in the last 10 minutes.
     Used automatically when no reference coordinate is set.
6. **Rejection = HTTP 403** with error code `GEOFENCE_VIOLATION` and a
   `details.reason` that explains *why* the check-in was blocked.

## When to enable this

Geofencing is the right control when you want to guarantee the visitor
is physically at the tenant site — e.g. a secure facility, an office
with strict attendance policies, or a hospital where you need to prove
reception actually processed the walk-in. It is **not** the right
control for a multi-site tenant where visitors self-check-in remotely
(a satellite office, a field job). Leave it off for those cases.

## Super admin configuration

Update via `PATCH /v1/tenant-settings` (super admin only):

```json
{
  "geofencing_enabled": true,
  "geofencing_radius_meters": 50,
  "geofencing_reference_lat": 6.524379,
  "geofencing_reference_lng": 3.379206
}
```

### Fields

| Field | Default | Allowed range | Notes |
| --- | --- | --- | --- |
| `geofencing_enabled` | `false` | `bool` | Off by default. Turning it off skips all geofence checks. |
| `geofencing_radius_meters` | `50` | `5`–`5000` | See "Picking a radius" below. |
| `geofencing_reference_lat` | `null` | `-90`–`90` | Optional. Must be set together with `_lng`. |
| `geofencing_reference_lng` | `null` | `-180`–`180` | Optional. Must be set together with `_lat`. |

### Picking a radius

| Company size | Recommended radius | Why |
| --- | --- | --- |
| Small single-building office | **10–20m (strict)** | Outdoor GPS only — indoor users may be rejected even when present. Use strict radii only if your reception is outdoors or near a window. |
| Medium office, typical kiosk at reception | **50m (standard)** | The default. Tolerates normal indoor GPS drift of 20–30m while still rejecting visitors who are clearly off-site. |
| Large campus, warehouse, or multi-building site | **100–500m** | Covers the whole site so a visitor entering through any gate is accepted. |
| Very large industrial / estate-scale facility | **500–5000m** | The upper bound is 5000m — anything larger is effectively "off". |

Indoor GPS from a browser is typically accurate to 20–50m depending on
the device, so radii below 50m will produce a steady trickle of false
rejections. Start at 50m, watch your rejection rate for a week, then
tighten if you see the radius is too permissive in practice.

### Reference point vs. approver proximity

- **Reference point mode** (recommended). Set `geofencing_reference_lat`
  and `geofencing_reference_lng` to the office's coordinates. The
  backend measures visitor-to-office directly. Deterministic —
  decisions don't depend on whether approvers are logged in.
- **Approver-proximity mode.** Leave the reference lat/lng null.
  The backend scans all active approvers (`receptionist`, `super_admin`,
  `dept_admin`) who have reported a location in the last 10 minutes and
  allows the check-in if any one of them is within the radius. If no
  approver has reported a location, the check-in is rejected
  (`reason: "no_active_approvers"`). Useful when staff roam between
  multiple sites — the geofence effectively follows them.

Use **reference point mode** unless you have a specific reason not to.
It's simpler to reason about, does not depend on staff having location
permission enabled, and behaves predictably if a whole shift goes
offline.

### Turning it off

Set `geofencing_enabled` to `false`. All visitor submits will succeed
regardless of location. You do not need to clear the radius or
reference point — they are simply ignored.

## How staff locations are collected

### Frontend contract

On every authenticated request, the frontend reads
`navigator.geolocation.getCurrentPosition(...)` in the browser and
attaches a header:

```
X-User-Location: 6.524379,3.379206,18
```

Format: `<lat>,<lng>[,<accuracy_m>]`. The optional third field is the
browser-reported accuracy in metres — useful for debugging but not used
for enforcement today.

If the user has not granted geolocation permission, **omit the header**.
Do not send `0,0` or a stale cached value. A missing header is
interpreted as "no recent location" and the user will not be counted
toward approver-proximity.

### Backend ingestion path

1. The auth dependency chain
   ([`security/account_status_check.py:capture_user_location_from_request`](../security/account_status_check.py)
   for admins/users,
   [`security/auth.py:_capture_location`](../security/auth.py) for
   system users) reads the header.
2. Header is parsed by
   [`core/geofencing.py:parse_location_header`](../core/geofencing.py).
3. A `user_location.update` task is enqueued via `enqueue_write` —
   fire-and-forget.
4. The celery worker runs
   [`services/user_location_writer.py`](../services/user_location_writer.py)
   which writes to Redis with a 10-minute TTL:
   - Primary key: `user_location:{user_id}` → `{lat, lng, role, tenant_id, ts, accuracy_m?}`
   - Secondary index: `user_location:tenant:{tenant_id}` (Redis set of user ids)

### Why not a middleware?

We considered a middleware that would read the header and enqueue
unconditionally. We chose the gate-check approach because:

- Only authenticated requests have a principal — otherwise we don't
  know who the location belongs to.
- The gate checks already run for exactly the right set of requests.
- It means public endpoints (visitor submit, login, etc.) don't
  accidentally capture tenant-staff locations.

## How visitor submits enforce the geofence

### Public submit contracts

Both public kiosk endpoints now accept visitor coordinates:

**`POST /v1/public/tenants/{tenant_id}/submit`** — multipart form fields:

- `visitor_lat` (optional float)
- `visitor_lng` (optional float)

**`POST /v1/public/tenants/{tenant_id}/submit-by-visitor-id`** — JSON body:

```json
{
  "visitor_id": "6524abcdef0123456789abcd",
  "purpose": { ... },
  "tenant_specific_data": { ... },
  "visitor_lat": 6.524379,
  "visitor_lng": 3.379206,
  "visitor_location_accuracy_m": 12
}
```

If the tenant has `geofencing_enabled=false`, `visitor_lat` and
`visitor_lng` are ignored. If geofencing is enabled and either is
missing, the request is rejected with `GEOFENCE_VIOLATION` and
`details.reason = "missing_visitor_location"` so the kiosk can prompt
the visitor to grant location access.

### Decision flow

[`services/checkin_service.py:_enforce_tenant_geofence`](../services/checkin_service.py)
runs before the visitor record is touched:

1. Load tenant settings (upsert defaults on first access).
2. If `geofencing_enabled` is false → allow.
3. If `visitor_lat`/`visitor_lng` is null → reject, reason
   `missing_visitor_location`.
4. If tenant has a reference point → haversine distance vs. reference,
   allow or reject based on radius.
5. Otherwise scan active approvers; allow if any one is within radius.
6. If no active approvers exist → reject, reason `no_active_approvers`.

### Error shape

```json
{
  "success": false,
  "message": "You appear to be outside the check-in zone. Please move closer to the reception area and retry.",
  "code": "GEOFENCE_VIOLATION",
  "details": {
    "reason": "outside_reference_point",
    "distance_m": 412.3,
    "radius_m": 50
  }
}
```

### Reason codes

| `details.reason` | Meaning | Typical UI prompt |
| --- | --- | --- |
| `missing_visitor_location` | Tenant requires geofencing; client didn't send coords | "Please enable location access in your browser." |
| `outside_reference_point` | Visitor too far from fixed office coordinate | "Move closer to the reception area." |
| `outside_approver_radius` | Visitor too far from any active approver | "Ask reception to retry on your behalf." |
| `no_active_approvers` | No approver has reported a location in 10 min | "No approver is currently on-site." |
| `tenant_misconfigured` | Enabled with no reference point AND no approvers found | "Ask the super admin to set a reference location." |

## Privacy & audit

- Staff locations live **only in Redis** with a 10-minute TTL. There is
  no MongoDB collection and no history table.
- Locations are invisible to other tenants — every key is scoped by the
  tenant's `user_location:tenant:{tenant_id}` index.
- The `queue_job_log` audit row for each `user_location.update` task
  contains the coordinates in its redacted payload. If you consider
  coordinates sensitive, redact them by adding `"lat"` and `"lng"` to
  the `_REDACTED_KEYS` set in
  [`core/queue/write_pipeline.py`](../core/queue/write_pipeline.py).
- Before enabling geofencing, the tenant's DPO should update the
  privacy notice to cover "We collect your approximate location when
  you are logged in, retained for up to 10 minutes, to verify that
  visitor check-ins happen on-site." Do not enable this without
  informed-consent copy.

## Monitoring

- Redis key prefix: `user_location:*`. Count active staff with
  `SCAN 0 MATCH user_location:*` (excluding the `:tenant:*` index).
- Rejection rate: grep structured logs for `GEOFENCE_VIOLATION` or
  filter audit events by action once you add one. The check-in service
  raises `AppException` for violations so they surface in the normal
  error envelope response.
- If the rejection rate spikes unexpectedly, the most likely cause is a
  radius that is too tight for the indoor GPS accuracy at the site.
  Bump the radius by 25% and re-measure.

## Files changed

- [`schemas/tenant_settings_schema.py`](../schemas/tenant_settings_schema.py)
  — added `geofencing_enabled`, `geofencing_radius_meters`,
  `geofencing_reference_lat`, `geofencing_reference_lng` with bounds
  validation.
- [`core/errors.py`](../core/errors.py) — new `GEOFENCE_VIOLATION`
  error code.
- [`core/geofencing.py`](../core/geofencing.py) — new module:
  `parse_location_header`, `store_user_location`, `haversine_meters`,
  `check_visitor_within_geofence`.
- [`services/user_location_writer.py`](../services/user_location_writer.py)
  — new `@write_handler("user_location.update")`.
- [`core/queue/registrations.py`](../core/queue/registrations.py) —
  register `user_location_writer` so celery workers pick it up.
- [`security/account_status_check.py`](../security/account_status_check.py)
  — added `capture_user_location_from_request`; wired into admin and
  user gates.
- [`security/auth.py`](../security/auth.py) — `_capture_location`
  helper called from `verify_system_user_token` closures and
  `verify_any_system_user_token`.
- [`schemas/public_registration_schema.py`](../schemas/public_registration_schema.py)
  — added `visitor_lat`, `visitor_lng`, `visitor_location_accuracy_m`
  to `PublicReturningVisitorSubmitRequest`.
- [`services/checkin_service.py`](../services/checkin_service.py) —
  new `_enforce_tenant_geofence` helper; threaded
  `visitor_lat`/`visitor_lng` through both submit entry points.
- [`api/v1/public_registration_route.py`](../api/v1/public_registration_route.py)
  — accept `visitor_lat`/`visitor_lng` form fields on the tenant
  submit endpoint; forward JSON fields on the returning-visitor submit.
