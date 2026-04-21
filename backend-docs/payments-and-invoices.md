# Payments & Invoices — Frontend Integration Guide

How to initiate payments, list/download invoices, and understand where invoice data comes from.

## Key Concept: Invoices Are Generated, Not Created

**The frontend never POSTs to create an invoice.** There is no `POST /v1/invoices`.

Invoices are produced server-side by the billing automation:

1. **Subscription creation** — `POST /v1/subscriptions` queues a subscription. When the writer commits, it generates the first invoice.
2. **Renewal** — `services/renewal_service.py` runs hourly; on successful charge it creates an invoice for the new period.
3. **Dunning recovery** — `services/dunning_service.py` retries failed `PAST_DUE` charges; on success it generates an invoice.
4. **Trial conversion** — when `trial_ends_at` passes and payment succeeds, an invoice is generated.

The frontend responsibilities are:

- Initiate payment via `POST /v1/payments/intents` (returns an authorization URL to redirect the user to).
- List/view invoices via `GET /v1/invoices/...`.
- Download PDFs via `GET /v1/invoices/{id}/pdf`.
- Optionally trigger refunds via `POST /v1/payments/{id}/refund`.

## Auth Matrix

| Endpoint | Role |
|----------|------|
| `POST /v1/payments/intents` | any authenticated token |
| `GET /v1/payments/{id}` | any authenticated token (owner or app admin) |
| `POST /v1/payments/{id}/refund` | any authenticated token (owner or app admin) |
| `GET /v1/payments/webhooks/events` | app admin only |
| `POST /v1/payments/webhooks/replay/{event_id}` | app admin only |
| `GET /v1/invoices/tenant/{tenant_id}` | `super_admin` |
| `GET /v1/invoices/admin` | app admin |
| `GET /v1/invoices/{id}` | `super_admin` |
| `GET /v1/invoices/{id}/pdf` | `super_admin` |

All responses use the standard envelope: `{ success, message, data, meta?, requestId? }`.

## Payment Endpoints

### `POST /v1/payments/intents` — Start a payment

Creates a payment transaction and returns an authorization URL. Redirect the user there (or mount the returned `client_secret` in Stripe Elements).

**Request body — `PaymentIntentIn`**

```ts
{
  amount_minor: number;   // integer > 0, in minor units (kobo, cents)
  currency: string;       // ISO 4217, length 3 (e.g. "NGN", "USD")
  reference: string;      // min 3 chars, your internal ref (e.g. invoice number)
  customer_email?: string;
  provider?: "stripe" | "flutterwave";   // omit to use tenant/global default
  metadata?: Record<string, any>;
}
```

**Response — `PaymentTransactionOut` (201)**

```ts
{
  id: string;
  owner_id: string;
  provider: "stripe" | "flutterwave";
  reference: string;
  status: "pending" | "completed" | "failed" | "refunded";
  amount_minor: number;
  currency: string;
  idempotency_key: string;
  response_payload: {
    authorization_url?: string;   // redirect here
    client_secret?: string;       // for Stripe Elements
    ...                           // provider-specific
  };
  created_at: number;             // unix seconds
  updated_at: number;
}
```

### `GET /v1/payments/{payment_id}` — Fetch payment

Returns the same `PaymentTransactionOut`. Poll this after redirecting the user back from the provider to confirm `status === "completed"` — but the authoritative source of truth is the provider webhook (the server updates the record when the webhook arrives; no action required from the frontend).

### `POST /v1/payments/{payment_id}/refund` — Refund

**Body — `RefundIn`**

```ts
{ amount_minor?: number }   // omit for full refund; must be > 0 if provided
```

**Response:** updated `PaymentTransactionOut` with `status: "refunded"`.

### `GET /v1/payments/webhooks/events` (admin) — Webhook audit

Query params: `provider`, `processing_status`, `start` (default 0), `stop` (default 50, max 200). Returns `{ data: WebhookEvent[], meta: { total, start, stop } }`. Use the admin billing dashboard to surface failed webhooks.

### `POST /v1/payments/webhooks/replay/{event_id}` (admin) — Replay

Re-runs a stored webhook through the normal handler. Response: `{ replayed_event_id, provider, result }`.

## Invoice Endpoints

### `GET /v1/invoices/tenant/{tenant_id}` — Tenant invoices (super_admin)

Query: `start` (default 0), `stop` (default 20, max 100).

