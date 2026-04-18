# Subscription checkout — frontend integration guide

Everything a frontend needs to take a tenant super_admin from "picks a plan"
to "subscription is active" using the provider-agnostic checkout API.

> This flow is only for **tenant super_admins** paying for their own tenant.
> Application-admin-driven subscription creation (`POST /v1/subscriptions`)
> is unchanged and is not covered here.

## TL;DR

1. Tenant super_admin is logged in (access token has `role=super_admin`).
2. User picks a plan → you `POST /v1/checkout/sessions`.
3. Response has `checkout_url`. Redirect (or open in a new tab).
4. User completes payment on that URL (real provider page OR the built-in app-mode simulator).
5. The backend activates the subscription on success (webhook for Stripe/Flutterwave, in-page POST for app mode).
6. You poll `GET /v1/checkout/sessions/{id}` or re-fetch the tenant's subscription to detect completion.

You do **not** need to know which provider was used — the server picks and the response tells you.

## Required auth

All `/v1/checkout/*` endpoints require a super_admin access token:

```
Authorization: Bearer <super_admin_access_token>
```

The tenant is inferred from the token; do not pass `tenant_id` in the body or URL.

## 1. Create a checkout session

### Request

```http
POST /v1/checkout/sessions
Content-Type: application/json
Authorization: Bearer <token>

{
  "plan_id": "65abc...d1",
  "billing_cycle": "monthly",            // "monthly" | "yearly"
  "discount_ids": ["65abc...e2"],         // optional; can be empty
  "preferred_provider": "stripe",          // optional; "stripe" | "flutterwave" | "app"
  "trial_days": 0,                         // optional
  "metadata": { "source": "upgrade-flow" } // optional free-form
}
```

### Response (201)

```json
{
  "success": true,
  "message": "Checkout session created",
  "data": {
    "id": "64f...aa",
    "tenant_id": "64f...t1",
    "plan_id": "65abc...d1",
    "billing_cycle": "monthly",
    "currency": "NGN",
    "amount_minor": 950000,
    "provider": "app",
    "status": "pending",
    "checkout_url": "/payments/app-checkout/chk_9f2c...",
    "provider_reference": "chk_9f2c...",
    "provider_payload": { "mode": "app_test" },
    "breakdown": {
      "base_price": 10000.0,
      "billing_cycle": "monthly",
      "currency": "NGN",
      "applied_discount_ids": ["65abc...e2"],
      "total_percentage_off": 5.0,
      "total_fixed_off": 0.0,
      "final_price": 9500.0,
      "amount_minor": 950000
    },
    "applied_discount_ids": ["65abc...e2"],
    "created_by_user_id": "64f...u1",
    "expires_at": 1776541200,
    "completed_at": null,
    "subscription_id": null,
    "failure_reason": null,
    "metadata": { "source": "upgrade-flow" },
    "trial_days": 0,
    "date_created": 1776454800,
    "last_updated": 1776454800
  }
}
```

### Things to know

* `checkout_url` may be **relative** (e.g. `/payments/app-checkout/...`) when the provider is `app` and `APP_BASE_URL` isn't configured. Prefix with the API origin if you need an absolute URL.
* `preferred_provider` is a hint. If it isn't configured or fails, the server silently falls back and `data.provider` tells you what actually got used.
* `amount_minor` is the amount in minor units (kobo for NGN, cents for USD). Multiply `breakdown.final_price` × 100 for display sanity-checks.
* `breakdown.total_percentage_off` is capped at 100 and is applied before `total_fixed_off`.

### Common 4xx responses

| Status | `code`                | Reason                                            |
| ------ | --------------------- | ------------------------------------------------- |
| 400    | `VALIDATION_FAILED`   | `plan_id` malformed, plan not active, bad body.   |
| 402    | `SUBSCRIPTION_INACTIVE` | Tenant subscription blocked — but this endpoint is exempt, so you should not see this here. |
| 403    | `AUTH_PERMISSION_DENIED` | Caller isn't a super_admin (or token has no `tenant_id`). |
| 404    | `RESOURCE_NOT_FOUND`  | `plan_id` doesn't exist.                          |
| 502    | `PAYMENT_PROVIDER_ERROR` | Every configured provider failed. Retry later.  |

## 2. Redirect to the checkout URL

```js
// React-ish pseudocode
const { data } = await api.post('/v1/checkout/sessions', body);
window.location.assign(data.checkout_url);
```

For the app-mode URL, the page is served by the same backend origin and shows:

* plan name and billing cycle
* base price, discounts applied, total due
* two buttons — **Mark as paid** / **Mark as failed**

The page is Tailwind-via-CDN and works in any browser. It POSTs back to
`/payments/app-checkout/{ref}/complete` itself — you don't implement that.

## 3. Detect completion

The subscription is provisioned on the backend as soon as:

* Stripe / Flutterwave fire their success webhook, or
* the app-mode page posts `outcome=success`.

There is no redirect-on-success URL yet, so the frontend should poll.

