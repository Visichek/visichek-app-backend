# Queue / Precompute / Gate-Cache Migration — Remaining Work

**Reference implementation:** `departments` (see `api/v1/department_route.py`, `services/department_writer.py`).
**Infrastructure already built:** `docker-compose.yml`, `core/queue/{write_pipeline,gate_cache,precompute,registrations}.py`, `schemas/queue_job_log_schema.py`, `repositories/queue_job_log_repo.py`.

## Per-route conversion checklist

For every **QUEUE** route below, follow these 7 steps:

1. Add `preassigned_id: Optional[str] = None` kwarg to the repository `create_*` function.
2. Forward `preassigned_id` through the service layer's `add_*` function.
3. Create `services/<entity>_writer.py` with `@write_handler("<entity>.{create,update,delete,...}")` handlers.
4. Register precompute loaders with `@register_precompute("<entity>.list", scope=PrecomputeScope.TENANT)` for hot GETs.
5. Add a new import line to `core/queue/registrations.py`.
6. Rewrite the route handlers: POST → 202 + `{id, job_id, status}` via `enqueue_write`; GET list (page 1) → `get_or_compute` with inline `_load_*_for_tenant`.
7. Update/remove affected tests in `tests/unit/test_routes.py` (+ integration tests that hit these endpoints) for the 202 contract.

After every cluster: `mypy --ignore-missing-imports --no-error-summary .` and `ruff check .`.

---

## Legend

- ✅ = done
- 🔄 = QUEUE (convert writes to `enqueue_write`)
- 🔥 = PRECOMPUTE (wire GET through `get_or_compute` + register a loader)
- 🔒 = STAY SYNC (auth-critical, webhook, streaming, or needs immediate DB/user feedback)
- ⚠️ = DISCUSS with user before converting (UX / correctness risk)

---

## Business CRUD (standard queue conversion)

### ✅ `department_route.py` — **DONE** (reference)

### 🔄 `appointment_route.py`
- `POST ""` → 🔄 `appointment.create`
- `GET ""` (list) → 🔥 precompute `appointments.list`
- `GET /{appointment_id}` → sync (HttpCache 60s)
- `PATCH /{appointment_id}` → 🔄 `appointment.update`
- `DELETE /{appointment_id}` → 🔄 `appointment.delete`

### 🔄 `branch_route.py`
- `POST ""` → 🔄 `branch.create` (enforces max_branches cap — keep cap check inside writer)
- `GET ""` (list) → 🔥 precompute `branches.list`
- `GET /{branch_id}` → sync
- `PUT /{branch_id}` → 🔄 `branch.update`
- `POST /{branch_id}/deactivate` → 🔄 `branch.deactivate`
- `DELETE /{branch_id}` → 🔄 `branch.delete` (must reject last-branch deletion inside writer)

### 🔄 `branding_route.py`
- `GET /public/tenant/{tenant_id}` → 🔥 precompute `branding.public` (public, tenant-keyed)
- `GET /tenant/{tenant_id}` → 🔥 precompute `branding.full`
- `PUT ""` → 🔄 `branding.upsert`
- `DELETE ""` → 🔄 `branding.delete`

### 🔄 `checkin_config_route.py`
- `GET /{checkin_config_id}` → sync (public)
- `GET /{checkin_config_id}/visitors/lookup` → sync (real-time)
- `POST /{checkin_config_id}/checkins` → ⚠️ visitor self-check-in — STAY SYNC, badge returned
- `POST ""` → 🔄 `checkin_config.create`
- `PATCH /{checkin_config_id}` → 🔄 `checkin_config.update`
- `GET ""` (list) → 🔥 precompute `checkin_configs.list`

### 🔄 `compliance_route.py`
- `GET /register`, `/deletion-logs`, `/consent-log`, `/export` → 🔥 precompute (tenant-keyed, possibly weekly instead of 60s — set `ttl=300+`)
- `POST /register` → 🔄 `compliance.register_consent`

### 🔄 `data_subject_request_route.py`
- `POST ""` → 🔄 `dsr.create`
- `GET ""` (list) → 🔥 precompute `dsr.list`
- `GET /{dsr_id}` → sync
- `PATCH /{dsr_id}` → 🔄 `dsr.update`

### 🔄 `discount_route.py` (application admin)
- `POST ""` → 🔄 `discount.create`
- `GET ""` (list) → 🔥 precompute `discounts.list` (scope=GLOBAL)
- `GET /code/{code}`, `GET /{discount_id}` → sync
- `PUT /{discount_id}` → 🔄 `discount.update`
- `POST /{discount_id}/disable` → 🔄 `discount.disable`
- `POST /validate` → 🔒 STAY SYNC (returns immediate validation result)
- `DELETE /{discount_id}` → 🔄 `discount.delete`

