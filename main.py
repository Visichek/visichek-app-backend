from __future__ import annotations

import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import redis
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from limits import parse as parse_rate
from limits.storage import RedisStorage
from limits.strategies import FixedWindowRateLimiter
from pymongo import MongoClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from celery_worker import celery_app
from core.email.manager import EmailManager
from core.logging_config import configure_logging, get_logger
from core.payments.manager import PaymentManager
from core.queue import registrations as _queue_registrations  # noqa: F401
from core.queue.celery_provider import CeleryQueueProvider
from core.queue.manager import QueueManager
from core.response_envelope import (
    apply_response_documentation,
    document_response,
    error_response,
    http_exception_response,
)
from core.scheduler import scheduler
from core.role_config import (
    build_role_rate_limits,
    build_role_rate_limits_csv,
    normalize_role,
)
from core.settings import get_settings
from core.storage.manager import DocumentStorageManager
from repositories.tokens_repo import get_access_token_allow_expired

settings = get_settings()

# --- Configure structured logging before anything else ---
configure_logging(log_level=settings.log_level, is_production=settings.is_production)
logger = get_logger(__name__)

MONGO_URI = os.getenv("MONGO_URL")
mongo_client: "MongoClient | None" = (
    MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000) if MONGO_URI else None
)
redis_client = redis.Redis.from_url(
    settings.redis_url, socket_connect_timeout=2, decode_responses=True
)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        response.headers["X-Process-Time"] = str(time.time() - start_time)
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every completed HTTP request with method, path, status, duration, and context."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        client_host = request.client.host if request.client else "-"
        method = request.method
        path = request.url.path
        query = request.url.query

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.time() - start_time) * 1000
            request_id = getattr(request.state, "request_id", None)
            logger.exception(
                "Unhandled exception during request %s %s (%.2fms)",
                method,
                path,
                duration_ms,
                extra={
                    "request_id": request_id,
                    "endpoint": path,
                },
            )
            raise

        duration_ms = (time.time() - start_time) * 1000
        request_id = getattr(request.state, "request_id", None)
        user_id = response.headers.get("X-User-Id")
        user_type = response.headers.get("X-User-Type")
        status_code = response.status_code

        log_level = logging.INFO
        if status_code >= 500:
            log_level = logging.ERROR
        elif status_code >= 400:
            log_level = logging.WARNING

        logger.log(
            log_level,
            '%s - "%s %s%s" %d %.2fms',
            client_host,
            method,
            path,
            f"?{query}" if query else "",
            status_code,
            duration_ms,
            extra={
                "request_id": request_id,
                "user_id": user_id,
                "endpoint": path,
                "method": method,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
                "user_type": user_type,
            },
        )
        return response


ROLE_RATE_LIMITS_DEFAULT = build_role_rate_limits_csv(non_admin_roles=["user"])
RATE_LIMITS = build_role_rate_limits(
    os.getenv("ROLE_RATE_LIMITS"),
    fallback_csv=ROLE_RATE_LIMITS_DEFAULT,
)

if settings.env.lower() == "development":
    dev_rule = parse_rate("100000/second")
    RATE_LIMITS = {role: dev_rule for role in RATE_LIMITS}

storage = RedisStorage(settings.redis_url)
limiter = FixedWindowRateLimiter(storage)


async def get_user_type(request: Request) -> tuple[str, str]:
    auth_header = request.headers.get("Authorization")
    fallback_id = request.headers.get("X-Forwarded-For") or request.client.host  # type: ignore

    token: str | None = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", maxsplit=1)[1]
    else:
        token = request.cookies.get("access_token")

    if not token:
        return fallback_id, "anonymous"

    access_token = await get_access_token_allow_expired(accessToken=token)
    if not access_token:
        return fallback_id, "anonymous"

    user_type = normalize_role(access_token.role or "anonymous")
    if user_type not in RATE_LIMITS:
        user_type = "anonymous"

    return access_token.userId, user_type


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security-related HTTP headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


class RateLimitingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if get_settings().env == "testing":
            return await call_next(request)
        user_id, user_type = await get_user_type(request)
        rate_limit_rule = RATE_LIMITS[user_type]

        allowed = limiter.hit(rate_limit_rule, user_id)
        reset_time, remaining = limiter.get_window_stats(rate_limit_rule, user_id)
        seconds_until_reset = max(math.ceil(reset_time - time.time()), 0)

        headers = {
            "X-User-Id": user_id,
            "X-User-Type": user_type,
            "X-RateLimit-Limit": str(rate_limit_rule.amount),
            "X-RateLimit-Remaining": str(max(remaining, 0)),
            "X-RateLimit-Reset": str(seconds_until_reset),
        }

        if not allowed:
            headers["Retry-After"] = str(seconds_until_reset)
            return error_response(
                status_code=429,
                message="Too Many Requests",
                data={
                    "code": "TOO_MANY_REQUESTS",
                    "details": {
                        "retry_after_seconds": seconds_until_reset,
                        "user_type": user_type,
                    },
                },
                headers=headers,
                request_id=getattr(request.state, "request_id", None),
            )

        response = await call_next(request)
        for key, value in headers.items():
            response.headers[key] = value
        return response


def apscheduler_heartbeat() -> None:
    redis_client.set("apscheduler:heartbeat", str(time.time()), ex=60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup validation ---
    if settings.is_production and not settings.session_secret_key:
        raise RuntimeError(
            "SESSION_SECRET_KEY must be set in production. Refusing to start."
        )
    if settings.is_production and not settings.secret_key:
        raise RuntimeError("SECRET_KEY must be set in production. Refusing to start.")

    logger.info("Starting VisiChek backend (env=%s)", settings.env)

    scheduler.add_job(
        apscheduler_heartbeat,
        trigger=IntervalTrigger(seconds=15),
        id="apscheduler_heartbeat",
        name="APScheduler Heartbeat",
        replace_existing=True,
    )
    scheduler.start()

    QueueManager.configure(CeleryQueueProvider(celery_app=celery_app))
    EmailManager.configure_from_settings()
    DocumentStorageManager.configure_from_settings()
    try:
        PaymentManager.configure_from_settings()
    except RuntimeError:
        # Payments remain unavailable until provider credentials are configured.
        pass

    try:
        from core.kyc import KYCManager

        KYCManager.configure_from_settings()
    except Exception:
        # KYC providers (Dojah) remain unavailable until credentials are
        # configured — kyc_available_for_tenant returns False, kiosks
        # silently skip the verify step.
        logger.warning("KYC manager configuration deferred", exc_info=True)

    # Configure OCR manager (optional - only if credentials are provided)
    try:
        from core.ocr.manager import OCRManager

        OCRManager.configure_from_settings()
    except RuntimeError:
        pass

    # Create TTL index for OTP challenges (auto-expire)
    try:
        from core.database import db as _db

        await _db["pending_otp"].create_index("expires_at", expireAfterSeconds=0)
    except Exception:
        logger.warning("Could not create pending_otp TTL index")

    # Ensure the rest of the hot-path indexes exist (idempotent).
    try:
        from core.database import db as _db
        from core.indexes import ensure_indexes

        await ensure_indexes(_db)
    except Exception:
        logger.warning("ensure_indexes failed at startup", exc_info=True)

    # Prime the platform-wide security policy cache so password validators
    # (which run synchronously inside Pydantic) see real values rather than
    # the dataclass defaults on the very first request after boot.
    try:
        from core.security_policy import get_security_policy

        await get_security_policy(force_refresh=True)
    except Exception:
        logger.warning("security policy prime failed at startup", exc_info=True)

    # One-shot branch-assignment backfill. Idempotent: tenants that already
    # have a HQ branch and users that already carry branch_ids are skipped.
    # See services/branch_backfill.py for the rationale.
    try:
        from services.branch_backfill import backfill_branch_assignments

        backfill_summary = await backfill_branch_assignments()
        logger.info("branch_backfill summary: %s", backfill_summary)
    except Exception:
        logger.warning("branch_backfill failed at startup", exc_info=True)

    # Canonical plan bootstrap. Idempotent: upserts the four canonical plans
    # (Free / Starter / Premium / Enterprise), archives any legacy plan, and
    # auto-subscribes any tenant without an active subscription onto Free.
    # See services/plan_bootstrap.py for the rationale.
    try:
        from services.plan_bootstrap import run_full_bootstrap

        plan_bootstrap_summary = await run_full_bootstrap()
        logger.info("plan_bootstrap summary: %s", plan_bootstrap_summary)
    except Exception:
        logger.warning("plan_bootstrap failed at startup", exc_info=True)

    # Schedule retention cleanup job
    from services.retention_service import run_retention_cleanup

    scheduler.add_job(
        run_retention_cleanup,
        trigger=IntervalTrigger(hours=settings.retention_check_interval_hours),
        id="retention_cleanup",
        name="Data Retention Cleanup",
        replace_existing=True,
    )

    # Schedule expired discount cleanup (every 6 hours)
    scheduler.add_job(
        "services.discount_service:expire_stale_discounts",
        trigger=IntervalTrigger(hours=6),
        id="expire_stale_discounts",
        name="Expire Stale Discounts",
        replace_existing=True,
    )

    # Schedule usage record cleanup (daily, keep 90 days)
    scheduler.add_job(
        "services.usage_service:cleanup_old_usage_records",
        trigger=IntervalTrigger(hours=24),
        id="cleanup_usage_records",
        name="Usage Record Cleanup",
        replace_existing=True,
    )

    # Schedule subscription renewal check (hourly)
    scheduler.add_job(
        "services.renewal_service:renew_due_subscriptions",
        trigger=IntervalTrigger(hours=1),
        id="renew_due_subscriptions",
        name="Subscription Renewal Check",
        replace_existing=True,
    )

    # Schedule trial conversion check (hourly)
    scheduler.add_job(
        "services.renewal_service:convert_expiring_trials",
        trigger=IntervalTrigger(hours=1),
        id="convert_expiring_trials",
        name="Trial Conversion Check",
        replace_existing=True,
    )

    # Schedule dunning process (every 6 hours)
    scheduler.add_job(
        "services.dunning_service:process_dunning",
        trigger=IntervalTrigger(hours=6),
        id="process_dunning",
        name="Dunning Process",
        replace_existing=True,
    )

    # Schedule precompute fanout for active tenants (every 60s). The job
    # scans Redis for tenants with live auth traffic and enqueues a
    # refresh per registered precompute resource on the `precompute`
    # celery queue. See core/queue/precompute.py for registered loaders.
    scheduler.add_job(
        "core.queue.precompute:fanout_for_active_tenants",
        trigger=IntervalTrigger(seconds=60),
        id="precompute_fanout_active_tenants",
        name="Precompute Fanout (Active Tenants)",
        replace_existing=True,
    )

    # Support-case auto-close: RESOLVED → CLOSED after 7 days of inactivity.
    scheduler.add_job(
        "services.support_case_service:auto_close_resolved_cases",
        trigger=IntervalTrigger(hours=6),
        id="support_case_auto_close",
        name="Support Case Auto-Close",
        replace_existing=True,
    )
    # Nudge tenants whose cases sit in AWAITING_TENANT for 48h+ (runs daily).
    scheduler.add_job(
        "services.support_case_service:nudge_awaiting_tenant_cases",
        trigger=IntervalTrigger(hours=12),
        id="support_case_nudge_awaiting",
        name="Support Case Awaiting-Tenant Nudge",
        replace_existing=True,
    )
    # Alert admins on SLA breaches for STANDARD/PRIORITY tenants.
    scheduler.add_job(
        "services.support_case_service:alert_sla_breaches",
        trigger=IntervalTrigger(hours=1),
        id="support_case_sla_breach",
        name="Support Case SLA Breach Alerts",
        replace_existing=True,
    )
    # Flip SCHEDULED appointments past their grace window to NO_SHOW.
    # Runs hourly so the dashboard's appointment-status pie reflects
    # missed visits without operator intervention.
    scheduler.add_job(
        "services.appointment_lifecycle_service:mark_no_shows",
        trigger=IntervalTrigger(hours=1),
        id="appointment_no_show_sweep",
        name="Appointment No-Show Sweeper",
        replace_existing=True,
    )

    try:
        yield
    finally:
        # Allow in-flight fire-and-forget tasks (e.g. audit writes) to
        # settle before the process goes down. Bounded so a stuck task
        # can't delay shutdown forever.
        try:
            from core.background_tasks import drain_pending

            await drain_pending(timeout=5.0)
        except Exception:
            logger.warning("drain_pending raised during shutdown", exc_info=True)
        scheduler.shutdown()


from core.case_conversion import CaseConversionMiddleware
from core.http_cache import HttpCacheMiddleware
from core.plan_enforcement import PlanEnforcementMiddleware

app = FastAPI(lifespan=lifespan, title="VisiChek REST API")
app.add_middleware(CaseConversionMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key
    or "dev-only-session-secret-NOT-FOR-PRODUCTION",
)
app.add_middleware(HttpCacheMiddleware)
app.add_middleware(PlanEnforcementMiddleware)
app.add_middleware(RateLimitingMiddleware)
app.add_middleware(RequestLoggingMiddleware)
_cors_origins = list(settings.cors_origins) if settings.cors_origins else []
_default_origins = [
    "http://localhost:3000",
    "https://visichek.app",
    "https://www.visichek.app",
    "https://client.visichek.app",
    "https://visichek-app.vercel.app",
]
for _origin in _default_origins:
    if _origin not in _cors_origins:
        _cors_origins.append(_origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Set-Cookie"],
)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    return http_exception_response(exc=exc, request=request)


@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    return error_response(
        status_code=422,
        message="Validation error",
        data={"code": "VALIDATION_FAILED", "details": {"errors": exc.errors()}},
        request_id=getattr(request.state, "request_id", None),
    )


@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        extra={
            "request_id": request_id,
            "endpoint": request.url.path,
            "method": request.method,
        },
    )
    details = (
        str(exc)
        if (settings.debug_include_error_details and not settings.is_production)
        else None
    )
    return error_response(
        status_code=500,
        message="Internal Server Error",
        data={
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred. Please try again later.",
            "details": details,
        },
        request_id=request_id,
    )


