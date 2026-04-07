# Flutterwave Integration Quick Start

## Configuration

Set these environment variables:
```bash
FLUTTERWAVE_SECRET_KEY=sk_live_xxxxx  # Flutterwave secret key
FLUTTERWAVE_WEBHOOK_SECRET_HASH=hash_xxxx  # Webhook secret hash from Flutterwave dashboard
PAYMENT_DEFAULT_PROVIDER=flutterwave  # Optional: set Flutterwave as default
```

## Webhook Setup

Configure your Flutterwave dashboard to send webhooks to:
```
https://your-domain.com/v1/payments/webhooks/flutterwave
```

Supported events:
- `charge.completed` - Payment succeeded
- `charge.failed` - Payment failed
- `transfer.completed` - Payout completed
- `subscription.cancelled` - Subscription cancelled

## Usage in Code

### 1. Create or Get Customer Token
```python
from services.flutterwave_customer_service import create_or_get_flutterwave_customer

customer_id = await create_or_get_flutterwave_customer(
    tenant_id="507f1f77bcf86cd799439011",
    email="customer@example.com",
    name="John Doe"
)
# Returns: "fw_customer_example_com_1712520000"
```

### 2. Create Payment Intent
```python
from core.payments import PaymentIntentRequest, PaymentManager

payment_manager = PaymentManager.get_instance()
provider = payment_manager.get_provider("flutterwave")

intent = provider.create_intent(
    PaymentIntentRequest(
        amount_minor=5000,  # USD 50.00
        currency="USD",
        reference="inv-2026-001",
        customer_email="customer@example.com",
        metadata={
            "subscription_id": "sub-123",
            "tenant_id": "tenant-456"
        }
    )
)

# Use intent.checkout_url to redirect customer to payment page
print(intent.checkout_url)
```

### 3. Fetch Payment Status
```python
provider = payment_manager.get_provider("flutterwave")
transaction = provider.fetch_transaction(reference="inv-2026-001")
print(transaction.status)  # "succeeded", "pending", "failed"
```

### 4. Process Refund
```python
provider = payment_manager.get_provider("flutterwave")
refund = provider.refund(
    reference="inv-2026-001",
    amount_minor=2500  # Optional: partial refund
)
print(refund.status)  # "refunded"
```

## Automatic Renewal Flow

The renewal service automatically uses Flutterwave if:
1. Tenant's `default_payment_provider` is set to "flutterwave"
2. Tenant has a `flutterwave_customer_id` stored

```python
# In renewal_service.py (automatic)
# _get_provider_for_tenant() selects the correct provider
provider_name = await _get_provider_for_tenant(subscription.tenant_id)
provider = payment_manager.get_provider(provider_name)
```

## Webhook Event Handling

Webhooks are automatically processed and recorded:

```python
# This happens automatically via POST /v1/payments/webhooks/flutterwave
# 1. Signature verified
# 2. Event parsed (charge.completed, charge.failed, etc.)
# 3. Payment/subscription status updated
# 4. Event recorded for audit trail
```

**View recorded webhook events:**
```
GET /v1/payments/webhooks/events?provider=flutterwave
```

**Replay a webhook (admin only):**
```
POST /v1/payments/webhooks/replay/{event_id}
```

## Debugging

### Check if customer token created
```python
from services.flutterwave_customer_service import get_flutterwave_customer

customer_id = await get_flutterwave_customer(tenant_id="...")
if customer_id:
    print(f"Customer ID: {customer_id}")
else:
    print("No customer token found")
```

### View webhook events
```bash
# List all Flutterwave webhooks
curl -H "Authorization: Bearer {token}" \
  "http://localhost:8000/v1/payments/webhooks/events?provider=flutterwave"

# Filter by status
curl -H "Authorization: Bearer {token}" \
  "http://localhost:8000/v1/payments/webhooks/events?provider=flutterwave&processing_status=failed"
```

### Check logs
```bash
# Grep for Flutterwave webhook processing
docker logs visichek-backend | grep "Flutterwave webhook"

# See payment provider selection
docker logs visichek-backend | grep "Using provider"

# View renewal attempts
docker logs visichek-backend | grep "Attempting renewal"
```