### 🔄 `incident_route.py`
- `POST ""` → 🔄 `incident.create`
- `GET ""` (list), `/approaching-deadline` → 🔥 precompute `incidents.list`, `incidents.approaching_deadline`
- `GET /{incident_id}` → sync
- `PATCH /{incident_id}` → 🔄 `incident.update`

### 🔄 `notification_route.py`
- `GET ""` (paginated list) → 🔥 precompute `notifications.list` (per-user scope — add `PrecomputeScope.USER`)
- `GET /unread-count` → 🔥 precompute `notifications.unread_count` (very hot — short TTL)
- `PATCH /{notification_id}/read` → 🔄 `notification.mark_read`
- `POST /read-all` → 🔄 `notification.mark_all_read`
- `DELETE /{notification_id}` → 🔄 `notification.delete`
- `GET /preferences` → sync (small, user-specific)
- `PUT /preferences` → 🔄 `notification.update_preferences`

> **Note:** Precompute loader currently only supports TENANT/GLOBAL scope. Add `PrecomputeScope.USER` before this cluster or key per-user results under `tenant:{tenant_id}:user:{user_id}`.

### 🔄 `plan_route.py` (application admin)
- `POST ""` → 🔄 `plan.create`
- `GET ""` (list) → 🔥 precompute `plans.list` (scope=GLOBAL, public plans are extremely hot)
- `GET /{plan_id}` → sync
- `PUT /{plan_id}` → 🔄 `plan.update` (must call `invalidate_plan_cache` fanout after)
- `POST /{plan_id}/activate` → 🔄 `plan.activate`
- `POST /{plan_id}/archive` → 🔄 `plan.archive`
- `POST /{source_plan_id}/clone` → 🔄 `plan.clone`
- `DELETE /{plan_id}` → 🔄 `plan.delete`

### 🔄 `privacy_notice_route.py`
- `POST ""` → 🔄 `privacy_notice.create`
- `GET /active` → 🔥 precompute `privacy_notice.active`
- `GET ""` (list) → 🔥 precompute `privacy_notices.list`
- `PATCH /{notice_id}` → 🔄 `privacy_notice.update`

### 🔄 `retention_route.py`
- `POST ""` → 🔄 `retention_policy.create`
- `GET ""` (list) → 🔥 precompute `retention_policies.list`
- `PATCH /{policy_id}` → 🔄 `retention_policy.update`

### 🔄 `sub_processor_route.py`
- `POST ""` → 🔄 `sub_processor.create`
- `GET ""` (list) → 🔥 precompute `sub_processors.list`
- `PATCH /{sp_id}` → 🔄 `sub_processor.update`
- `DELETE /{sp_id}` → 🔄 `sub_processor.delete`

### 🔄 `subscription_route.py` ⚠️
- All write paths currently call `invalidate_tenant_plan_cache`. Keep that call in the writer.
- `POST ""` → 🔄 `subscription.create` (⚠️ subscribe path — may trigger payment; ensure writer idempotent)
- `GET ""` (list admin) → 🔥 precompute `subscriptions.list` (scope=GLOBAL)
- `GET /tenant/{tenant_id}/active` → 🔥 precompute `subscription.active` (tenant-keyed)
- `GET /{subscription_id}` → sync
- `POST /change-plan` → 🔄 `subscription.change_plan`
- `POST /cancel` → 🔄 `subscription.cancel`
- `PUT /{subscription_id}/overrides` → 🔄 `subscription.update_overrides`

### 🔄 `super_admin_route.py`
- `GET /analytics` → 🔥 precompute `super_admin.analytics` (tenant-keyed)
- `GET /departments`, `/admins`, `/visitor-log` → 🔥 precompute matching resources
- `POST /departments` → 🔄 reuse `department.create` writer
- `POST /admins/invite` → 🔄 `super_admin.invite_admin` (sends email via queue already — keep that)
- `POST /registration-qr` → 🔒 STAY SYNC (signed token must be returned immediately)
- `PATCH /admins/{user_id}/department` → 🔄 `super_admin.update_admin_department` (invalidate gate_cache for that user)
- `PATCH /admins/{user_id}/mfa` → 🔄 `super_admin.update_admin_mfa` (invalidate gate_cache for that user)

### 🔄 `system_user_route.py` (partial — only user-management endpoints)
- All auth endpoints → 🔒 STAY SYNC (login/refresh/signup/verify-otp/logout/2fa)
- `GET /me` → sync
- `GET ""` (list) → 🔥 precompute `system_users.list`
- `PATCH /{user_id}` → 🔄 `system_user.update` (invalidate gate_cache for that user)
- `DELETE /{user_id}` → 🔄 `system_user.delete` (invalidate gate_cache)