### Option A — poll the checkout session

```http
GET /v1/checkout/sessions/{id}
Authorization: Bearer <token>
```

`data.status` transitions through `pending → succeeded | failed | expired | cancelled`.

On `succeeded`, `data.subscription_id` contains the id of the newly-provisioned subscription.

### Option B — poll the tenant subscription

```http
GET /v1/subscriptions/tenant/{tenant_id}/active
```

Returns the active subscription once it exists. (Endpoint pre-dates this feature; shape per `subscription_route`.)

### Recommended polling cadence

* Poll every 2s for the first 30s after the user returns from the checkout URL.
* Then back off to every 10s for another 2 minutes.
* Give up after 5 minutes — show "Still processing. We'll email you."

## 4. History view

```http
GET /v1/checkout/sessions?status=pending&skip=0&limit=50
Authorization: Bearer <token>
```

Statuses: `pending`, `succeeded`, `failed`, `expired`, `cancelled`.
Response includes `meta: { total, skip, limit }`. Results are newest-first.

Typical UI columns: created_at, plan, amount, provider, status, subscription_id.

## 5. Cancel a pending session

```http
POST /v1/checkout/sessions/{id}/cancel
Authorization: Bearer <token>
```

Only works while `status=pending`. Returns the updated session (status=`cancelled`). Does not refund.

## 6. Mode detection (stripe / flutterwave / app)

Everything is agnostic, but if you want to show a provider-specific UI:

| `data.provider` | What the checkout URL is                                         |
| --------------- | ---------------------------------------------------------------- |
| `stripe`        | Stripe PaymentIntent — `provider_payload.client_secret` is in the response. Use `@stripe/stripe-js` with that client_secret on your own hosted checkout form; OR redirect the user to a Stripe-hosted URL if you switch the provider to Checkout Sessions later. |
| `flutterwave`   | `checkout_url` is a Flutterwave-hosted link. Just redirect.      |
| `app`           | `checkout_url` is served by this backend. Just redirect.         |

You can treat all three the same — redirect to `checkout_url` — and it'll work today because Stripe's current provider returns a `null` `checkout_url` only if you explicitly choose Elements. If you need full Stripe redirect support, switch the provider implementation to Stripe Checkout Sessions (it's a drop-in change, signature stays).

## 7. Trial checkouts

Passing `trial_days > 0` creates a normal checkout session whose success path
flips the subscription to `TRIALING` instead of `ACTIVE`. The amount charged is
still the plan's monthly/yearly price — treat trial billing as a separate
feature if you need "0 up-front, charge after N days".

## 8. Currency and formatting

* `breakdown.currency` is always the plan's native currency (e.g. `NGN`).
* Don't hard-code the symbol — read it from the breakdown.
* `amount_minor / 100` for display; keep minor units on the wire.

## 9. Errors you should surface nicely

| Backend shape                                             | Frontend message                                           |
| --------------------------------------------------------- | ---------------------------------------------------------- |
| `code: PAYMENT_PROVIDER_ERROR`                            | "Couldn't reach our payment processor. Please try again."  |
| `code: RESOURCE_NOT_FOUND` with `resource: "Plan"`        | "That plan no longer exists."                              |
| `code: VALIDATION_FAILED` (plan not active)               | "That plan is not currently available."                    |
| 409 on session completion (already active subscription)   | "You already have an active subscription."                 |

All errors use the standard envelope `{ success: false, message, data: { code, details } }`.

## 10. Webhooks (informational — nothing to do on the frontend)

Stripe → `POST /v1/payments/webhooks/stripe`
Flutterwave → `POST /v1/payments/webhooks/flutterwave`

Both are wired so a successful payment flips the matching checkout session to
`succeeded` and calls `subscribe_tenant` internally. The frontend doesn't
touch these.

## 11. Feature toggle path

If a tenant super_admin hits the checkout flow but the backend has neither
Stripe nor Flutterwave keys set, they will automatically land on the app-mode
simulator page. That page exists so you can exercise the full flow in dev
without fake cards. In production, set at least one of `STRIPE_SECRET_KEY` or
`FLUTTERWAVE_SECRET_KEY` and the simulator will stop being selected as the
default provider — but remains available via `preferred_provider: "app"` if
you ever want to force test mode.

## 12. Minimum viable frontend checklist

- [ ] Plans page fetches `GET /v1/plans?status=active&public_only=true`.
- [ ] "Subscribe" CTA → `POST /v1/checkout/sessions` → redirect to `data.checkout_url`.
- [ ] Return / success URL on your side polls `GET /v1/checkout/sessions/{id}` until terminal.
- [ ] On `succeeded` → navigate to billing overview and refetch active subscription.
- [ ] Billing-history page → `GET /v1/checkout/sessions?status=...` with filter pills.
- [ ] Cancel button on pending rows → `POST /v1/checkout/sessions/{id}/cancel`.
- [ ] Nice error messages for the 4 codes in §9.
