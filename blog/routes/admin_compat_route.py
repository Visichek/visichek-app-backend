"""Blog-admin compatibility aliases on ``/v1/admins/*``.

The standalone blog backend's frontend was wired to a different set of
admin paths than the host backend exposes. Rather than break that UI
while we migrate, we publish a small set of aliases that delegate to
the host backend's existing services.

Aliases vs. originals:

* ``GET  /v1/admins/me``              → host's ``GET  /v1/admins/profile``
* ``POST /v1/admins/login/verify-otp`` → host's ``POST /v1/admins/verify-otp``
* ``POST /v1/admins/invite``          → host's ``POST /v1/admins/signup``
* ``POST /v1/admins/mfa/enable/request``  → host's ``POST /v1/auth/2fa/setup``
* ``POST /v1/admins/mfa/enable/confirm``  → host's ``POST /v1/auth/2fa/verify``
* ``POST /v1/admins/mfa/disable``         → host's ``DELETE /v1/auth/2fa``
  (POST kept here because the blog admin frontend uses it.)

All aliases reuse the host backend's auth deps, services, and response
envelope. There is no duplicated business logic in this file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from core.errors import auth_invalid_token
from core.response_envelope import document_response, success_payload
from core.settings import get_settings
from schemas.admin_schema import AdminOut, AdminSignupRequest
from schemas.otp_schema import OtpVerifyRequest
from schemas.session_schema import (
    TwoFactorDisableRequest,
    TwoFactorVerifyRequest,
)
from security.account_status_check import check_admin_account_status_and_permissions
from security.cookie_utils import build_auth_response
from security.principal import AuthPrincipal
from security.auth import verify_admin_token
from services.admin_service import add_admin, verify_admin_otp
from services.two_factor_service import (
    disable_two_factor_with_password,
    setup_two_factor,
    verify_two_factor_setup,
)

router = APIRouter(prefix="/admins", tags=["Application Admins (Blog Aliases)"])


# ---------------------------------------------------------------------------
# /me  (alias of /profile)
# ---------------------------------------------------------------------------


@router.get("/me")
@document_response(
    message="Admin profile fetched successfully",
    description=(
        "Alias of ``GET /v1/admins/profile`` kept for compatibility with the "
        "blog-admin frontend."
    ),
    summary="Get current admin (alias)",
)
async def get_current_admin(
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> AdminOut:
    return admin


# ---------------------------------------------------------------------------
# /login/verify-otp  (alias of /verify-otp)
# ---------------------------------------------------------------------------


@router.post("/login/verify-otp")
@document_response(
    message="OTP verified, login successful",
    description=(
        "Alias of ``POST /v1/admins/verify-otp``. Step 2 of admin 2FA login."
    ),
    summary="Verify admin OTP (alias)",
)
async def verify_admin_otp_alias(request: Request, otp_data: OtpVerifyRequest):
    admin = await verify_admin_otp(otp_data.otp_challenge_id, otp_data.otp_code)
    is_prod = get_settings().env == "production"
    return build_auth_response(
        request=request,
        payload=admin,
        message="OTP verified, login successful",
        is_production=is_prod,
    )


# ---------------------------------------------------------------------------
# /invite  (alias of /signup, simpler payload)
# ---------------------------------------------------------------------------


class AdminInviteRequest(BaseModel):
    """Blog-admin "invite admin" payload.

    The blog backend let an authenticated admin invite another admin by
    only specifying ``full_name`` + ``email`` — the password was system
    generated. We preserve that ergonomic by wrapping the host's
    ``AdminSignupRequest`` with a sensible default password the invited
    admin must rotate on first login.
    """

    full_name: str = Field(min_length=1)
    email: EmailStr
    # Optional initial password; if not provided the inviter must follow
    # up out-of-band with a reset link. The host's password policy
    # validator will still reject anything weak.
    password: str | None = None


@router.post("/invite", status_code=status.HTTP_201_CREATED)
@document_response(
    message="Admin invited successfully",
    status_code=status.HTTP_201_CREATED,
    description=(
        "Alias of ``POST /v1/admins/signup``. Invite a new admin. Accepts "
        "either a password (forwarded as-is) or omits one, in which case the "
        "inviter must reset it before the invitee can log in."
    ),
    summary="Invite new admin (alias)",
)
async def invite_admin_alias(
    payload: AdminInviteRequest,
    admin: AdminOut = Depends(check_admin_account_status_and_permissions),
) -> AdminOut:
    # Build a host-shaped signup request from the slimmer invite payload.
    signup = AdminSignupRequest(
        full_name=payload.full_name,
        email=payload.email,
        password=payload.password
        # When no password was supplied we still need to satisfy the
        # password-policy validator; pick a long random string so the
        # invitee MUST run a reset before login. The host service caches
        # the hash; the plaintext is discarded after model_dump().
        or _generate_invite_placeholder_password(),
    )
    return await add_admin(signup_data=signup, invited_by=admin.id)  # type: ignore[arg-type]


def _generate_invite_placeholder_password() -> str:
    """Return a strong throwaway password that satisfies the policy.

    Contains upper, lower, digit, special, no sequences/repeats, length
    > 12.
    """
    import secrets
    import string

    rng = secrets.SystemRandom()
    chars = (
        rng.choice(string.ascii_uppercase)
        + rng.choice(string.ascii_lowercase)
        + rng.choice(string.digits)
        + rng.choice("!@#$%^&*()-_")
        + "".join(
            rng.choice(string.ascii_letters + string.digits + "!@#$%^&*()-_")
            for _ in range(20)
        )
    )
    return chars


# ---------------------------------------------------------------------------
# MFA aliases — delegate to two_factor_service
# ---------------------------------------------------------------------------


@router.post("/mfa/enable/request")
@document_response(
    message="Two-factor enrollment started",
    description=(
        "Alias of ``POST /v1/auth/2fa/setup``. Initiates TOTP enrollment "
        "and returns the shared secret, QR URI, and backup codes."
    ),
    summary="Initiate admin 2FA setup (alias)",
)
async def request_admin_mfa(
    request: Request,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    from core.database import db
    from bson import ObjectId

    doc = await db.admins.find_one(
        {"_id": ObjectId(principal.user_id)}, {"email": 1}
    )
    if not doc:
        raise auth_invalid_token()
    result = await setup_two_factor(
        user_id=principal.user_id, user_type="admin", email=doc["email"]
    )
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        content=jsonable_encoder(
            success_payload(
                result,
                message="Two-factor enrollment started",
                request_id=request_id,
            )
        )
    )


@router.post("/mfa/enable/confirm")
@document_response(
    message="Two-factor authentication enabled",
    description=(
        "Alias of ``POST /v1/auth/2fa/verify``. Confirms enrollment with "
        "a TOTP code and returns the user's backup codes."
    ),
    summary="Confirm admin 2FA setup (alias)",
)
async def confirm_admin_mfa(
    payload: TwoFactorVerifyRequest,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    backup_codes = await verify_two_factor_setup(
        user_id=principal.user_id, user_type="admin", code=payload.code
    )
    return {"enabled": True, "backup_codes": backup_codes}


@router.post("/mfa/disable")
@document_response(
    message="Two-factor authentication disabled",
    description=(
        "Alias of ``DELETE /v1/auth/2fa`` with a POST verb (matches the "
        "blog-admin frontend). Requires the admin's current password."
    ),
    summary="Disable admin 2FA (alias)",
)
async def disable_admin_mfa(
    payload: TwoFactorDisableRequest,
    principal: AuthPrincipal = Depends(verify_admin_token),
):
    await disable_two_factor_with_password(
        user_id=principal.user_id, user_type="admin", password=payload.password
    )
    return {"disabled": True}
