# VisiChek MVP Backend -- Implementation TODO

> Based on: Diagram Explanation (data model), MVP Spec, NDPA Compliance Analysis, OCR Research, Development Plan

---

## Phase 0: Foundation (Roles, Tenants, Multi-Tenancy)

### Role System Refactor
- [ ] Expand `security/principal.py` role Literal to: receptionist, dept_admin, super_admin, auditor, security_officer, dpo
- [ ] Add `verify_system_user_token(*allowed_roles)` factory in `security/auth.py`
- [ ] Add `check_system_user_permissions` dependency in `security/account_status_check.py`
- [ ] Update `core/role_config.py` with rate limits for 6 new roles

### Shared Enums (in `schemas/imports.py`)
- [ ] `SystemUserRole` -- receptionist, dept_admin, super_admin, auditor, security_officer, dpo
- [ ] `VisitStatus` -- registered, pending_verification, checked_in, checked_out, denied, cancelled
- [ ] `CheckInMethod` -- QR, ID_scan, manual
- [ ] `VerificationMethod` -- id_scan, qr_upload, host_approval
- [ ] `VerificationStatus` -- verified, unverified, denied
- [ ] `AppointmentStatus` -- scheduled, fulfilled, cancelled, missed
- [ ] `LawfulBasis` -- consent, legitimate_interest
- [ ] `DeletionAction` -- delete, anonymise
- [ ] `DSRType` -- access, correction, deletion, consent_withdrawal
- [ ] `DSRStatus` -- pending, in_progress, completed, rejected
- [ ] `IncidentStatus` -- open, investigating, contained, reported_to_ndpc, closed
- [ ] `BadgeFormat` -- A6, A7

### TenantCompany Model
- [ ] Create `schemas/tenant_schema.py` (name, lawful_basis, notice_display_mode, retention_days, default_deletion_action, dpo_email, privacy_policy_url, hosting_country, cross_border_approved)
- [ ] Create `repositories/tenant_repo.py` (CRUD on `tenant_companies` collection)
- [ ] Create `services/tenant_service.py`
- [ ] Create `api/v1/tenant_route.py` (POST, GET, PATCH -- super_admin only)

### Department Model
- [ ] Create `schemas/department_schema.py` (tenant_id, code, name, is_active)
- [ ] Create `repositories/department_repo.py` (CRUD, always filtered by tenant_id)
- [ ] Create `services/department_service.py`
- [ ] Create `api/v1/department_route.py` (CRUD -- super_admin + dept_admin)

### SystemUser Model
- [ ] Create `schemas/system_user_schema.py` (tenant_id, department_id, name, email, hashed_password, role, is_active)
- [ ] Create `repositories/system_user_repo.py`
- [ ] Create `services/system_user_service.py` (login, signup/invite, token issuance)
- [ ] Create `api/v1/system_user_route.py` (login, signup, me, refresh, list, update, deactivate)

### UserSession Model
- [ ] Create `schemas/user_session_schema.py` (user_id, tenant_id, ip, device_signature, user_agent, mfa_passed, started_at, last_activity)
- [ ] Create `repositories/user_session_repo.py`

### Multi-Tenancy Plumbing
- [ ] Add `tenant_id` and `role` fields to `schemas/tokens_schema.py`
- [ ] Update `repositories/tokens_repo.py` to store/retrieve tenant_id
- [ ] Update `services/auth_helpers.py` to include tenant_id in token payload
- [ ] Add `get_current_tenant()` dependency helper

### Config Updates
- [ ] Add OCR, QR, retention env vars to `core/settings.py`
- [ ] Update `.env.example` with new variables
- [ ] Register new routers in `main.py`

---

## Phase 1: Visitor Operations Core

### VisitorProfile Model
- [ ] Create `schemas/visitor_profile_schema.py` (tenant_id, phone, email, full_name, company, photo_url, id_type, id_number, id_image_url, profiling_preference, last_verified_at, deleted_at)
- [ ] Create `repositories/visitor_profile_repo.py` (soft-delete support, lookup by phone)
- [ ] Create `services/visitor_profile_service.py` (create/lookup by phone, repeat visitor merge)
- [ ] Create `api/v1/visitor_profile_route.py` (search, get, update)

### VisitSession Model
- [ ] Create `schemas/visit_session_schema.py` (all fields: visitor, dept, host, receptionist, check-in/out methods, verification, status, consent fields, badge fields, timestamps, name snapshots)
- [ ] Create `repositories/visit_session_repo.py` (CRUD + active visitors query + aggregations)
- [ ] Create `services/visit_session_service.py` (check-in flow, check-out flow, status transitions)
- [ ] Create `api/v1/visitor_route.py` (POST /check-in, POST /check-out, GET /active, GET /{id}/badge)

### Check-In Flow Implementation
- [ ] Visitor profile lookup/creation by phone number
- [ ] OCR integration for ID scan check-in method
- [ ] Privacy notice version lookup and consent capture based on tenant's lawful_basis
- [ ] Visit session creation with all required fields and name snapshots
- [ ] Badge PDF generation with HMAC-signed QR token
- [ ] Appointment linking (match visitor+host+time, set status=fulfilled)
- [ ] Audit log entry for check-in event