@app.get("/", tags=["Health"], include_in_schema=False)
@document_response(
    message="Successfully fetched data",
    success_example={"message": "Hello from FasterAPI!"},
)
def read_root(request: Request):
    return {
        "message": "Hello from FasterAPI!",
        "request_id": getattr(request.state, "request_id", None),
    }


@app.get("/health/live", tags=["Health"], include_in_schema=False)
async def liveness_check():
    """Liveness probe: confirms the process is running. Always returns 200."""
    return JSONResponse({"status": "alive", "timestamp": datetime.utcnow().isoformat()})


@app.get("/health/ready", tags=["Health"])
@document_response(
    message="Readiness check completed",
    success_example={
        "status": "ready",
        "services": {"mongo": "healthy", "redis": "healthy"},
    },
)
async def readiness_check():
    """Readiness probe: confirms MongoDB and Redis are reachable.
    Returns 503 if either dependency is unreachable."""
    services: dict[str, dict[str, str | float]] = {}
    ready = True

    if mongo_client is not None:
        start = time.perf_counter()
        try:
            mongo_client.admin.command("ping")
            services["mongo"] = {
                "status": "healthy",
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            }
        except Exception as exc:
            ready = False
            services["mongo"] = {
                "status": "unhealthy",
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "message": str(exc),
            }

    start = time.perf_counter()
    try:
        redis_client.ping()
        services["redis"] = {
            "status": "healthy",
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        }
    except Exception as exc:
        ready = False
        services["redis"] = {
            "status": "unhealthy",
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "message": str(exc),
        }

    status_code = 200 if ready else 503
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "services": services},
        status_code=status_code,
    )


