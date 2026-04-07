from __future__ import annotations

import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import redis
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from limits.storage import RedisStorage
from limits.strategies import FixedWindowRateLimiter
from pymongo import MongoClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from celery_worker import celery_app
from core.email.manager import EmailManager
from core.payments.manager import PaymentManager
from core.queue.celery_provider import CeleryQueueProvider
from core.queue.manager import QueueManager
from core.response_envelope import (
    apply_response_documentation,
    document_response,
    error_response,
    http_exception_response,
)
from core.scheduler import scheduler
from core.role_config import build_role_rate_limits, build_role_rate_limits_csv, normalize_role
from core.settings import get_settings
from core.storage.manager import DocumentStorageManager
from repositories.tokens_repo import get_access_token_allow_expired

settings = get_settings()

MONGO_URI = os.getenv("MONGO_URL")
mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000) if MONGO_URI else None
redis_client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, decode_responses=True)


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


ROLE_RATE_LIMITS_DEFAULT = build_role_rate_limits_csv(non_admin_roles=["user"])
RATE_LIMITS = build_role_rate_limits(
    os.getenv("ROLE_RATE_LIMITS"),
    fallback_csv=ROLE_RATE_LIMITS_DEFAULT,
)

storage = RedisStorage(settings.redis_url)
limiter = FixedWindowRateLimiter(storage)


async def get_user_type(request: Request) -> tuple[str, str]:
    auth_header = request.headers.get("Authorization")
    fallback_id = request.headers.get("X-Forwarded-For") or request.client.host # type: ignore

    if not auth_header or not auth_header.startswith("Bearer "):
        return fallback_id, "anonymous"

    token = auth_header.split(" ", maxsplit=1)[1]
    access_token = await get_access_token_allow_expired(accessToken=token)
    if not access_token:
        return fallback_id, "anonymous"

    user_type = normalize_role(access_token.role or "anonymous")
    if user_type not in RATE_LIMITS:
        user_type = "anonymous"

    return access_token.userId, user_type


class RateLimitingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
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

    # Configure OCR manager (optional - only if credentials are provided)
    try:
        from core.ocr.manager import OCRManager
        OCRManager.configure_from_settings()
    except RuntimeError:
        pass

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

    try:
        yield
    finally:
        scheduler.shutdown()


from core.case_conversion import CaseConversionMiddleware
from core.plan_enforcement import PlanEnforcementMiddleware

app = FastAPI(lifespan=lifespan, title="REST API")
app.add_middleware(CaseConversionMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key or "dev-only-session-secret")
app.add_middleware(PlanEnforcementMiddleware)
app.add_middleware(RateLimitingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins) if settings.cors_origins else ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    return http_exception_response(exc=exc, request=request)


@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(request: Request, exc: RequestValidationError):
    return error_response(
        status_code=422,
        message="Validation error",
        data={"code": "VALIDATION_FAILED", "details": {"errors": exc.errors()}},
        request_id=getattr(request.state, "request_id", None),
    )


@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    details = str(exc) if (settings.debug_include_error_details and not settings.is_production) else None
    return error_response(
        status_code=500,
        message="Internal Server Error",
        data={"code": "INTERNAL_ERROR", "details": details},
        request_id=getattr(request.state, "request_id", None),
    )


@app.get("/", tags=["Health"], include_in_schema=False)
@document_response(
    message="Successfully fetched data",
    success_example={"message": "Hello from FasterAPI!"},
)
def read_root(request: Request):
    return {"message": "Hello from FasterAPI!", "request_id": getattr(request.state, "request_id", None)}


@app.get("/health", tags=["Health"])
@document_response(
    message="Health check completed",
    success_example={"status": "healthy", "services": {"mongo": "healthy", "redis": "healthy"}},
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
        age = time.time() - float(aps_heartbeat.decode('utf-8')) # type: ignore
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

    return {
        "status": overall_status,
        "timestamp": datetime.utcnow().isoformat(),
        "services": services,
    }


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

app.include_router(v1_admin_route_router, prefix='/v1')
app.include_router(v1_documents_route_router, prefix='/v1')
app.include_router(v1_payments_route_router, prefix='/v1')
app.include_router(v1_user_route_router, prefix='/v1')
app.include_router(v1_tenant_route_router, prefix='/v1')
app.include_router(v1_department_route_router, prefix='/v1')
app.include_router(v1_system_user_route_router, prefix='/v1')
app.include_router(v1_visitor_route_router, prefix='/v1')
app.include_router(v1_visitor_profile_route_router, prefix='/v1')
app.include_router(v1_appointment_route_router, prefix='/v1')
app.include_router(v1_privacy_notice_route_router, prefix='/v1')
app.include_router(v1_dashboard_route_router, prefix='/v1')
app.include_router(v1_super_admin_route_router, prefix='/v1')
app.include_router(v1_dsr_route_router, prefix='/v1')
app.include_router(v1_retention_route_router, prefix='/v1')
app.include_router(v1_sub_processor_route_router, prefix='/v1')
app.include_router(v1_compliance_route_router, prefix='/v1')
app.include_router(v1_audit_route_router, prefix='/v1')
app.include_router(v1_incident_route_router, prefix='/v1')
app.include_router(v1_plan_route_router, prefix='/v1')
app.include_router(v1_subscription_route_router, prefix='/v1')
app.include_router(v1_discount_route_router, prefix='/v1')
app.include_router(v1_usage_route_router, prefix='/v1')
app.include_router(v1_admin_dashboard_route_router, prefix='/v1')
app.include_router(v1_branch_route_router, prefix='/v1')
app.include_router(v1_branding_route_router, prefix='/v1')
# --- auto-routes-end ---

apply_response_documentation(app)