### Check-Out Flow Implementation
- [ ] QR token HMAC verification and expiry check
- [ ] Session status update to checked_out with check_out_time
- [ ] Audit log entry for check-out event

### Badge Generation System
- [ ] Create `services/badge_service.py` (PDF generation using ReportLab, A6/A7 sizes)
- [ ] Badge content: visitor name, photo, company, host/dept, date, time-in, QR code
- [ ] Store generated PDF via existing DocumentStorageManager

### QR Token Security
- [ ] Create `services/qr_service.py` (HMAC-SHA256 signing with QR_SIGNING_SECRET)
- [ ] Token format: base64url(session_id | expiry | hmac)
- [ ] Verification: parse, recompute HMAC, check expiry
- [ ] Add `QR_SIGNING_SECRET` to env config

### OCR Provider Integration
- [ ] Create `core/ocr/types.py` (OCRResult dataclass: full_name, id_number, id_type, confidence)
- [ ] Create `core/ocr/provider.py` (abstract base class)
- [ ] Create `core/ocr/structocr_provider.py` (StructOCR HTTP integration via httpx)
- [ ] Create `core/ocr/manager.py` (singleton manager, same pattern as storage/payments)
- [ ] Configure OCR manager in `main.py` lifespan
- [ ] Add `OCR_PROVIDER`, `OCR_API_KEY`, `OCR_API_URL` to settings

### ExpectedAppointment Model
- [ ] Create `schemas/appointment_schema.py`
- [ ] Create `repositories/appointment_repo.py`
- [ ] Create `services/appointment_service.py`
- [ ] Create `api/v1/appointment_route.py` (CRUD -- admin creates expected visitors)

### PrivacyNoticeVersion Model
- [ ] Create `schemas/privacy_notice_schema.py`
- [ ] Create `repositories/privacy_notice_repo.py`
- [ ] Create `services/privacy_notice_service.py` (create versions, get active for tenant)
- [ ] Create `api/v1/privacy_notice_route.py` (CRUD -- super_admin + dpo)

### Dependencies
- [ ] Add to `requirements.txt`: qrcode[pil], reportlab, Pillow, httpx

---

## Phase 2: Admin Dashboard & Analytics

### Dashboard API
- [ ] Create `services/dashboard_service.py` (MongoDB aggregation: visitor counts, avg duration, peak hours, dept breakdown)
- [ ] Create `api/v1/dashboard_route.py`:
  - [ ] `GET /dashboard/stats` -- visitor counts, avg duration, peak hours (dept_admin, super_admin)
  - [ ] `GET /dashboard/visitors` -- paginated visitor log with search/filter by name, date, host, status
  - [ ] `GET /dashboard/visitors/active` -- currently checked-in visitors (receptionist, dept_admin)
  - [ ] `GET /dashboard/export?format=csv|xlsx` -- export visitor logs (dept_admin, super_admin)

### Export Service
- [ ] Create `services/export_service.py` (CSV via stdlib, Excel via openpyxl)
- [ ] Return StreamingResponse with Content-Disposition header

### Super Admin Endpoints
- [ ] Create `api/v1/super_admin_route.py`:
  - [ ] `GET /super-admin/analytics` -- company-wide cross-department stats
  - [ ] `GET /super-admin/departments` -- list all departments
  - [ ] `POST /super-admin/departments` -- create department
  - [ ] `GET /super-admin/admins` -- list all system users
  - [ ] `POST /super-admin/admins/invite` -- invite new admin/user

### Dependencies
- [ ] Add to `requirements.txt`: openpyxl

---

## Phase 3: Privacy & Compliance (NDPA/NDPR)

### DataSubjectRequest Model
- [ ] Create `schemas/data_subject_request_schema.py` (visitor_id, type, status, sla_deadline, identity_verified)
- [ ] Create `repositories/data_subject_request_repo.py`
- [ ] Create `services/data_subject_request_service.py` (create, process each type: access/correction/deletion/consent_withdrawal)
- [ ] Create `api/v1/data_subject_request_route.py` (CRUD -- dpo + super_admin)

### RetentionPolicy Model
- [ ] Create `schemas/retention_policy_schema.py` (tenant_id, scope, retention_days, action)
- [ ] Create `repositories/retention_policy_repo.py`
- [ ] Create `api/v1/retention_route.py` (CRUD -- dpo + super_admin)

### Auto-Deletion Scheduler
- [ ] Create `services/retention_service.py` with `run_retention_cleanup()`:
  - [ ] Query expired data per tenant's retention policies
  - [ ] Delete or anonymise expired records
  - [ ] Write DeletionLog entries for each action
  - [ ] Log to SystemAuditLog
- [ ] Register APScheduler job in `main.py` lifespan (runs every 24h)

### DeletionLog Model
- [ ] Create `schemas/deletion_log_schema.py` (entity_type, entity_id, reason, action, performed_by)
- [ ] Create `repositories/deletion_log_repo.py` (insert + query only -- append-only)

