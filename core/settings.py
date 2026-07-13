from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return tuple()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _safe_float_env(name: str, default: float) -> float:
    """Read a float env var, falling back to the default on garbage.

    A malformed threshold must not crash boot, and it must not silently
    become 0 (which would disable the check it exists to enforce).
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_int_list(value: str | None) -> tuple[int, ...]:
    if not value:
        return (0, 3, 7, 14, 21)
    return tuple(int(x.strip()) for x in value.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    env: str
    secret_key: str
    session_secret_key: str
    email_host: str | None
    email_port: int
    email_username: str | None
    email_password: str | None
    email_from_email: str | None
    email_sender_name: str
    email_retry_attempts: int
    email_retry_backoff_seconds: float
    email_queue_enabled: bool
    cors_origins: tuple[str, ...]
    debug_include_error_details: bool
    redis_url: str
    s3_bucket_name: str | None
    s3_region: str | None
    s3_endpoint_url: str | None
    s3_access_key_id: str | None
    s3_secret_access_key: str | None
    storage_backend: str
    storage_local_root: str
    payment_default_provider: str
    # The ``app`` checkout provider is a SIMULATOR, not a real processor.
    # It is registered automatically outside production; in production it
    # is registered only when this flag is explicitly set (break-glass).
    payment_app_mode_enabled: bool
    # Local filesystem storage exposes unauthenticated PUT/GET transport
    # shims. It is rejected in production unless this flag is set (for a
    # deliberately isolated local-only deployment).
    allow_local_storage_in_production: bool
    stripe_secret_key: str | None
    stripe_webhook_secret: str | None
    flutterwave_secret_key: str | None
    flutterwave_public_key: str | None
    flutterwave_webhook_secret_hash: str | None
    paystack_secret_key: str | None
    paystack_public_key: str | None
    # VisiChek-specific settings
    ocr_provider: str
    ocr_api_key: str | None
    ocr_api_url: str | None
    qr_signing_secret: str
    retention_check_interval_hours: int
    # Google Document AI (check-in system)
    gcp_project_id: str | None = None
    gcp_location: str = "us"
    docai_passport_processor_id: str | None = None
    docai_drivers_license_processor_id: str | None = None
    docai_national_id_processor_id: str | None = None
    gcp_access_token: str | None = None
    # ID number encryption
    id_number_encryption_key: str | None = None
    # Logging
    log_level: str = "INFO"
    # Billing / Dunning
    max_dunning_attempts: int = 5
    dunning_retry_days: tuple[int, ...] = (0, 3, 7, 14, 21)
    # Backup
    backup_enabled: bool = False
    backup_s3_bucket: str | None = None
    backup_retention_days: int = 30
    # Session
    session_inactivity_timeout_minutes: int = 15
    # OTP / 2FA
    otp_dev_code: str = "123456"
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    # Public base URL used to build absolute checkout links (app-mode fallback)
    app_base_url: str = ""
    # Default TTL (seconds) for a checkout session before it expires
    checkout_session_ttl_seconds: int = 24 * 60 * 60
    # Where hosted-checkout providers (Paystack / Flutterwave) redirect the
    # customer's browser AFTER payment — the "callback URL". Point this at a
    # FRONTEND page, NOT the webhook endpoint. The provider appends
    # ?reference=...&trxref=... so the page can resolve + display the result.
    # When unset, the provider falls back to the Callback URL configured in its
    # own dashboard. A per-checkout ``metadata.redirect_url`` overrides this.
    payment_callback_url: str = ""
    # Cloudflare Turnstile secret key for verifying self-onboarding submissions.
    # When unset, Turnstile verification is skipped (development convenience).
    turnstile_secret_key: str | None = None
    turnstile_verify_url: str = (
        "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    )
    # Dojah KYC. ``app_id`` is the public client identifier; ``secret_key``
    # signs server-side calls and verifies webhook signatures;
    # ``public_key`` is exposed to the kiosk widget. ``base_url`` falls
    # back to sandbox in development and production base in prod when
    # left unset.
    dojah_app_id: str | None = None
    dojah_secret_key: str | None = None
    dojah_public_key: str | None = None
    dojah_base_url: str | None = None
    # Override hatch — Dojah's standard design uses the private
    # ``secret_key`` as the webhook signing key (see their reference
    # implementation: `hmac.new(secret, payload, sha256)`). Leave this
    # unset to use ``dojah_secret_key`` (the normal case). Only set
    # this if an operator has explicitly provisioned a separate
    # signing secret for webhooks (rare; some bespoke deployments).
    dojah_webhook_secret: str | None = None
    # When true (production default) we reject webhooks that don't carry
    # the body-bound v1 signature, even if v2 is present. Set to "false"
    # in dev when working against the Dojah sandbox without webhook
    # configuration.
    dojah_require_v1_signature: bool = True
    # Minimum Dojah selfie confidence (0-100) for the face on the selfie to
    # count as the face on the ID. Dojah already computes this and we already
    # store it — until now nothing ever read it, so a valid ID held up by a
    # different person passed the face step silently. Set to 0 to disable the
    # threshold (not recommended outside local development).
    dojah_min_selfie_confidence: float = 70.0
    # Extremely verbose webhook diagnostics. When enabled, Dojah webhook
    # logs include full request headers, signatures, and body bytes.
    # Use only during active debugging because KYC payloads contain PII.
    dojah_debug_log_full_payload: bool = False

    # ------------------------------------------------------------------
    # Blog backend (merged from visichek-blog-backend)
    # ------------------------------------------------------------------
    # Cloudflare R2 — primary object store for blog images uploaded
    # through the admin editor. When unset, the R2 service raises a
    # 500 at upload time (matches blog backend's behaviour).
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_endpoint_url: str | None = None
    r2_bucket: str | None = None
    # Public URL prefix used to build absolute media URLs (e.g.
    # ``https://media.visichek.app``). Trailing slash is stripped.
    public_base_url: str = ""
    # Optional Unsplash search key — when set, blogs without a feature
    # image fall back to a topic-matched Unsplash photo.
    unsplash_access_key: str | None = None
    # Optional FreeImage.Host key — legacy host used by the
    # ``upload_to_freeimage_service`` helper. R2 is preferred; this is
    # retained so the path stays functional during transition.
    freeimage_api_key: str | None = None

    # ------------------------------------------------------------------
    # Push notifications (pluggable provider — see core/push/)
    # ------------------------------------------------------------------
    # ``push_provider`` selects the transport (currently ``webpush``).
    # Swapping to FCM/APNs/OneSignal means a new provider class in
    # core/push/ + flipping this value; nothing else changes.
    push_provider: str = "webpush"
    # How long the push service holds a message for an offline device.
    push_ttl_seconds: int = 24 * 60 * 60
    # Web Push (VAPID) credentials. ``vapid_public_key`` is the base64url
    # application server key the frontend passes to
    # ``pushManager.subscribe``; ``vapid_private_key`` signs outgoing
    # pushes; ``vapid_subject`` is the contact (``mailto:`` or URL) the
    # push service can reach. Defaults below are dev/test keys — override
    # ALL THREE via env in production.
    vapid_public_key: str = "BBM8Qbdy-SqWp_EAjXUWPtEVZJtHKJJ4Qds3wYyORAnrpnRXmCXv6cbTP_0WwA3pm9H8-TdfZhrWtrV3zK7tVcY"
    vapid_private_key: str = "cUICg_JfUvPmMZXZ-XrRx180wsQvz2i4GSPBkKpwdJk"
    vapid_subject: str = "mailto:support@visichek.app"

    # ------------------------------------------------------------------
    # Email provider selection + Resend (HTTPS API) transport.
    # ``email_provider`` chooses the transport WITHOUT touching the SMTP
    # path: "smtp" (default, unchanged behaviour) or "resend". When set to
    # "resend", outgoing mail goes through the Resend API using
    # ``resend_api_key`` and ``resend_from_email`` (a verified-domain
    # sender). Leaving ``resend_from_email`` unset falls back to
    # ``email_from_email`` / ``email_username``.
    # ------------------------------------------------------------------
    email_provider: str = "smtp"
    resend_api_key: str | None = None
    resend_from_email: str | None = None

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def is_local(self) -> bool:
        """A developer's machine — the only place the app-checkout SIMULATOR
        runs by default. ``ENV=local`` (case-insensitive). Every other value
        (development, staging, production) behaves like production."""
        return self.env.strip().lower() == "local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = os.getenv("ENV", "development")
    secret_key = os.getenv("SECRET_KEY", "")
    session_secret_key = os.getenv("SESSION_SECRET_KEY", "")

    default_redis = (
        os.getenv("CELERY_BROKER_URL")
        or os.getenv("REDIS_URL")
        or f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/0"
    )

    settings = Settings(
        env=env,
        secret_key=secret_key,
        session_secret_key=session_secret_key,
        email_host=os.getenv("EMAIL_HOST"),
        email_port=int(os.getenv("EMAIL_PORT", "587")),
        email_username=os.getenv("EMAIL_USERNAME"),
        email_password=os.getenv("EMAIL_PASSWORD"),
        email_from_email=os.getenv("EMAIL_FROM_EMAIL"),
        email_sender_name=os.getenv("EMAIL_SENDER_NAME", "FasterAPI"),
        email_retry_attempts=int(os.getenv("EMAIL_RETRY_ATTEMPTS", "3")),
        email_retry_backoff_seconds=float(
            os.getenv("EMAIL_RETRY_BACKOFF_SECONDS", "1.0")
        ),
        email_queue_enabled=os.getenv("EMAIL_QUEUE_ENABLED", "true").lower()
        in {"1", "true", "yes"},
        cors_origins=_split_csv(os.getenv("CORS_ORIGINS")),
        debug_include_error_details=os.getenv(
            "DEBUG_INCLUDE_ERROR_DETAILS", "false"
        ).lower()
        in {"1", "true", "yes"},
        redis_url=default_redis,
        s3_bucket_name=os.getenv("S3_BUCKET_NAME"),
        s3_region=os.getenv("S3_REGION"),
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL"),
        # Explicit S3/R2 creds; fall back to the standard AWS_* chain so
        # existing AWS-credential setups keep working unchanged.
        s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID")
        or os.getenv("AWS_ACCESS_KEY_ID"),
        s3_secret_access_key=(
            os.getenv("S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
        ),
        storage_backend=os.getenv("STORAGE_BACKEND", "local").lower(),
        storage_local_root=os.getenv("STORAGE_LOCAL_ROOT", "uploads"),
        payment_default_provider=os.getenv(
            "PAYMENT_DEFAULT_PROVIDER", "flutterwave"
        ).lower(),
        payment_app_mode_enabled=os.getenv("PAYMENT_APP_MODE_ENABLED", "false").lower()
        in {"1", "true", "yes"},
        allow_local_storage_in_production=os.getenv(
            "ALLOW_LOCAL_STORAGE_IN_PRODUCTION", "false"
        ).lower()
        in {"1", "true", "yes"},
        stripe_secret_key=os.getenv("STRIPE_SECRET_KEY"),
        stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET"),
        flutterwave_secret_key=os.getenv("FLUTTERWAVE_SECRET_KEY"),
        flutterwave_public_key=os.getenv("FLUTTERWAVE_PUBLIC_KEY"),
        flutterwave_webhook_secret_hash=os.getenv("FLW_WEBHOOK_SECRET_HASH"),
        paystack_secret_key=os.getenv("PAYSTACK_SECRET_KEY"),
        paystack_public_key=os.getenv("PAYSTACK_PUBLIC_KEY"),
        ocr_provider=os.getenv("OCR_PROVIDER", "none").lower(),
        ocr_api_key=os.getenv("OCR_API_KEY"),
        ocr_api_url=os.getenv("OCR_API_URL"),
        qr_signing_secret=os.getenv("QR_SIGNING_SECRET", ""),
        retention_check_interval_hours=int(
            os.getenv("RETENTION_CHECK_INTERVAL_HOURS", "24")
        ),
        gcp_project_id=os.getenv("GCP_PROJECT_ID"),
        gcp_location=os.getenv("GCP_LOCATION", "us"),
        docai_passport_processor_id=os.getenv("DOCAI_PASSPORT_PROCESSOR_ID"),
        docai_drivers_license_processor_id=os.getenv(
            "DOCAI_DRIVERS_LICENSE_PROCESSOR_ID"
        ),
        docai_national_id_processor_id=os.getenv("DOCAI_NATIONAL_ID_PROCESSOR_ID"),
        gcp_access_token=os.getenv("GCP_ACCESS_TOKEN"),
        id_number_encryption_key=os.getenv("ID_NUMBER_ENCRYPTION_KEY"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        max_dunning_attempts=int(os.getenv("MAX_DUNNING_ATTEMPTS", "5")),
        dunning_retry_days=_parse_int_list(
            os.getenv("DUNNING_RETRY_DAYS", "0,3,7,14,21")
        ),
        backup_enabled=os.getenv("BACKUP_ENABLED", "false").lower()
        in {"1", "true", "yes"},
        backup_s3_bucket=os.getenv("BACKUP_S3_BUCKET"),
        backup_retention_days=int(os.getenv("BACKUP_RETENTION_DAYS", "30")),
        session_inactivity_timeout_minutes=int(
            os.getenv("SESSION_INACTIVITY_TIMEOUT_MINUTES", "15")
        ),
        otp_dev_code=os.getenv("OTP_DEV_CODE", "123456"),
        otp_ttl_seconds=int(os.getenv("OTP_TTL_SECONDS", "300")),
        otp_max_attempts=int(os.getenv("OTP_MAX_ATTEMPTS", "5")),
        app_base_url=os.getenv("APP_BASE_URL", "").strip(),
        checkout_session_ttl_seconds=int(
            os.getenv("CHECKOUT_SESSION_TTL_SECONDS", str(24 * 60 * 60))
        ),
        payment_callback_url=os.getenv("PAYMENT_CALLBACK_URL", "").strip(),
        turnstile_secret_key=os.getenv("TURNSTILE_SECRET_KEY") or None,
        turnstile_verify_url=os.getenv(
            "TURNSTILE_VERIFY_URL",
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        ),
        dojah_app_id="69f0e700ece0dca6443aba71",
        dojah_secret_key=os.getenv("DOJAH_SECRET_KEY") or None,
        dojah_public_key=os.getenv("DOJAH_PUBLIC_KEY") or None,
        dojah_base_url=os.getenv("DOJAH_BASE_URL") or None,
        dojah_webhook_secret=os.getenv("DOJAH_WEBHOOK_SECRET") or None,
        dojah_require_v1_signature=os.getenv(
            "DOJAH_REQUIRE_V1_SIGNATURE", "true"
        ).lower()
        in {"1", "true", "yes"},
        dojah_min_selfie_confidence=_safe_float_env(
            "DOJAH_MIN_SELFIE_CONFIDENCE", 70.0
        ),
        dojah_debug_log_full_payload=os.getenv(
            "DOJAH_DEBUG_LOG_FULL_PAYLOAD", "false"
        ).lower()
        in {"1", "true", "yes"},
        r2_access_key_id=os.getenv("R2_ACCESS_KEY_ID") or None,
        r2_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY") or None,
        r2_endpoint_url=os.getenv("R2_ENDPOINT_URL") or None,
        r2_bucket=os.getenv("R2_BUCKET") or None,
        public_base_url=(os.getenv("PUBLIC_BASE_URL") or "").rstrip("/"),
        unsplash_access_key=os.getenv("UNSPLASH_ACCESS_KEY") or None,
        freeimage_api_key=os.getenv("FREEIMAGE_API_KEY") or None,
        push_provider=os.getenv("PUSH_PROVIDER", "webpush").lower(),
        push_ttl_seconds=int(os.getenv("PUSH_TTL_SECONDS", str(24 * 60 * 60))),
        vapid_public_key=os.getenv(
            "VAPID_PUBLIC_KEY",
            "BBM8Qbdy-SqWp_EAjXUWPtEVZJtHKJJ4Qds3wYyORAnrpnRXmCXv6cbTP_0WwA3pm9H8-TdfZhrWtrV3zK7tVcY",
        ),
        vapid_private_key=os.getenv(
            "VAPID_PRIVATE_KEY", "cUICg_JfUvPmMZXZ-XrRx180wsQvz2i4GSPBkKpwdJk"
        ),
        vapid_subject=os.getenv("VAPID_SUBJECT", "mailto:support@visichek.app"),
        email_provider=os.getenv("EMAIL_PROVIDER", "smtp").strip().lower(),
        resend_api_key=os.getenv("RESEND_API_KEY") or None,
        resend_from_email=os.getenv("RESEND_FROM_EMAIL") or None,
    )
    return settings
