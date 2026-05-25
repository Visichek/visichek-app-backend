"""Production security-posture validation.

A single place that decides whether the current configuration is safe to
run in production, and FAILS THE BOOT (``RuntimeError``) when it is not.
The goal is "fail closed": a production deployment must never silently
come up with a missing signing secret, an unsigned webhook, a payment
simulator, or an unauthenticated local-storage transport.

Outside production the same checks run but only log warnings — local /
dev / test must stay frictionless.

Call :func:`assert_security_posture` once at app startup (see
``main.py`` lifespan). It is intentionally import-light so it can also be
invoked from a smoke test against staging.
"""

from __future__ import annotations

import logging

from core.settings import Settings

logger = logging.getLogger(__name__)


def evaluate_security_posture(settings: Settings) -> list[str]:
    """Return a list of human-readable security-posture violations.

    Empty list == safe. The caller decides whether to fail (production)
    or warn (everything else).
    """
    violations: list[str] = []

    # --- Always-required signing / encryption secrets in production ------
    required_secrets = {
        "SECRET_KEY": settings.secret_key,
        "SESSION_SECRET_KEY": settings.session_secret_key,
        "QR_SIGNING_SECRET": settings.qr_signing_secret,
        "ID_NUMBER_ENCRYPTION_KEY": settings.id_number_encryption_key,
    }
    for name, value in required_secrets.items():
        if not value:
            violations.append(f"{name} must be set in production.")

    # --- Webhook secrets must back any registered payment provider ------
    if settings.flutterwave_secret_key and not settings.flutterwave_webhook_secret_hash:
        violations.append(
            "FLW_WEBHOOK_SECRET_HASH is required when FLUTTERWAVE_SECRET_KEY is set "
            "(unsigned Flutterwave webhooks would be forgeable)."
        )
    if settings.stripe_secret_key and not settings.stripe_webhook_secret:
        violations.append(
            "STRIPE_WEBHOOK_SECRET is required when STRIPE_SECRET_KEY is set "
            "(unsigned Stripe webhooks would be forgeable)."
        )

    # --- The app-mode simulator must never settle real money ------------
    if (
        settings.payment_default_provider == "app"
        and not settings.payment_app_mode_enabled
    ):
        violations.append(
            "PAYMENT_DEFAULT_PROVIDER=app selects the checkout SIMULATOR. "
            "Set a real provider, or set PAYMENT_APP_MODE_ENABLED=true to "
            "explicitly opt into break-glass app mode."
        )

    # At least one real payment provider must be configured (unless the
    # operator has deliberately enabled app mode as a break-glass).
    has_real_provider = bool(
        settings.stripe_secret_key
        or settings.flutterwave_secret_key
        or settings.paystack_secret_key
    )
    if not has_real_provider and not settings.payment_app_mode_enabled:
        violations.append(
            "No real payment provider is configured (Stripe / Flutterwave / "
            "Paystack). Configure one, or set PAYMENT_APP_MODE_ENABLED=true to "
            "run the simulator on purpose."
        )

    # --- Local storage exposes unauthenticated byte transport -----------
    if (
        settings.storage_backend != "s3"
        and not settings.allow_local_storage_in_production
    ):
        violations.append(
            "STORAGE_BACKEND=local exposes unauthenticated upload/read "
            "transport. Use STORAGE_BACKEND=s3, or set "
            "ALLOW_LOCAL_STORAGE_IN_PRODUCTION=true for a deliberately "
            "isolated local-only deployment."
        )

    # --- A static, reusable OTP code is not a real second factor --------
    if settings.otp_dev_code == "123456":
        violations.append(
            "OTP_DEV_CODE is left at the default '123456'. Override it in "
            "production so the primary-admin OTP is not a known shared secret."
        )

    return violations


def assert_security_posture(settings: Settings) -> None:
    """Validate posture; raise in production, warn elsewhere.

    Raises ``RuntimeError`` (refusing to start) when running in production
    with one or more violations. Outside production the violations are
    logged as warnings so local development stays unblocked.
    """
    violations = evaluate_security_posture(settings)
    if not violations:
        logger.info("Security posture check passed (env=%s)", settings.env)
        return

    bullet_list = "\n".join(f"  - {v}" for v in violations)
    if settings.is_production:
        raise RuntimeError(
            f"Refusing to start: unsafe production security posture.\n{bullet_list}"
        )
    logger.warning(
        "Security posture warnings (non-fatal outside production):\n%s",
        bullet_list,
    )
