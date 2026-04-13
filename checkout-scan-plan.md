# Plan: QR-Scan Auto-Checkout Endpoint

## Current state

- Badge QR encodes an HMAC-signed token (`sign_badge_token(session_id, 24h)`) at [services/visit_session_service.py:287](services/visit_session_service.py#L287).
- Existing checkout: `POST /v1/visitors/check-out` with body `{ badge_qr_token, check_out_method }` at [api/v1/visitor_route.py:147](api/v1/visitor_route.py#L147). Requires auth (receptionist/dept_admin/super_admin).
- Service `check_out_visitor` already verifies the token and transitions status to CHECKED_OUT at [services/visit_session_service.py:380](services/visit_session_service.py#L380).

So the plumbing exists — the gap is ergonomics for a scanner UX. Two options:

## Option A — Thin wrapper for scanners (recommended)

A single-purpose endpoint that takes only the token. No body shape to remember, no `check_out_method` default confusion.

- **Endpoint**: `POST /v1/visitors/scan-checkout`
- **Auth**: `receptionist | dept_admin | super_admin` (same as `check-out`)
- **Body**: `{ token: str }`
- **Behavior**: delegates to `check_out_visitor(CheckOutRequest(badge_qr_token=token, check_out_method=QR_SCAN), tenant_id)`.
- **Response**: same `VisitSessionOut` as `/check-out`.
- **Errors**: 400 invalid/expired token, 404 session not found, 400 already checked out.

Why a new endpoint rather than "just call /check-out": the scanner client posts exactly what the camera decoded, nothing else. Keeps scanner code tiny and makes the audit trail clearer (`check_out_method` is always `qr_scan`).

### Code changes

1. [schemas/visit_session_schema.py](schemas/visit_session_schema.py) — add:
   ```python
   class ScanCheckoutRequest(BaseModel):
       token: str
   ```
2. [api/v1/visitor_route.py](api/v1/visitor_route.py) — add `POST /scan-checkout` handler that builds a `CheckOutRequest(badge_qr_token=request.token, check_out_method=CheckOutMethod.QR_SCAN)` and calls `check_out_visitor`.
3. No service-layer changes needed — reuse `check_out_visitor`.

## Option B — GET endpoint for URL-embedded QR codes

Only relevant if the QR encodes a full URL (e.g. `https://app/checkout?t=...`) rather than a raw token. Given current code signs a raw token, Option A fits better. If you later want "scan from any camera → deep link", add:

- `GET /v1/visitors/scan-checkout?token=...` — same logic, but GET. Downside: GETs shouldn't mutate state; prefer a redirect-to-POST page or keep it POST-only and have the scanner app handle the network call.

## Recommendation

Go with **Option A**. ~20 lines across schema + route, zero service churn, scanner clients post `{ token }` and get the updated session back. Reject Option B unless there's a specific need to checkout directly from a camera app that can only open URLs.

## Out of scope / follow-ups

- Rate limiting on token reuse (a valid token can be posted repeatedly until TTL; current check `status != CHECKED_IN` already prevents double-checkout, so this is fine).
- Audit event tagging the checkout as scanner-initiated vs manual — `check_out_method=QR_SCAN` already records this.