## Testing Webhooks Locally

### 1. Start local tunnel (ngrok)
```bash
ngrok http 8000
# Copy the URL: https://xxxx-xx-xxx-xxx.ngrok.io
```

### 2. Set Flutterwave test webhook URL
In Flutterwave dashboard: `https://xxxx-xx-xxx-xxx.ngrok.io/v1/payments/webhooks/flutterwave`

### 3. Trigger test webhook
Use Flutterwave dashboard's "Test Webhook" feature or use curl:
```bash
curl -X POST http://localhost:8000/v1/payments/webhooks/flutterwave \
  -H "Content-Type: application/json" \
  -H "Verif-Hash: your-test-hash" \
  -d '{
    "data": {
      "id": 123456,
      "tx_ref": "inv-2026-001",
      "status": "successful"
    },
    "event": "charge.completed"
  }'
```

## Common Issues

### Issue: "Flutterwave is not configured"
**Solution:** Ensure `FLUTTERWAVE_SECRET_KEY` is set in environment

### Issue: "Invalid webhook signature"
**Solution:** Verify `FLUTTERWAVE_WEBHOOK_SECRET_HASH` matches your Flutterwave dashboard settings

### Issue: Payment created but renewal failed
**Solution:** Check logs for webhook processing errors
```bash
docker logs visichek-backend | grep -i "flutterwave\|renewal"
```

### Issue: Webhook already processed (409)
**Solution:** This is expected for duplicate webhooks - Flutterwave may retry webhooks

## Provider Failover

If Flutterwave is unavailable, the renewal service automatically falls back:

```python
# If tenant.flutterwave_customer_id is not set,
# renewal uses the default provider (usually Stripe)
```

To switch providers for a tenant:
```python
from repositories.tenant_repo import update_tenant
from schemas.tenant_schema import TenantUpdate

update = TenantUpdate(
    default_payment_provider="stripe",  # Switch from Flutterwave to Stripe
    flutterwave_customer_id=None
)
await update_tenant({"_id": ObjectId(tenant_id)}, update)
```

## Monitoring

Key metrics to monitor:

1. **Webhook Processing:** Check `webhook_events` collection for `processing_status="failed"`
2. **Renewal Success:** Track `renewal_count` vs `failed_count` in renewal logs
3. **Customer Creation:** Monitor `flutterwave_customer_service` logs for creation failures
4. **Provider Selection:** Log lines showing which provider was selected per tenant

## API Reference

### Services

**flutterwave_webhook_service:**
- `process_flutterwave_webhook(body, headers)` → dict

**flutterwave_customer_service:**
- `create_or_get_flutterwave_customer(tenant_id, email, name)` → str (customer_id)
- `get_flutterwave_customer(tenant_id)` → Optional[str]

**payment_service:**
- `process_webhook(provider_name, body, headers)` → dict (routes to Flutterwave handler)

**renewal_service:**
- `_get_provider_for_tenant(tenant_id)` → str (internal helper)
- `renew_due_subscriptions()` → dict (uses provider-aware renewal)

### Repositories

**webhook_event_repo:**
- `create_webhook_event(event_data)` - Record webhook for audit trail
- `is_webhook_processed(event_id, provider)` - Check for duplicates
- `mark_webhook_processed(event_id, provider, status, error_message, duration_ms)` - Mark as processed
- `get_webhook_events(filter_dict, skip, limit)` - Query recorded webhooks

### Database Collections

- `payment_transactions` - Payment records
- `webhook_events` - Webhook audit trail
- `subscriptions` - Subscription data
- `tenant_companies` - Tenant data with `flutterwave_customer_id`

## Next Steps

1. Set environment variables
2. Configure Flutterwave webhook URL
3. Run tests: `pytest tests/integration/test_flutterwave_*.py`
4. Monitor webhook processing in logs
5. Test renewal with Flutterwave payment

See `CLAUDE.md` for the full architectural reference.