@app.get("/health", tags=["Health"])
@document_response(
    message="Health check completed",
    success_example={
        "status": "healthy",
        "services": {"mongo": "healthy", "redis": "healthy"},
    },
)
async def health_check():
    services: dict[str, dict[str, str | float]] = {}
    overall_status = "healthy"

    if mongo_client is not None:
        start = time.perf_counter()
        try:
            mongo_client.admin.command("ping")
            services["mongo"] = {
                "status": "healthy",
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "message": "MongoDB ping successful",
            }
        except Exception as exc:
            overall_status = "degraded"
            services["mongo"] = {
                "status": "unhealthy",
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "message": str(exc),
            }

    start = time.perf_counter()
    try:
        redis_client.ping()
        services["redis"] = {
            "status": "healthy",
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "message": "Redis ping successful",
        }
    except Exception as exc:
        overall_status = "degraded"
        services["redis"] = {
            "status": "unhealthy",
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "message": str(exc),
        }

    aps_heartbeat = redis_client.get("apscheduler:heartbeat")
    if aps_heartbeat:
        if isinstance(aps_heartbeat, bytes):
            aps_heartbeat = aps_heartbeat.decode("utf-8")
        age = time.time() - float(aps_heartbeat) # type: ignore
        services["apscheduler"] = {
            "status": "healthy" if age <= 30 else "degraded",
            "latency_ms": 0,
            "message": f"Last heartbeat {int(age)}s ago",
        }
        if age > 30:
            overall_status = "degraded"
    else:
        overall_status = "degraded"
        services["apscheduler"] = {
            "status": "unhealthy",
            "latency_ms": 0,
            "message": "No heartbeat found",
        }

    status_code = 200 if overall_status == "healthy" else 503
    return JSONResponse(
        {
            "status": overall_status,
            "timestamp": datetime.utcnow().isoformat(),
            "services": services,
        },
        status_code=status_code,
    )


