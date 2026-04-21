# Admin Reader — Search Endpoint

Lightweight, read-only admin lookup for the application admin console. Use it to find an admin by id, email, or name without pulling the full admin list.

## Endpoint

```
GET /v1/admins/search
```

**Auth:** application admin (`role="admin"`), via `check_admin_account_status_and_permissions`.

## Query Parameters

| Param  | Type   | Required | Default | Notes                                                            |
|--------|--------|----------|---------|------------------------------------------------------------------|
| `q`    | string | yes      | —       | Search term. Min length 1. Matched against id, email, full name. |
| `start`| int    | no       | `0`     | Pagination offset (≥ 0).                                         |
| `stop` | int    | no       | `50`    | Pagination limit / exclusive end index (1–200).                  |

## Matching Rules

- **Email / full name** — case-insensitive substring match (MongoDB `$regex` with `$options: "i"`). The query is escaped before building the regex, so special characters (`.`, `+`, `@`, etc.) are treated literally.
- **Id** — when `q` is a valid 24-character ObjectId, an exact-match `_id` clause is OR'd into the query.
- **Primary super admin** — the `.env`-configured admin (id `656f7ac12b9d4f6c9e2b9f7d`) is included in results when `q` matches its id, email, or the literal string "super admin".

Search is live against MongoDB — it bypasses the precompute cache and the write queue because it is an interactive lookup (see CLAUDE.md, "What Stays Synchronous").

## Response

Responses use the standard envelope (`{ success, message, data, meta, requestId }`). `data` is a list of `AdminSearchResult` objects with only the fields needed to identify an admin — no passwords, tokens, or permission lists.

```json
{
  "success": true,
  "message": "Admin search results",
  "data": [
    {
      "id": "64f1a2b3c4d5e6f7a8b9c0d1",
      "full_name": "John Admin",
      "email": "admin@example.com",
      "account_status": "ACTIVE",
      "mfa_enabled": true,
      "date_created": 1712500000,
      "last_updated": 1712500600
    }
  ],
  "meta": { "page": 1, "limit": 25 },
  "requestId": "..."
}
```

### Fields

| Field            | Type             | Description                                                     |
|------------------|------------------|-----------------------------------------------------------------|
| `id`             | string           | Admin ObjectId as a string.                                     |
| `full_name`      | string           | Admin's display name.                                           |
| `email`          | string (email)   | Admin's email (not normalized in response).                     |
| `account_status` | `AccountStatus`  | `ACTIVE`, `INACTIVE`, or `SUSPENDED`.                           |
| `mfa_enabled`    | bool             | Whether TOTP 2FA is enabled on the account.                     |
| `date_created`   | int (unix epoch) | Creation timestamp, if available.                               |
| `last_updated`   | int (unix epoch) | Last update timestamp, if available.                            |

## Error Responses

| Status | Code                    | Cause                                      |
|--------|-------------------------|--------------------------------------------|
| 400    | `VALIDATION_FAILED`     | `q` omitted or whitespace-only.            |
| 401    | `AUTH_INVALID_TOKEN`    | Missing / expired / malformed admin token. |
| 403    | `AUTH_PERMISSION_DENIED`| Caller is not an application admin.        |

## Examples

### By partial email

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.example.com/v1/admins/search?q=jane%40acme"
```

### By partial name

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.example.com/v1/admins/search?q=jane"
```

### By id

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.example.com/v1/admins/search?q=64f1a2b3c4d5e6f7a8b9c0d1"
```

### Paginated

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.example.com/v1/admins/search?q=admin&start=50&stop=100"
```

## Implementation Map

| Layer        | File                                                                 | Symbol                      |
|--------------|----------------------------------------------------------------------|-----------------------------|
| Schema       | [schemas/admin_schema.py](../schemas/admin_schema.py)                | `AdminSearchResult`         |
| Repository   | [repositories/admin_repo.py](../repositories/admin_repo.py)          | `search_admins`             |
| Service      | [services/admin_service.py](../services/admin_service.py)            | `search_admins_by_query`    |
| Route        | [api/v1/admin_route.py](../api/v1/admin_route.py)                    | `search_admins_endpoint`    |
