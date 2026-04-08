# VisiChek Backend

A FastAPI backend for visitor management, built with the FasterAPI framework.

## Architecture

Four-layer architecture: **Route** -> **Service** -> **Repository** -> **Database**

Two parallel user systems:

- **Application Admins** (`/v1/admins/`) — Platform operators who manage tenants, plans, subscriptions, and discounts
- **Tenant Users** (`/v1/system-users/`) — Users within a tenant with 6 roles: `super_admin`, `dept_admin`, `receptionist`, `auditor`, `security_officer`, `dpo`

## Tenant And System User Creation

Tenant creation happens in two ways:

- `POST /v1/tenants/` lets an application admin create only the tenant record
- `POST /v1/admins/tenants/bootstrap` creates the tenant and its first tenant user (`super_admin`) together; if creating the `super_admin` fails, the tenant is rolled back

After a tenant has a `super_admin`, that user can create the rest of the tenant's system users through `/v1/system-users/signup`. System users always belong to a specific `tenant_id`, and permissions are assigned automatically from their role.

## Tech Stack

- **Framework**: FastAPI + FasterAPI CLI scaffolding
- **Database**: MongoDB (Motor async) or SQLite (`DB_TYPE` env var)
- **Cache / Queue**: Redis + Celery
- **Auth**: JWT bearer tokens, role-based permissions, password policy enforcement
- **Payments**: Stripe / Flutterwave (pluggable)
- **Storage**: S3 / local filesystem (pluggable)
- **Email**: SMTP (pluggable, queueable)

## Getting Started

1. Copy `.env.example` to `.env` and configure:
   - `SECRET_KEY`, `SESSION_SECRET_KEY`
   - `MONGO_URL`, `DB_NAME`, `DB_TYPE`
   - `REDIS_URL` (or `REDIS_HOST` + `REDIS_PORT`)
   - `STORAGE_BACKEND` (`local` or `s3`)
   - `PAYMENT_DEFAULT_PROVIDER` (`flutterwave` or `stripe`)

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Start the development server:
   ```bash
   fasterapi run-d
   ```

4. Open Swagger UI at `http://localhost:8000/docs`

## Key API Groups

| Tag | Prefix | Auth | Purpose |
|-----|--------|------|---------|
| Application Admins | `/v1/admins/` | Application admin token | Platform management, tenant bootstrap |
| Application Admin Dashboard | `/v1/admins/dashboard/` | Application admin token | Platform-wide stats |
| Tenants | `/v1/tenants/` | Application admin token | Tenant CRUD |
| Plans | `/v1/plans/` | Application admin token | Subscription plan CRUD |
| Subscriptions | `/v1/subscriptions/` | Application admin token | Tenant subscription management |
| Discounts | `/v1/discounts/` | Application admin token | Discount code management |
| Tenant Users | `/v1/system-users/` | Tenant user token | Tenant user CRUD + login |
| Tenant Super Admin | `/v1/super-admin/` | super_admin token | Tenant user management |
| Branches | `/v1/branches/` | super_admin token | Tenant branch management |
| Tenant Dashboard | `/v1/dashboard/` | dept_admin / super_admin | Tenant-scoped visitor stats |
| Visitors | `/v1/visitors/` | Tenant user token | Visitor registration and check-in |
| Appointments | `/v1/appointments/` | Tenant user token | Appointment scheduling |

## Security Features

- **Password strength validation**: Min 8 chars, uppercase, lowercase, digit, special char required
- **Common password blocking**: Top 250+ breached passwords are rejected
- **Account lockout**: 5 failed login attempts triggers a 15-minute lockout
- **Password history**: Last 5 passwords cannot be reused
- **JWT bearer tokens** with role-based access control
- **Per-role rate limiting** (configurable via `ROLE_RATE_LIMITS` env var)
- **Plan enforcement middleware**: Feature gating + CRUD/retrieval quotas per tenant subscription

## Subscription & Plan System

Tenants subscribe to plans that control feature access, operation limits, storage caps, and entity limits. Application admins manage plans, subscriptions, and discounts.

- Plans can be `draft`, `active`, or `archived`
- Archived plans are hidden from public listings but existing subscribers continue until expiry
- Discounts support percentage/fixed amounts, global/tenant/plan scoping, and stackability
- Usage is tracked via pre-aggregated counters for fast quota checking

## FasterAPI CLI

```bash
fasterapi make-schema <name>     # Generate Pydantic schemas
fasterapi make-crud <name>       # Generate repository functions
fasterapi make-service <name>    # Generate service layer
fasterapi make-route <name>      # Generate route handlers
fasterapi mount                  # Register all routes in main.py
fasterapi run-d                  # Start dev server
```

## Testing

```bash
pytest                            # All tests
pytest tests/unit/                # Unit tests only
pytest tests/integration/         # Integration tests (needs MongoDB + Redis)
```

## Queue & Background Tasks

```python
from core.queue.manager import QueueManager

QueueManager.get_instance().enqueue("delete_tokens", {"userId": user_id})
```

## Response Format

All responses use a standard envelope:

```json
{
  "success": true,
  "message": "Description",
  "data": { ... },
  "requestId": "uuid"
}
```

Use `@document_response(message=..., ...)` decorator on every endpoint.