# --- auto-routes-start ---
from api.v1.admin_route import router as v1_admin_route_router
from api.v1.documents_route import router as v1_documents_route_router
from api.v1.payments_route import router as v1_payments_route_router
from api.v1.user_route import router as v1_user_route_router
from api.v1.tenant_route import router as v1_tenant_route_router
from api.v1.department_route import router as v1_department_route_router
from api.v1.system_user_route import router as v1_system_user_route_router
from api.v1.visitor_route import router as v1_visitor_route_router
from api.v1.visitor_profile_route import router as v1_visitor_profile_route_router
from api.v1.appointment_route import router as v1_appointment_route_router
from api.v1.privacy_notice_route import router as v1_privacy_notice_route_router
from api.v1.dashboard_route import router as v1_dashboard_route_router
from api.v1.super_admin_route import router as v1_super_admin_route_router
from api.v1.data_subject_request_route import router as v1_dsr_route_router
from api.v1.retention_route import router as v1_retention_route_router
from api.v1.sub_processor_route import router as v1_sub_processor_route_router
from api.v1.compliance_route import router as v1_compliance_route_router
from api.v1.audit_route import router as v1_audit_route_router
from api.v1.incident_route import router as v1_incident_route_router
from api.v1.plan_route import router as v1_plan_route_router
from api.v1.subscription_route import router as v1_subscription_route_router
from api.v1.discount_route import router as v1_discount_route_router
from api.v1.usage_route import router as v1_usage_route_router
from api.v1.admin_dashboard_route import router as v1_admin_dashboard_route_router
from api.v1.branch_route import router as v1_branch_route_router
from api.v1.branding_route import router as v1_branding_route_router
from api.v1.invoice_route import router as v1_invoice_route_router
from api.v1.public_registration_route import (
    router as v1_public_registration_route_router,
)
from api.v1.public_rights_route import router as v1_public_rights_route_router
from api.v1.notification_route import router as v1_notification_route_router
from api.v1.admin_settings_route import router as v1_admin_settings_route_router
from api.v1.system_user_settings_route import (
    router as v1_system_user_settings_route_router,
)
from api.v1.tenant_settings_route import router as v1_tenant_settings_route_router
from api.v1.user_settings_route import router as v1_user_settings_route_router
from api.v1.session_management_route import router as v1_session_management_route_router
from api.v1.auth_management_route import router as v1_auth_management_route_router
from api.v1.account_route import router as v1_account_route_router
from api.v1.unified_tenant_settings_route import (
    router as v1_unified_tenant_settings_route_router,
)
from api.v1.unified_platform_settings_route import (
    router as v1_unified_platform_settings_route_router,
)
from api.v1.settings_manifest_route import router as v1_settings_manifest_route_router
from api.v1.checkin_config_route import router as v1_checkin_config_route_router
from api.v1.id_extraction_route import router as v1_id_extraction_route_router
from api.v1.checkin_route import router as v1_checkin_route_router
from api.v1.badge_route import router as v1_badge_route_router
from api.v1.face_crop_route import router as v1_face_crop_route_router
from api.v1.visitor_verification_route import (
    router as v1_visitor_verification_route_router,
)
from api.v1.checkin_submit_route import router as v1_checkin_submit_route_router
from api.v1.checkout_route import router as v1_checkout_route_router
from api.v1.trial_route import router as v1_trial_route_router
from api.v1.app_payment_route import router as app_payment_route_router
from api.v1.job_route import router as v1_job_route_router
from api.v1.support_case_route import router as v1_support_case_route_router
from api.v1.admin_support_case_route import (
    router as v1_admin_support_case_route_router,
)
from api.v1.onboarding_route import router as v1_onboarding_route_router
from api.v1.admin_onboarding_route import router as v1_admin_onboarding_route_router
from api.v1.tenant_enum_route import (
    public_router as v1_tenant_enum_public_router,
    router as v1_tenant_enum_route_router,
)
from api.v1.kyc_route import router as v1_kyc_route_router
from api.v1.saved_view_route import router as v1_saved_view_route_router
from api.v1.me_limitations_route import router as v1_me_limitations_route_router
from api.v1.tenant_form_route import (
    router as v1_tenant_form_route_router,
    public_router as v1_tenant_form_public_router,
)
# Blog backend port (visichek-blog-backend merged in). Admin routers
# live under /v1 alongside the existing /v1/admins admin surface;
# public website routers live under /api/v1; video streaming sits at
# the FastAPI root because saved video URLs have the form `/videos/{id}`.
from blog.routes.admin_blog_route import router as v1_blog_admin_router
from blog.routes.admin_media_route import router as v1_blog_media_admin_router
from blog.routes.admin_compat_route import router as v1_blog_admin_compat_router
from blog.routes.public_articles_route import router as blog_public_articles_router
from blog.routes.public_media_route import router as blog_public_media_router
from blog.routes.video_route import router as blog_video_router