### SubProcessor Registry
- [ ] Create `schemas/sub_processor_schema.py` (provider, purpose, jurisdiction, dpa_signed, uses_data_for_training)
- [ ] Create `repositories/sub_processor_repo.py`
- [ ] Create `services/sub_processor_service.py`
- [ ] Create `api/v1/sub_processor_route.py` (CRUD -- dpo + super_admin)

### DataProcessingRegister
- [ ] Create `schemas/data_processing_register_schema.py` (field, purpose, lawful_basis, retention, sub_processor_id)
- [ ] Create `repositories/data_processing_register_repo.py`
- [ ] Create `api/v1/compliance_route.py` (GET /compliance/register, GET /compliance/deletion-logs)

---

## Phase 4: Security & Governance

### SystemAuditLog Model (APPEND-ONLY)
- [ ] Create `schemas/audit_log_schema.py` (tenant_id, actor_id, actor_name, action, target_entity, target_id, ip, device_signature, timestamp)
- [ ] Create `repositories/audit_log_repo.py` (insert + paginated query ONLY -- NO update/delete)
- [ ] Create `services/audit_service.py` with `log_action()` helper
- [ ] Create `core/audit_middleware.py` (FastAPI dependency for automatic admin action logging)
- [ ] Create `api/v1/audit_route.py` (GET /audit-logs -- auditor + super_admin + dpo)

### IncidentLog Model
- [ ] Create `schemas/incident_log_schema.py` (reporter_id, type, status, description, risk_level, data_affected, mitigation_steps, ndpc_notified)
- [ ] Create `repositories/incident_log_repo.py`
- [ ] Create `services/incident_service.py` (CRUD, status transitions, NDPC email notification)
- [ ] Create `api/v1/incident_route.py` (CRUD -- security_officer + super_admin)

### Audit Integration
- [ ] Add `audit_log_action` dependency to admin/management routes
- [ ] Add explicit `audit_service.log_action()` calls in check-in, check-out, DSR processing, retention cleanup services

---

## Phase 5: Integration, Testing & Polish

### Seed Data
- [ ] Update `seed.py` with:
  - [ ] Default TenantCompany with NDPA-compliant settings
  - [ ] 2-3 sample departments
  - [ ] Super admin SystemUser
  - [ ] Sample PrivacyNoticeVersion
  - [ ] Default RetentionPolicy entries (visit sessions: 3 years, ID images: 30 days)
  - [ ] Sample SubProcessor entries (OCR provider)

### MongoDB Indexes
- [ ] Create `scripts/create_indexes.py` for all collections:
  - [ ] `tenant_companies` -- unique on "name"
  - [ ] `departments` -- unique compound on (tenant_id, code)
  - [ ] `system_users` -- unique compound on (tenant_id, email)
  - [ ] `user_sessions` -- index on (user_id, started_at desc)
  - [ ] `visitor_profiles` -- unique compound on (tenant_id, phone)
  - [ ] `visit_sessions` -- compound on (tenant_id, status), (tenant_id, check_in_time desc), unique sparse on badge_qr_token
  - [ ] `expected_appointments` -- compound on (tenant_id, scheduled_datetime)
  - [ ] `system_audit_logs` -- compound on (tenant_id, timestamp desc), (tenant_id, actor_id)
  - [ ] `incident_logs` -- compound on (tenant_id, status)

### Integration Tests
- [ ] Manual check-in flow (phone + purpose -> session + badge)
- [ ] Repeat visitor recognition (same phone -> same profile)
- [ ] ID scan with OCR (ID image -> extracted name -> profile populated)
- [ ] QR check-out (badge token -> session checked_out)
- [ ] Dashboard stats accuracy (multiple check-ins -> correct counts)
- [ ] CSV/Excel export (visitor logs -> correct file contents)
- [ ] DSR access request (compile visitor data)
- [ ] DSR deletion request (anonymise profile, DeletionLog created)
- [ ] Retention auto-cleanup (expired records anonymised/deleted)
- [ ] Audit trail completeness (all admin actions logged)
- [ ] Cross-tenant data isolation (tenant A never sees tenant B data)

### Final Cleanup
- [ ] Update `docker-compose.yml` if needed
- [ ] Update `.env.example` with all new environment variables
- [ ] Register all 15 new routers in `main.py`
- [ ] Run full test suite and fix failures
- [ ] Health check verification (`GET /health` reports all services healthy)

---

## Summary

| Phase | New Files | Focus |
|-------|-----------|-------|
| Phase 0 | ~16 | Roles, tenants, departments, system users |
| Phase 1 | ~24 | Visitor profiles, visit sessions, check-in/out, badges, OCR |
| Phase 2 | ~4 | Dashboard API, export, super admin endpoints |
| Phase 3 | ~14 | DSR, retention, deletion logs, sub-processors, compliance |
| Phase 4 | ~8 | Audit logs (immutable), incident logs |
| Phase 5 | ~2 | Seed data, indexes, integration tests, polish |
| **Total** | **~68 new files** | **14 existing files modified** |
