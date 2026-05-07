"""Ensure MongoDB indexes exist for hot query paths.

Called once at startup (from ``main.lifespan``). ``create_index`` is idempotent
in Motor — repeated calls are cheap once the index exists. Adding an index
here is almost always net-positive for read-heavy collections; if a collection
is write-heavy we pick a narrow compound rather than many single-field indexes.

Keep this list pruned. Unused indexes cost RAM and write throughput.
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger(__name__)


# Each entry: (collection_name, keys, options)
# ``keys`` is a list of (field, direction) tuples.
# ``options`` is passed straight to ``create_index``; common ones:
#   unique=True, sparse=True, name="<explicit>", expireAfterSeconds=<ttl>
_INDEX_PLAN: list[tuple[str, list[tuple[str, int]], dict[str, Any]]] = [
    # ── auth / tokens ────────────────────────────────────────────────
    # verify_*_token → look up by _id (auto-indexed), but delete_all_* scans by userId
    ("accessToken", [("userId", ASCENDING)], {}),
    ("accessToken", [("status", ASCENDING)], {"sparse": True}),
    ("refreshToken", [("userId", ASCENDING)], {}),
    # Brute-force lockout / password history
    (
        "login_attempts",
        [("identifier", ASCENDING)],
        {"unique": True},
    ),
    (
        "password_history",
        [("user_id", ASCENDING), ("role", ASCENDING), ("changed_at", DESCENDING)],
        {},
    ),
    # ── tenants ──────────────────────────────────────────────────────
    (
        "tenant_companies",
        [("company_name", ASCENDING)],
        {"unique": True},
    ),
    # ── accounts ─────────────────────────────────────────────────────
    ("admins", [("email", ASCENDING)], {"unique": True}),
    ("users", [("email", ASCENDING)], {"unique": True}),
    # Tenant users: email is only unique WITHIN a tenant.
    (
        "system_users",
        [("tenant_id", ASCENDING), ("email", ASCENDING)],
        {"unique": True},
    ),
    ("system_users", [("tenant_id", ASCENDING), ("role", ASCENDING)], {}),
    # ── plans / subscriptions / billing ──────────────────────────────
    ("plans", [("name", ASCENDING)], {"unique": True}),
    ("plans", [("status", ASCENDING), ("is_public", ASCENDING)], {}),
    (
        "subscriptions",
        [("tenant_id", ASCENDING), ("status", ASCENDING)],
        {},
    ),
    # Dunning / renewal scan: "due subscriptions"
    (
        "subscriptions",
        [("status", ASCENDING), ("current_period_end", ASCENDING)],
        {},
    ),
    (
        "subscriptions",
        [("status", ASCENDING), ("next_retry_at", ASCENDING)],
        {"sparse": True},
    ),
    (
        "discounts",
        [("code", ASCENDING)],
        {"unique": True, "sparse": True},
    ),
    ("discounts", [("status", ASCENDING), ("valid_until", ASCENDING)], {}),
    # Usage aggregates — hot on every authenticated request (quota checks).
    (
        "usage_aggregates",
        [
            ("tenant_id", ASCENDING),
            ("collection", ASCENDING),
            ("operation", ASCENDING),
            ("period_key", ASCENDING),
        ],
        {"unique": True},
    ),
    (
        "usage_records",
        [("tenant_id", ASCENDING), ("timestamp", DESCENDING)],
        {},
    ),
    # Invoices: list by tenant newest-first, lookup by number.
    (
        "invoices",
        [("tenant_id", ASCENDING), ("date_created", DESCENDING)],
        {},
    ),
    ("invoices", [("number", ASCENDING)], {"unique": True, "sparse": True}),
    ("invoice_counters", [("year", ASCENDING)], {"unique": True}),
    # ── payments / checkout / webhooks ───────────────────────────────
    (
        "payment_transactions",
        [("reference", ASCENDING)],
        {"unique": True, "sparse": True},
    ),
    ("payment_transactions", [("owner_id", ASCENDING)], {}),
    ("payment_transactions", [("status", ASCENDING)], {}),
    (
        "webhook_events",
        [("provider", ASCENDING), ("event_id", ASCENDING)],
        {"unique": True},
    ),
    (
        "checkout_sessions",
        [("tenant_id", ASCENDING), ("date_created", DESCENDING)],
        {},
    ),
    (
        "checkout_sessions",
        [("provider_reference", ASCENDING)],
        {"unique": True, "sparse": True},
    ),
    ("checkout_sessions", [("status", ASCENDING), ("expires_at", ASCENDING)], {}),
    # ── core tenant data ─────────────────────────────────────────────
    ("branches", [("tenant_id", ASCENDING), ("status", ASCENDING)], {}),
    ("branches", [("tenant_id", ASCENDING), ("name", ASCENDING)], {}),
    ("departments", [("tenant_id", ASCENDING)], {}),
    ("departments", [("tenant_id", ASCENDING), ("name", ASCENDING)], {}),
    (
        "visitors",
        [("tenant_id", ASCENDING), ("check_in_time", DESCENDING)],
        {},
    ),
    ("visitors", [("tenant_id", ASCENDING), ("status", ASCENDING)], {}),
    ("visitors", [("tenant_id", ASCENDING), ("host_user_id", ASCENDING)], {}),
    (
        "visitor_profiles",
        [("tenant_id", ASCENDING), ("email_normalized", ASCENDING)],
        {"sparse": True},
    ),
    (
        "visitor_profiles",
        [("tenant_id", ASCENDING), ("phone", ASCENDING)],
        {"sparse": True},
    ),
    (
        "appointments",
        [("tenant_id", ASCENDING), ("scheduled_time", ASCENDING)],
        {},
    ),
    ("appointments", [("tenant_id", ASCENDING), ("status", ASCENDING)], {}),
    ("incidents", [("tenant_id", ASCENDING), ("status", ASCENDING)], {}),
    ("incidents", [("tenant_id", ASCENDING), ("detection_time", DESCENDING)], {}),
    ("documents", [("tenant_id", ASCENDING), ("date_created", DESCENDING)], {}),
    (
        "audit_trail",
        [("tenant_id", ASCENDING), ("timestamp", DESCENDING)],
        {},
    ),
    (
        "audit_trail",
        [("resource_type", ASCENDING), ("resource_id", ASCENDING)],
        {},
    ),
    (
        "data_subject_requests",
        [("tenant_id", ASCENDING), ("status", ASCENDING)],
        {},
    ),
    ("privacy_notices", [("tenant_id", ASCENDING), ("version", ASCENDING)], {}),
    ("sub_processors", [("tenant_id", ASCENDING)], {}),
    # ── settings / sessions / notifications ──────────────────────────
    (
        "user_settings",
        [("user_id", ASCENDING), ("user_type", ASCENDING)],
        {"unique": True},
    ),
    (
        "user_preferences",
        [("user_id", ASCENDING), ("user_type", ASCENDING), ("key", ASCENDING)],
        {"unique": True},
    ),
    ("tenant_settings", [("tenant_id", ASCENDING)], {"unique": True}),
    (
        "sessions",
        [("user_id", ASCENDING), ("is_current", ASCENDING)],
        {},
    ),
    ("sessions", [("user_id", ASCENDING), ("last_active_at", DESCENDING)], {}),
    (
        "notifications",
        [("user_id", ASCENDING), ("read", ASCENDING), ("date_created", DESCENDING)],
        {},
    ),
    (
        "notification_preferences",
        [("user_id", ASCENDING), ("user_type", ASCENDING)],
        {"unique": True},
    ),
    # ── 2FA / OTP ────────────────────────────────────────────────────
    (
        "totp_secrets",
        [("user_id", ASCENDING), ("user_type", ASCENDING)],
        {"unique": True},
    ),
    ("backup_codes", [("user_id", ASCENDING)], {}),
    (
        "otp_challenges",
        [("challenge_id", ASCENDING)],
        {"unique": True, "sparse": True},
    ),
    # ── check-in system ─────────────────────────────────────────────
    ("checkin_configs", [("tenant_id", ASCENDING)], {}),
    ("badges", [("tenant_id", ASCENDING), ("qr_code_value", ASCENDING)], {}),
    ("id_verification_hashes", [("tenant_id", ASCENDING), ("hash", ASCENDING)], {}),
    # ── support cases (platform support threads) ─────────────────────
    (
        "support_cases",
        [
            ("tenant_id", ASCENDING),
            ("status", ASCENDING),
            ("last_message_at", DESCENDING),
        ],
        {},
    ),
    ("support_cases", [("tenant_id", ASCENDING), ("status", ASCENDING)], {}),
    ("support_cases", [("assigned_admin_id", ASCENDING), ("status", ASCENDING)], {}),
    ("support_cases", [("status", ASCENDING), ("sla_due_at", ASCENDING)], {}),
    (
        "support_case_messages",
        [("case_id", ASCENDING), ("date_created", ASCENDING)],
        {},
    ),
    # ── self-onboarding (public marketing-site lead capture) ─────────
    (
        "onboarding_submissions",
        [("submitted_at", DESCENDING)],
        {},
    ),
    (
        "onboarding_submissions",
        [("status", ASCENDING), ("submitted_at", DESCENDING)],
        {},
    ),
    (
        "onboarding_submissions",
        [("email", ASCENDING)],
        {"sparse": True},
    ),
    (
        "onboarding_submissions",
        [("super_admin_user_id", ASCENDING)],
        {"sparse": True},
    ),
    (
        "onboarding_submissions",
        [("tenant_id", ASCENDING)],
        {"sparse": True},
    ),
]


async def ensure_indexes(db: Any) -> dict[str, int]:
    """Create (or confirm) every index in ``_INDEX_PLAN``. Idempotent.

    Returns a summary mapping collection → count of indexes ensured on it.
    Failures are logged but never raised: the app must be able to boot with a
    Mongo that's slow to build indexes.
    """
    summary: dict[str, int] = {}
    for collection, keys, options in _INDEX_PLAN:
        try:
            name = await db[collection].create_index(keys, **options)
            summary[collection] = summary.get(collection, 0) + 1
            logger.debug("Ensured index %s on %s (%s)", name, collection, keys)
        except Exception as err:
            logger.warning(
                "Failed to ensure index on %s (%s): %s", collection, keys, err
            )
    logger.info(
        "Mongo indexes ensured: %s total across %s collections",
        sum(summary.values()),
        len(summary),
    )
    return summary