app.include_router(v1_admin_route_router, prefix="/v1")
app.include_router(v1_documents_route_router, prefix="/v1")
app.include_router(v1_payments_route_router, prefix="/v1")
app.include_router(v1_user_route_router, prefix="/v1")
# NB: admin_onboarding_route MUST be included before tenant_route.
# tenant_route registers GET "/{tenant_id}" which would otherwise shadow
# the static GET /tenants/onboarding paths defined in admin_onboarding_route
# (both share the /tenants prefix, and FastAPI resolves in registration order).
app.include_router(v1_admin_onboarding_route_router, prefix="/v1")
app.include_router(v1_tenant_route_router, prefix="/v1")
app.include_router(v1_department_route_router, prefix="/v1")
# NB: system_user_settings_route MUST be included before system_user_route.
# system_user_route registers PATCH/DELETE "/{user_id}" which would otherwise
# shadow the static PATCH /settings, PATCH /preferences, DELETE /sessions
# paths defined in system_user_settings_route (both routers share the
# /system-users prefix, and FastAPI resolves routes in registration order).
app.include_router(v1_system_user_settings_route_router, prefix="/v1")
app.include_router(v1_system_user_route_router, prefix="/v1")
app.include_router(v1_visitor_route_router, prefix="/v1")
app.include_router(v1_visitor_profile_route_router, prefix="/v1")
app.include_router(v1_appointment_route_router, prefix="/v1")
app.include_router(v1_privacy_notice_route_router, prefix="/v1")
app.include_router(v1_dashboard_route_router, prefix="/v1")
app.include_router(v1_super_admin_route_router, prefix="/v1")
app.include_router(v1_dsr_route_router, prefix="/v1")
app.include_router(v1_retention_route_router, prefix="/v1")
app.include_router(v1_sub_processor_route_router, prefix="/v1")
app.include_router(v1_compliance_route_router, prefix="/v1")
app.include_router(v1_audit_route_router, prefix="/v1")
app.include_router(v1_incident_route_router, prefix="/v1")
app.include_router(v1_plan_route_router, prefix="/v1")
app.include_router(v1_subscription_route_router, prefix="/v1")
app.include_router(v1_discount_route_router, prefix="/v1")
app.include_router(v1_usage_route_router, prefix="/v1")
app.include_router(v1_admin_dashboard_route_router, prefix="/v1")
app.include_router(v1_branch_route_router, prefix="/v1")
app.include_router(v1_branding_route_router, prefix="/v1")
app.include_router(v1_invoice_route_router, prefix="/v1")
app.include_router(v1_public_registration_route_router, prefix="/v1")
app.include_router(v1_public_rights_route_router, prefix="/v1")
app.include_router(v1_notification_route_router, prefix="/v1")
app.include_router(v1_admin_settings_route_router, prefix="/v1")
# v1_system_user_settings_route_router is included earlier (before system_user_route)
# to avoid PATCH/DELETE "/{user_id}" shadowing its static paths.
app.include_router(v1_tenant_settings_route_router, prefix="/v1")
app.include_router(v1_user_settings_route_router, prefix="/v1")
app.include_router(v1_session_management_route_router, prefix="/v1")
app.include_router(v1_auth_management_route_router, prefix="/v1")
app.include_router(v1_account_route_router, prefix="/v1")
app.include_router(v1_unified_tenant_settings_route_router, prefix="/v1")
app.include_router(v1_unified_platform_settings_route_router, prefix="/v1")
app.include_router(v1_settings_manifest_route_router, prefix="/v1")
app.include_router(v1_checkin_config_route_router, prefix="/v1")
app.include_router(v1_id_extraction_route_router, prefix="/v1")
app.include_router(v1_checkin_route_router, prefix="/v1")
app.include_router(v1_badge_route_router, prefix="/v1")
app.include_router(v1_face_crop_route_router, prefix="/v1")
app.include_router(v1_visitor_verification_route_router, prefix="/v1")
app.include_router(v1_checkin_submit_route_router, prefix="/v1")
app.include_router(v1_checkout_route_router, prefix="/v1")
app.include_router(v1_trial_route_router, prefix="/v1")
app.include_router(v1_job_route_router, prefix="/v1")
app.include_router(v1_support_case_route_router, prefix="/v1")
app.include_router(v1_admin_support_case_route_router, prefix="/v1")
app.include_router(v1_onboarding_route_router, prefix="/v1")
# v1_admin_onboarding_route_router is included earlier (before tenant_route)
# to avoid GET "/{tenant_id}" shadowing /tenants/onboarding.
# Tenant-configurable enums (purpose-of-visit, id_type, ...).
# Two routers: super_admin CRUD + public kiosk bundle.
app.include_router(v1_tenant_enum_route_router, prefix="/v1")
app.include_router(v1_tenant_enum_public_router, prefix="/v1")
# KYC (Dojah today, pluggable). Public kiosk + webhook routes.
app.include_router(v1_kyc_route_router, prefix="/v1")
app.include_router(v1_saved_view_route_router, prefix="/v1")
app.include_router(v1_me_limitations_route_router, prefix="/v1")
# Tenant form builder — authenticated CRUD + kiosk-public read endpoints.
app.include_router(v1_tenant_form_route_router, prefix="/v1")
app.include_router(v1_tenant_form_public_router, prefix="/v1")
# App-mode payment simulator — deliberately NOT under /v1 so the URLs
# match the checkout_url emitted by AppCheckoutPaymentProvider.
app.include_router(app_payment_route_router)