First page (`start=0&stop=20`) is served from the per-tenant precompute cache (60s TTL) — fast, may be slightly stale.

**Response**

```json
{
  "success": true,
  "message": "Tenant invoices retrieved",
  "data": [InvoiceWithSummaryOut, ...],
  "meta": { "total": 42, "start": 0, "stop": 20 }
}
```

An empty list looks exactly like the sample you showed: `"data": []`, `"meta.total": 0`. That just means the tenant has no invoices yet — no subscriptions have renewed.

### `GET /v1/invoices/admin` — All invoices (app admin)

Query: `start`, `stop`, `tenant_id?`, `status?` (filters: `draft | issued | paid | void | refunded`).

Unfiltered first page is served from a global precompute cache (120s TTL).

### `GET /v1/invoices/{invoice_id}` — Invoice detail (super_admin)

Returns `InvoiceWithSummaryOut` — same as list item plus `tenant_summary` and `subscription_summary`.

### `GET /v1/invoices/{invoice_id}/pdf` — PDF download URL (super_admin)

**Response**

```json
{ "success": true, "message": "Invoice PDF URL generated", "data": { "pdf_url": "https://..." } }
```

`pdf_url` is a short-lived presigned S3/local URL. Don't cache it — request a fresh one each time the user clicks Download. Returns 404 if the invoice has no PDF yet (e.g. storage misconfigured or invoice is still `DRAFT`).

## Schemas

### `InvoiceOut`

```ts
{
  id: string;
  tenant_id: string;
  subscription_id: string;
  invoice_number: string;              // "INV-2026-000042"
  status: "draft" | "issued" | "paid" | "void" | "refunded";
  billing_cycle: "monthly" | "yearly";
  currency: string;                    // default "NGN"
  subtotal_minor: number;              // before discounts
  discount_total_minor: number;
  tax_minor: number;
  total_minor: number;                 // final amount charged
  line_items: InvoiceLineItem[];
  payment_transaction_id?: string;
  issued_at?: number;                  // unix seconds
  paid_at?: number;
  period_start: number;
  period_end: number;
  pdf_object_key?: string;             // internal; use /pdf endpoint instead
  pdf_url?: string;                    // resolved presigned URL on single fetch
  provider?: "stripe" | "flutterwave";
  date_created?: number;
  last_updated?: number;
}
```

### `InvoiceLineItem`

```ts
{
  description: string;                 // "Professional Plan - Monthly", "Discount: SAVE20"
  quantity: number;                    // default 1
  unit_price_minor: number;            // may be negative for discount lines
  total_minor: number;
  metadata?: { type?: "plan" | "discount"; billing_cycle?: string };
}
```

### `InvoiceWithSummaryOut`

Extends `InvoiceOut` with:

```ts
{
  tenant_summary?: { id, company_name?, is_active?, country_of_hosting? };
  subscription_summary?: { id, status?, billing_cycle?, plan_id?, current_period_end? };
}
```

## Display Tips

- **Formatting amounts:** values are in minor units. Divide by 100 for most currencies (NGN, USD, EUR). JPY is already major units but the system still uses `_minor` field naming.
- **Status badges:** map `draft`→grey, `issued`→blue, `paid`→green, `void`→grey, `refunded`→amber.
- **Payment status polling:** after redirect-back from the provider, hit `GET /v1/payments/{id}` once. If still `pending`, show "Processing…" and rely on webhooks — don't poll aggressively.
- **Empty states:** a tenant with no subscription history will return `{ data: [], meta.total: 0 }`. Show "No invoices yet" rather than an error.
- **New invoices appearing:** cache TTL is 60s (tenant) or 120s (global admin), so a brand-new invoice may not appear immediately on the first page after a renewal. For a forced fresh read, paginate past the first page or call the single-invoice detail endpoint.

## Error Codes

| HTTP | `code` | When |
|------|--------|------|
| 401 | `AUTH_INVALID_TOKEN` | Missing/invalid token |
| 403 | `AUTH_PERMISSION_DENIED` | Wrong role or cross-tenant access |
| 404 | `RESOURCE_NOT_FOUND` | Invoice or payment id unknown; PDF not yet generated |
| 409 | `RESOURCE_CONFLICT` | Duplicate payment reference; refund on non-refundable state |
| 422 | `VALIDATION_FAILED` | Bad amount, currency, or body shape |

---

# Admin View: Tracking Tenant Payments

How an **application admin** answers "has tenant X paid? When? For what period? With which provider?"