### 🔄 `tenant_route.py`
- `POST ""` → 🔄 `tenant.create` (⚠️ super_admin creation is via `/admins/tenants/bootstrap` which stays sync; this POST is for admin-only tenant scaffolding)
- `GET ""` (list, admin) → 🔥 precompute `tenants.list` (scope=GLOBAL)
- `GET /{tenant_id}` → sync
- `PATCH /{tenant_id}` → 🔄 `tenant.update`

### 🔄 `tenant_settings_route.py`
- `GET /{tenant_id}/settings` → 🔥 precompute `tenant.settings` (tenant-keyed)
- `PATCH /{tenant_id}/settings` → 🔄 `tenant_settings.update`

### 🔄 `unified_tenant_settings_route.py`
- `GET ""` → 🔥 precompute `tenant.settings` (reuse loader — tenant_id from principal)
- `PATCH ""` → 🔄 reuse `tenant_settings.update` writer (infer tenant_id from principal)

### 🔄 `unified_platform_settings_route.py`
- `GET ""` → 🔥 precompute `platform.settings` (scope=GLOBAL)
- `PATCH ""` → 🔄 `platform_settings.update`

### 🔄 `user_settings_route.py`
- `GET ""` → sync (per-user, small, low hit rate — skip precompute)
- `PATCH ""` → 🔄 `user_settings.update`

### 🔄 `visitor_profile_route.py`
- `GET /search` → 🔒 STAY SYNC (interactive search)
- `GET ""` (list) → 🔥 precompute `visitor_profiles.list`
- `GET /{profile_id}` → sync
- `PATCH /{profile_id}` → 🔄 `visitor_profile.update`

---

## Dashboards & read-only aggregation (PRECOMPUTE only)

### 🔥 `admin_dashboard_route.py` (application admin)
- `GET /stats` → 🔥 precompute `admin_dashboard.stats` (scope=GLOBAL, TTL 120s — heavy aggregation)
- `GET /billing` → 🔥 precompute `admin_dashboard.billing` (GLOBAL, accepts date-range query — cache key must include range)
- `GET /billing/discrepancies` → 🔥 precompute `admin_dashboard.billing_discrepancies` (GLOBAL)

### 🔥 `dashboard_route.py` (tenant)
- `GET /stats` → 🔥 precompute `dashboard.stats` (TENANT)
- `GET /visitors` → 🔥 precompute `dashboard.visitors`
- `GET /visitors/active` → 🔥 precompute `dashboard.visitors_active` (very hot — short TTL 30s)
- `GET /export` → 🔒 STAY SYNC (streaming CSV/PDF download)

### 🔥 `audit_route.py`
- `GET ""` → 🔥 precompute `audit.recent` (first page, tenant-keyed)

### 🔥 `usage_route.py`
- `GET /tenant/{tenant_id}/summary` → 🔥 precompute `usage.summary` (tenant-keyed)
- `GET /my-usage` → 🔥 precompute `usage.my_usage` (tenant-keyed)

### 🔥 `invoice_route.py`
- `GET /tenant/{tenant_id}` → 🔥 precompute `invoices.for_tenant`
- `GET /admin` → 🔥 precompute `invoices.admin_list` (GLOBAL)
- `GET /{invoice_id}` → sync
- `GET /{invoice_id}/pdf` → 🔒 STAY SYNC (streaming PDF)

### 🔥 `settings_manifest_route.py`
- `GET ""` → 🔥 precompute `settings.manifest` (per-user — blocked on `PrecomputeScope.USER`)