# ----- Blog backend port -----
# Admin blog routes (/v1/blogs, /v1/media) — authenticated via the
# host's `check_admin_account_status_and_permissions` dep.
app.include_router(v1_blog_admin_router, prefix="/v1")
app.include_router(v1_blog_media_admin_router, prefix="/v1")
# Blog-admin compatibility aliases (/v1/admins/me, /v1/admins/invite,
# /v1/admins/login/verify-otp, /v1/admins/mfa/*). These coexist with
# the host's /v1/admins/* router and never shadow its paths.
app.include_router(v1_blog_admin_compat_router, prefix="/v1")
# Public-website article + media reads. The original blog backend
# mounted these as a sub-app at /api/v1; here they're plain
# APIRouters so they share middleware (rate limiting, http cache,
# request ids) with the rest of the API.
app.include_router(blog_public_articles_router, prefix="/api/v1")
app.include_router(blog_public_media_router, prefix="/api/v1")
# Video streaming sits at the root because emitted media URLs have
# the form ``/videos/{id}``.
app.include_router(blog_video_router)
# --- auto-routes-end ---

# Enterprise sub-apps. Each Enterprise plan can ship its own APIRouter
# mounted at /v1/enterprise/<slug>/*. Modules that define those routers
# call ``register_enterprise_app(slug, router)`` at import time, so we
# trigger their imports first (via core.enterprise_registrations) and
# then include each registered router on the parent FastAPI instance.
# See ``core/enterprise_apps.py`` for the contract and CLAUDE.md for
# the MUST rule that governs implementing one.
try:
    import core.enterprise_registrations  # noqa: F401  side-effect imports
except ImportError:
    # No enterprise feature packs are installed in this deployment —
    # that's fine, the registry simply stays empty.
    pass

from core.enterprise_apps import include_enterprise_apps

include_enterprise_apps(app)

apply_response_documentation(app)