The system does **not** expose a "list payments by tenant" endpoint. Instead, the invoice is the canonical record of a completed payment — every successful charge produces exactly one invoice, and every invoice links back to its `payment_transaction_id`. Walk the chain: **Subscription → Invoice → PaymentTransaction → WebhookEvent**.

## The Four Sources of Truth

| Question | Endpoint | What to read |
|----------|----------|--------------|
| Is the tenant currently paid up? | `GET /v1/subscriptions?tenantId={id}` | `status`, `current_period_end`, `renewal_attempts` |
| What has the tenant been charged? | `GET /v1/invoices/admin?tenant_id={id}` | `status`, `total_minor`, `paid_at`, `period_start/end` |
| Did a specific charge succeed at the provider? | `GET /v1/payments/{payment_id}` | `status`, `response_payload` |
| Did the provider webhook arrive and process cleanly? | `GET /v1/payments/webhooks/events?provider=&processing_status=` | `processing_status`, `raw_payload` |

## 1. Subscription status — "Are they paid up right now?"

### `GET /v1/subscriptions?tenantId={tenant_id}`

Returns `SubscriptionWithDetailsOut[]` — the subscription record plus tenant and plan snapshots.

**Fields that tell you payment state:**

| Field | Meaning |
|-------|---------|
| `status` | `active` → paid, valid. `trialing` → no charge yet. `past_due` → last charge failed, dunning in progress. `suspended` → dunning exhausted. `cancelled` / `expired` → no longer billing. |
| `current_period_start` / `current_period_end` | Unix seconds. If `current_period_end > now()` and `status === "active"`, they're covered. |
| `renewal_attempts` | `0` on a clean period. `>0` means at least one charge failure has been retried. |
| `last_renewal_attempt_at` | Timestamp of most recent charge attempt (success or failure). |
| `next_retry_at` | Set when in dunning — the next scheduled retry. |
| `payment_method_id` | Stored card/token reference at the provider. |
| `tenant.default_payment_provider` | `"stripe"` or `"flutterwave"`. |
| `tenant.stripe_customer_id` / `tenant.flutterwave_customer_id` | Provider-side customer references. |

**Quick decision tree:**

- `status=active` + `current_period_end > now` + `renewal_attempts=0` → **paid, healthy**.
- `status=active` + `renewal_attempts > 0` → paid, but recovered from a dunning cycle.
- `status=past_due` → **last charge failed**; check `next_retry_at` and the invoice list for a `void` invoice.
- `status=suspended` → dunning exhausted (`MAX_DUNNING_ATTEMPTS` hit); access is gated.
- `status=trialing` + `trial_ends_at` in the future → **no money collected yet**.

## 2. Invoice history — "Show me every time they paid"

### `GET /v1/invoices/admin?tenant_id={tenant_id}&status=paid&start=0&stop=50`

Filter by `tenant_id` + `status=paid` to get the tenant's complete successful-payment history. Each `InvoiceWithSummaryOut` includes:

| Field | Meaning |
|-------|---------|
| `invoice_number` | `INV-2026-000042` — human-readable reference. |
| `status` | `paid` (succeeded), `issued` (awaiting payment), `void` (generation failed / voided), `refunded`. |
| `total_minor` + `currency` | Amount charged (in minor units). |
| `paid_at` | Unix seconds — **this is when the tenant paid**. |
| `issued_at` | When the invoice was created (usually identical to `paid_at` in this system — invoices are generated on successful charge). |
| `period_start` / `period_end` | Which billing period this payment covers. |
| `payment_transaction_id` | FK to the payment record — use this to drill in. |
| `provider` | `"stripe"` or `"flutterwave"` — which processor handled it. |
| `line_items` | Plan charge + any discount lines (negative amounts). |
| `subscription_summary` / `tenant_summary` | Denormalized context for display. |

**To answer "did tenant X pay for period Y?":** filter invoices by `tenant_id` + `status=paid`, then find one whose `[period_start, period_end]` contains Y.

## 3. Payment transaction — "What did the provider actually say?"

### `GET /v1/payments/{payment_transaction_id}`

Returns the raw payment record. Use the `payment_transaction_id` from the invoice.

Key fields:

- `status`: `"completed"` confirms the provider finalized the charge. `"pending"` means the intent was created but no webhook has arrived yet. `"failed"` / `"refunded"` are self-explanatory.
- `provider`: which processor.
- `amount_minor` + `currency`: what was charged.
- `reference`: your internal reference (usually the invoice number suffix).
- `idempotency_key`: used to deduplicate retries.
- `response_payload`: the provider's raw response — contains `charge_id`, `receipt_url` (Stripe), Flutterwave transaction ID, etc. Surface `receipt_url` as "View receipt" when available.
- `created_at` / `updated_at`: intent creation vs. last status change (webhook update).

Admins can hit this endpoint for any payment (the owner check is bypassed for `role=admin`).

## 4. Webhook audit — "Why is the state weird?"

### `GET /v1/payments/webhooks/events?provider={stripe|flutterwave}&processing_status={pending|processed|failed}`

Lists every inbound webhook the system received. Use this when an invoice looks stuck — e.g. subscription shows `past_due` but the tenant insists they paid.

Response items (`WebhookEventOut`):

| Field | Meaning |
|-------|---------|
| `provider` | `"stripe"` / `"flutterwave"` |
| `event_id` | Provider-side event id |
| `event_type` | e.g. `charge.completed`, `charge.failed`, `subscription.cancelled` |
| `processing_status` | `processed` → applied cleanly. `failed` → handler raised. `pending` → queued but not finished. |
| `payload_hash` | Dedup key |
| `raw_payload` | Full provider body — inspect this to see what the provider reported |
| `date_created` | When the webhook landed |

Query params: `provider`, `processing_status`, `start` (default 0), `stop` (default 50, max 200).

### `POST /v1/payments/webhooks/replay/{event_id}`

Re-runs a stored webhook through the normal handler. Use when a handler bug caused `processing_status=failed` and you've since shipped a fix. Response: `{ replayed_event_id, provider, result }`.

## 5. Aggregate billing — "Platform-wide revenue & churn"

### `GET /v1/admins/dashboard/billing?start_date={unix}&end_date={unix}`

Omit both dates for the default cached "last 30 days" view (5-min TTL). Custom ranges bypass the cache.

Response:

```ts
{
  period: { start: number; end: number };
  total_revenue_minor: number;       // sum of paid invoices in range
  invoice_count: number;
  new_subscriptions: number;
  cancelled_subscriptions: number;
  active_subscriptions: number;
  mrr_minor: number;                 // monthly recurring revenue = sum(active monthly prices) + sum(active yearly prices)/12
  generated_at: number;
}
```

### `GET /v1/admins/dashboard/billing/discrepancies`

Reconciliation check. Returns a list of issues:

```ts
{
  type: "missing_invoice" | "orphaned_payment";
  subscription_id: string | null;
  payment_id: string | null;
  tenant_id: string;
  issue_description: string;
  detected_at: number;
}[]
```

- `missing_invoice` → active subscription has no invoice in its current period (billing job failed silently).
- `orphaned_payment` → successful payment with no matching invoice (webhook race or manual charge).

### `GET /v1/admins/dashboard/stats`

Platform-wide headline numbers — includes `subscription_breakdown` (by status), `plan_distribution` (subscribers per plan), `total_monthly_revenue`, `total_yearly_revenue`, `recent_signups_30d`. Cached globally for 120s.

## Recommended Admin UI Flow

**On a tenant detail page:**

1. Call `GET /v1/subscriptions?tenantId={id}` — show current status badge + period end + "next charge in N days".
2. Call `GET /v1/invoices/admin?tenant_id={id}&stop=20` — render a table of invoices with columns: Number · Period · Amount · Status · Paid At · Provider · [View PDF].
3. On invoice row click → `GET /v1/invoices/{id}` for full detail (line items, subscription/tenant summaries).
4. "View receipt" button → `GET /v1/payments/{payment_transaction_id}` → open `response_payload.receipt_url` in a new tab.
5. Troubleshooting panel (only if `subscription.status === "past_due"` or there's a recent `void` invoice): `GET /v1/payments/webhooks/events?provider={provider}` filtered to last 7 days, show `failed` events first with a "Replay" button wired to `POST /v1/payments/webhooks/replay/{event_id}`.

**On the platform overview page:**

- Headline tiles from `GET /v1/admins/dashboard/stats` (revenue, active subs, signups).
- MRR/churn chart from `GET /v1/admins/dashboard/billing`.
- Health banner driven by `GET /v1/admins/dashboard/billing/discrepancies` — if the list is non-empty, show a yellow banner linking to a reconciliation page.