### 🔥 `session_management_route.py`
- `GET ""` → sync (per-user, security-sensitive — don't cache)
- `DELETE /{session_id}`, `POST /revoke-all` → 🔒 STAY SYNC (revocation must be immediate; also call `gate_cache.invalidate_gate`)

---

## ⚠️ Visitor flow — needs user decision

### `visitor_route.py` (check-in / check-out lifecycle)

All of these are real-time visitor operations that currently return badges, QR tokens, or verification state. Queueing them breaks the front-desk flow (visitor stands waiting for a badge PDF). **Recommend STAY SYNC unless user disagrees.**

- `POST /registration-qr` → 🔒 sync (returns signed token)
- `POST /check-in` → ⚠️ DISCUSS — returns full session + badge
- `POST /check-out` → ⚠️ DISCUSS — returns session
- `GET /active` → 🔥 precompute `visitors.active` (very hot, TENANT, TTL 30s)
- `GET /sessions` → 🔥 precompute `visitors.sessions_page1`
- `GET /sessions/pending` → 🔥 precompute `visitors.sessions_pending`
- `GET /sessions/{id}` → sync
- `POST /sessions/{id}/confirm` → ⚠️ DISCUSS — generates badge
- `POST /sessions/{id}/deny` → ⚠️ DISCUSS
- `POST /verify/id-scan` → 🔒 sync (OCR result)
- `POST /sessions/{id}/apply-id-scan` → ⚠️ DISCUSS
- `PATCH /sessions/{id}/update-draft` → 🔄 `visitor.update_draft`
- `POST /sessions/{id}/host-approve` → ⚠️ DISCUSS
- `GET /sessions/{id}/badge` → 🔒 sync (streaming PDF)

### `checkin_submit_route.py` / `checkin_route.py`
- `POST /{checkin_config_id}/submit` → ⚠️ DISCUSS (public visitor submission; needs immediate ack + badge)
- `POST` in `checkin_route.py` → ⚠️ DISCUSS

### `checkout_route.py`
- `POST /sessions` → 🔄 `checkout_session.create` (queuable — payment page URL is the payload)
- `POST /sessions/{id}/cancel` → 🔄 `checkout_session.cancel`
- `GET /sessions` → 🔥 precompute
- `GET /sessions/{id}` → sync

---

## 🔒 STAY SYNC (auth, webhooks, streaming, real-time lookups)

These route files need **no conversion** — every endpoint inside them must stay synchronous.

- `account_route.py` — account deletion (immediate confirmation)
- `admin_route.py` — all auth (signup, login, refresh, verify-otp, logout, bootstrap); exception: `POST /tenants/{id}/offboard` and `GET /tenants/{id}/offboarding-summary` could queue, low priority.
- `admin_settings_route.py` — change-password, 2FA, sessions; `PATCH /settings`, `PATCH /preferences`, `PATCH /platform-settings` can queue but benefit is marginal.
- `auth_management_route.py` — all auth
- `app_payment_route.py` — payment simulator callback
- `documents_route.py` — `/upload-intents`, `/complete`, `/upload-local` (presigned URL orchestration); `DELETE /{id}` could queue.
- `face_crop_route.py` — image processing
- `id_extraction_route.py` — OCR
- `payments_route.py` — `/intents`, `/webhooks/*`, `/{id}/refund`, `/webhooks/replay/{id}`
- `public_registration_route.py` — public real-time visitor flow
- `public_rights_route.py` — public DSR submission (sync ack expected)
- `system_user_route.py` — auth endpoints only (user-management PATCH/DELETE go to queue — see above)
- `system_user_settings_route.py` — auth/session/2FA
- `user_route.py` — auth endpoints
- `visitor_verification_route.py` — `/test` diagnostic

---

## Cross-cutting follow-ups

- [ ] Add `PrecomputeScope.USER` to `core/queue/precompute.py` before converting `notification_route.py` or `settings_manifest_route.py`.
- [ ] Add explicit `invalidate_gate(user_id, role)` calls in writers that mutate admins / system_users / passwords / 2FA so account changes propagate faster than the 5-min TTL: `super_admin.update_admin_mfa`, `system_user.update`, `system_user.delete`, all password-change paths.
- [ ] Confirm `invalidate_tenant_plan_cache` is preserved inside subscription writers.
- [ ] Integration tests (`tests/integration/test_plan_lifecycle.py`, `test_flows.py`, `test_super_admin_flows.py`, `test_dashboard_export_flows.py`) will break on the 202 contract — update alongside each cluster.
- [ ] `tests/load/locustfile.py` and `tests/load/setup_load_test_data.py` assume 201 for `POST /v1/departments` — update now that it returns 202.
- [ ] Add a `GET /v1/jobs/{job_id}` endpoint backed by `queue_job_log_repo.get_job_log_by_task_id` so the frontend can poll per-job status.
- [ ] Document the new pipeline in `CLAUDE.md` (new "Queued Write Pipeline" section, parallel to the existing "Subscription & Plan System" section).
- [ ] `tests/unit/test_routes.py::TestSystemUserRoutes::test_login_success` is pre-existingly broken on master — track separately, not caused by this migration.

---

## Recommended cluster order

1. **Small, independent, tenant-scoped** (low blast radius): `branch`, `branding`, `sub_processor`, `retention`, `privacy_notice`, `data_subject_request`.
2. **Core tenant CRUD**: `appointment`, `incident`, `visitor_profile`, `checkin_config`, `tenant_settings` (+ unified).
3. **Tenant + application-admin overlap**: `tenant`, `super_admin` (partial), `system_user` (partial).
4. **Billing cluster** (high-stakes, needs `invalidate_tenant_plan_cache` wiring): `subscription`, `plan`, `discount`, `compliance`.
5. **Notifications + settings** (needs `PrecomputeScope.USER`): `notification`, `user_settings`, `settings_manifest`, `admin_settings` (partial).
6. **Dashboards** (precompute only): `dashboard`, `admin_dashboard`, `audit`, `usage`, `invoice`.
7. **Visitor flow** (⚠️ confirm approach first): `visitor`, `checkin_submit`, `checkin`, `checkout`.
