"""Security regression tests for the 2026-05-25 hardening pass.

These are deliberately pure unit tests (no live Mongo / Redis / ASGI loop)
so they stay fast and deterministic. Each maps to a finding from the
security review:

  * Flutterwave webhook fail-closed verification (P1)
  * Production security-posture gate (Goal 1)
  * Tenant-user account-status enforcement at the verifier (P1)
  * KYC public capability tokens (P2)
  * CSRF / Origin gate for cookie-authenticated writes (defense-in-depth)
  * Public route inventory — no unauthenticated visitor-data endpoints (P0)
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.errors import AppException
from core.settings import get_settings


# ---------------------------------------------------------------------------
# Flutterwave webhook — fail closed
# ---------------------------------------------------------------------------


class TestFlutterwaveWebhookVerification:
    def _provider(self, secret):
        from core.payments.flutterwave_provider import FlutterwavePaymentProvider

        return FlutterwavePaymentProvider(secret_key="sk", webhook_secret_hash=secret)

    def test_missing_secret_rejected(self):
        with pytest.raises(AppException) as exc:
            self._provider(None).verify_webhook(
                body=b"{}", headers={"verif-hash": "anything"}
            )
        assert exc.value.status_code == 401

    def test_wrong_signature_rejected(self):
        with pytest.raises(AppException) as exc:
            self._provider("good").verify_webhook(
                body=b"{}", headers={"verif-hash": "bad"}
            )
        assert exc.value.status_code == 401

    def test_missing_header_rejected(self):
        with pytest.raises(AppException):
            self._provider("good").verify_webhook(body=b"{}", headers={})

    def test_valid_signature_accepted(self):
        event = self._provider("good").verify_webhook(
            body=b'{"id": 1, "event": "charge.completed"}',
            headers={"verif-hash": "good"},
        )
        assert event.event_type == "charge.completed"


# ---------------------------------------------------------------------------
# Security posture gate
# ---------------------------------------------------------------------------


def _prod_settings(**overrides):
    base = get_settings()
    defaults = dict(
        env="production",
        secret_key="s",
        session_secret_key="s",
        qr_signing_secret="s",
        id_number_encryption_key="k",
        storage_backend="s3",
        allow_local_storage_in_production=False,
        payment_default_provider="stripe",
        payment_app_mode_enabled=False,
        stripe_secret_key="sk",
        stripe_webhook_secret="wh",
        flutterwave_secret_key=None,
        paystack_secret_key=None,
        otp_dev_code="not-the-default-999",
    )
    defaults.update(overrides)
    return dataclasses.replace(base, **defaults)


class TestSecurityPosture:
    def test_good_production_config_has_no_violations(self):
        from core.security_posture import evaluate_security_posture

        assert evaluate_security_posture(_prod_settings()) == []

    def test_missing_signing_secrets_flagged(self):
        from core.security_posture import evaluate_security_posture

        v = evaluate_security_posture(
            _prod_settings(secret_key="", session_secret_key="", qr_signing_secret="")
        )
        joined = " ".join(v)
        assert "SECRET_KEY" in joined
        assert "SESSION_SECRET_KEY" in joined
        assert "QR_SIGNING_SECRET" in joined

    def test_flutterwave_without_webhook_secret_flagged(self):
        from core.security_posture import evaluate_security_posture

        v = evaluate_security_posture(_prod_settings(flutterwave_secret_key="fk"))
        assert any("FLW_WEBHOOK_SECRET_HASH" in x for x in v)

    def test_app_provider_default_flagged(self):
        from core.security_posture import evaluate_security_posture

        v = evaluate_security_posture(_prod_settings(payment_default_provider="app"))
        assert any("SIMULATOR" in x for x in v)

    def test_local_storage_flagged(self):
        from core.security_posture import evaluate_security_posture

        v = evaluate_security_posture(_prod_settings(storage_backend="local"))
        assert any("STORAGE_BACKEND=local" in x for x in v)

    def test_default_otp_flagged(self):
        from core.security_posture import evaluate_security_posture

        v = evaluate_security_posture(_prod_settings(otp_dev_code="123456"))
        assert any("OTP_DEV_CODE" in x for x in v)

    def test_assert_raises_in_production(self):
        from core.security_posture import assert_security_posture

        with pytest.raises(RuntimeError):
            assert_security_posture(_prod_settings(secret_key=""))

    def test_assert_only_warns_outside_production(self):
        from core.security_posture import assert_security_posture

        # env != production with violations → must NOT raise.
        assert_security_posture(_prod_settings(env="development", secret_key=""))


# ---------------------------------------------------------------------------
# Tenant-user account-status enforcement at the verifier
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, path: str = "/v1/some/protected"):
        self.scope: dict = {}
        self.url = type("U", (), {"path": path})()


def _principal(role: str = "receptionist"):
    from security.principal import AuthPrincipal

    return AuthPrincipal(
        user_id="0" * 24,
        role=role,  # type: ignore[arg-type]
        access_token_id="acc",
        jwt_token="jwt",
        tenant_id="tenant-1",
    )


class TestAccountStatusGate:
    @pytest.mark.asyncio
    async def test_inactive_tenant_user_rejected(self, monkeypatch):
        import security.auth as auth

        async def fake_flags(user_id, *, collection):
            return {"account_status": "INACTIVE"}

        monkeypatch.setattr(auth, "_fetch_account_flags", fake_flags)
        with pytest.raises(AppException) as exc:
            await auth._enforce_account_gates(_FakeRequest(), _principal())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_active_tenant_user_passes(self, monkeypatch):
        import security.auth as auth

        async def fake_flags(user_id, *, collection):
            return {"account_status": "ACTIVE"}

        monkeypatch.setattr(auth, "_fetch_account_flags", fake_flags)
        # Should not raise.
        await auth._enforce_account_gates(_FakeRequest(), _principal())

    @pytest.mark.asyncio
    async def test_missing_status_treated_as_active(self, monkeypatch):
        import security.auth as auth

        async def fake_flags(user_id, *, collection):
            return {}  # legacy row / DB blip → fail open to active

        monkeypatch.setattr(auth, "_fetch_account_flags", fake_flags)
        await auth._enforce_account_gates(_FakeRequest(), _principal())

    @pytest.mark.asyncio
    async def test_must_change_password_blocks_normal_path(self, monkeypatch):
        import security.auth as auth

        async def fake_flags(user_id, *, collection):
            return {"account_status": "ACTIVE", "must_change_password": True}

        monkeypatch.setattr(auth, "_fetch_account_flags", fake_flags)
        with pytest.raises(AppException):
            await auth._enforce_account_gates(_FakeRequest(), _principal())

    @pytest.mark.asyncio
    async def test_must_change_password_allows_change_endpoint(self, monkeypatch):
        import security.auth as auth

        async def fake_flags(user_id, *, collection):
            return {"account_status": "ACTIVE", "must_change_password": True}

        monkeypatch.setattr(auth, "_fetch_account_flags", fake_flags)
        await auth._enforce_account_gates(
            _FakeRequest("/v1/auth/change-password"), _principal()
        )


# ---------------------------------------------------------------------------
# KYC public capability tokens
# ---------------------------------------------------------------------------


class TestCheckinCapabilityToken:
    def test_roundtrip_valid(self):
        from services.qr_service import (
            sign_checkin_capability,
            verify_checkin_capability,
        )

        token = sign_checkin_capability("tenant-1", "checkin-123")
        assert verify_checkin_capability(token, checkin_id="checkin-123") is True

    def test_wrong_checkin_id_rejected(self):
        from services.qr_service import (
            sign_checkin_capability,
            verify_checkin_capability,
        )

        token = sign_checkin_capability("tenant-1", "checkin-123")
        assert verify_checkin_capability(token, checkin_id="other") is False

    def test_tampered_token_rejected(self):
        from services.qr_service import (
            sign_checkin_capability,
            verify_checkin_capability,
        )

        token = sign_checkin_capability("tenant-1", "checkin-123")
        # Flip a character early in the token so the decoded payload/signature
        # actually changes (appending after base64 padding would be ignored).
        idx = 4
        flipped = "B" if token[idx] != "B" else "C"
        tampered = token[:idx] + flipped + token[idx + 1 :]
        assert verify_checkin_capability(tampered, checkin_id="checkin-123") is False

    def test_empty_token_rejected(self):
        from services.qr_service import verify_checkin_capability

        assert verify_checkin_capability("", checkin_id="checkin-123") is False

    def test_expired_token_rejected(self):
        from services.qr_service import (
            sign_checkin_capability,
            verify_checkin_capability,
        )

        token = sign_checkin_capability("tenant-1", "checkin-123", ttl_seconds=-1)
        assert verify_checkin_capability(token, checkin_id="checkin-123") is False


# ---------------------------------------------------------------------------
# CSRF / Origin gate
# ---------------------------------------------------------------------------


class TestCsrfDecision:
    ALLOWED = {"https://app.visichek.app"}

    def _call(self, **kw):
        from core.csrf import is_csrf_violation

        params = dict(
            method="POST",
            has_auth_cookie=True,
            has_auth_header=False,
            origin="https://app.visichek.app",
            referer=None,
            allowed_origins=self.ALLOWED,
        )
        params.update(kw)
        return is_csrf_violation(**params)

    def test_safe_method_never_violation(self):
        assert self._call(method="GET", origin=None) is False

    def test_bearer_auth_exempt(self):
        assert self._call(has_auth_header=True, origin=None) is False

    def test_anonymous_exempt(self):
        assert self._call(has_auth_cookie=False, origin=None) is False

    def test_cookie_write_good_origin_ok(self):
        assert self._call() is False

    def test_cookie_write_bad_origin_rejected(self):
        assert self._call(origin="https://evil.example") is True

    def test_cookie_write_missing_origin_rejected(self):
        assert self._call(origin=None, referer=None) is True

    def test_referer_fallback_used(self):
        assert self._call(origin=None, referer="https://app.visichek.app/page") is False


# ---------------------------------------------------------------------------
# Public route inventory — no unauthenticated visitor-data endpoints
# ---------------------------------------------------------------------------


def _auth_dependency_callables():
    import security.account_status_check as gate
    import security.auth as auth

    return {
        auth.verify_any_token,
        auth.verify_user_token,
        auth.verify_admin_token,
        auth.verify_super_admin_token,
        auth.verify_any_system_user_token,
        auth.verify_receptionist_token,
        auth.verify_tenant_form_configure_token,
        auth.verify_system_user_token,
        gate.check_admin_account_status_and_permissions,
        gate.check_user_account_status_and_permissions,
    }


def _route_has_auth(route, auth_calls) -> bool:
    """Walk a route's dependant tree for any known auth dependency."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    stack = [dependant]
    while stack:
        dep = stack.pop()
        if getattr(dep, "call", None) in auth_calls:
            return True
        stack.extend(getattr(dep, "dependencies", []) or [])
    return False


class TestPublicRouteInventory:
    def test_visitor_verification_endpoints_require_auth(self):
        from main import app

        auth_calls = _auth_dependency_callables()
        sensitive = [
            r
            for r in app.routes
            if getattr(r, "path", "").startswith("/v1/visitor-verification")
        ]
        assert sensitive, "visitor-verification routes not found"
        for route in sensitive:
            assert _route_has_auth(route, auth_calls), (
                f"{getattr(route, 'path', '?')} must require authentication"
            )


# ---------------------------------------------------------------------------
# Route-name audit — test/debug/simulator routes must be explicitly guarded
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for sep in ("/", "-", "_", " ", "."):
        text = text.replace(sep, " ")
    for tok in text.split():
        out.add(tok.lower())
    return out


class TestRouteNameAudit:
    # Prefixes allowed to carry test/debug/simulator wording WITHOUT an auth
    # dependency because they have an explicit environment guard instead.
    # app-checkout: include_in_schema=False + _guard_app_mode() returns 404 in
    # prod, and the provider isn't even registered in production.
    _ENV_GUARDED_PREFIXES = ("/payments/app-checkout",)
    _KEYWORDS = {"test", "debug", "simulator"}

    def test_no_unguarded_test_debug_simulator_routes(self):
        from main import app

        auth_calls = _auth_dependency_callables()
        offenders = []
        for route in app.routes:
            path = getattr(route, "path", "") or ""
            name = getattr(route, "name", "") or ""
            summary = getattr(route, "summary", "") or ""
            tags = " ".join(getattr(route, "tags", []) or [])
            toks = _tokens(f"{path} {name} {summary} {tags}")
            if not (toks & self._KEYWORDS):
                continue
            # A keyword route is fine if it requires auth OR has an explicit
            # environment guard (allowlisted prefix). Otherwise it is an
            # unguarded public test/debug surface — exactly what Goal 2 bans.
            if _route_has_auth(route, auth_calls):
                continue
            if any(path.startswith(p) for p in self._ENV_GUARDED_PREFIXES):
                continue
            offenders.append(f"{path} (name={name})")
        assert not offenders, (
            "Unguarded test/debug/simulator routes mounted: " + ", ".join(offenders)
        )


# ---------------------------------------------------------------------------
# Payment webhook signature suites
# ---------------------------------------------------------------------------


class TestPaystackWebhook:
    SECRET = "sk_test_paystack_secret"

    def _provider(self):
        from core.payments.paystack_provider import PaystackPaymentProvider

        return PaystackPaymentProvider(secret_key=self.SECRET)

    def _sig(self, body: bytes) -> str:
        return hmac.new(self.SECRET.encode(), body, hashlib.sha512).hexdigest()

    def test_missing_signature_rejected(self):
        with pytest.raises(AppException) as exc:
            self._provider().verify_webhook(body=b"{}", headers={})
        assert exc.value.status_code == 401

    def test_wrong_signature_rejected(self):
        with pytest.raises(AppException) as exc:
            self._provider().verify_webhook(
                body=b"{}", headers={"x-paystack-signature": "deadbeef"}
            )
        assert exc.value.status_code == 401

    def test_valid_signature_accepted(self):
        body = b'{"event": "charge.success", "data": {"reference": "r1"}}'
        event = self._provider().verify_webhook(
            body=body, headers={"x-paystack-signature": self._sig(body)}
        )
        assert event.event_type == "charge.success"


class TestStripeWebhook:
    def _provider(self):
        pytest.importorskip("stripe")
        from core.payments.stripe_provider import StripePaymentProvider

        return StripePaymentProvider(secret_key="sk_test", webhook_secret="whsec_x")

    def test_missing_signature_rejected(self):
        with pytest.raises(AppException) as exc:
            self._provider().verify_webhook(body=b"{}", headers={})
        assert exc.value.status_code == 401

    def test_missing_secret_rejected(self):
        pytest.importorskip("stripe")
        from core.payments.stripe_provider import StripePaymentProvider

        provider = StripePaymentProvider(secret_key="sk_test", webhook_secret=None)
        with pytest.raises(AppException) as exc:
            provider.verify_webhook(
                body=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
            )
        assert exc.value.status_code == 401

    def test_invalid_signature_rejected(self):
        with pytest.raises(AppException) as exc:
            self._provider().verify_webhook(
                body=b"{}", headers={"stripe-signature": "t=1,v1=bogus"}
            )
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Flutterwave high-value verification gate + duplicate-event idempotency
# ---------------------------------------------------------------------------


def _fake_tx(status):
    from core.payments.types import PaymentProviderName, PaymentTransaction

    return PaymentTransaction(
        provider=PaymentProviderName.FLUTTERWAVE,
        reference="tx_ref_1",
        status=status,
        raw={},
    )


class TestFlutterwaveVerificationGate:
    def _fake_manager(self, *, has_provider: bool, tx_status=None):
        from core.payments.types import PaymentStatus

        provider = MagicMock()
        provider.fetch_transaction.return_value = _fake_tx(
            tx_status or PaymentStatus.SUCCEEDED
        )
        manager = MagicMock()
        manager.has_provider.return_value = has_provider
        manager.get_provider.return_value = provider
        return manager

    def test_unverified_transaction_not_trusted(self):
        from core.payments.types import PaymentStatus
        import services.flutterwave_webhook_service as svc

        with patch.object(
            svc.PaymentManager,
            "get_instance",
            return_value=self._fake_manager(
                has_provider=True, tx_status=PaymentStatus.PENDING
            ),
        ):
            assert svc._flutterwave_transaction_verified("tx_ref_1") is False

    def test_verified_transaction_trusted(self):
        import services.flutterwave_webhook_service as svc

        with patch.object(
            svc.PaymentManager,
            "get_instance",
            return_value=self._fake_manager(has_provider=True),
        ):
            assert svc._flutterwave_transaction_verified("tx_ref_1") is True

    def test_no_provider_falls_back_to_signature_trust(self):
        import services.flutterwave_webhook_service as svc

        with patch.object(
            svc.PaymentManager,
            "get_instance",
            return_value=self._fake_manager(has_provider=False),
        ):
            assert svc._flutterwave_transaction_verified("tx_ref_1") is True


class TestFlutterwaveDuplicateEvent:
    @pytest.mark.asyncio
    async def test_duplicate_event_rejected(self):
        import services.flutterwave_webhook_service as svc

        provider = MagicMock()
        provider.verify_webhook.return_value = MagicMock(
            event_id="evt-1", event_type="charge.completed"
        )
        manager = MagicMock()
        manager.get_provider.return_value = provider

        with (
            patch.object(svc.PaymentManager, "get_instance", return_value=manager),
            patch.object(svc, "is_webhook_processed", new=AsyncMock(return_value=True)),
            patch.object(svc, "_record_webhook_event", new=AsyncMock()),
        ):
            with pytest.raises(AppException) as exc:
                await svc.process_flutterwave_webhook(body=b"{}", headers={})
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# OAuth callback must not leak tokens in the redirect URL
# ---------------------------------------------------------------------------


class TestOAuthNoTokenLeak:
    @pytest.mark.asyncio
    async def test_callback_uses_cookies_not_query(self):
        import api.v1.user_route as ur

        fake_oauth = MagicMock()
        fake_oauth.google.authorize_access_token = AsyncMock(
            return_value={
                "userinfo": {
                    "name": "Alice",
                    "given_name": "Smith",
                    "email": "alice@example.com",
                }
            }
        )
        fake_user = MagicMock()
        fake_user.access_token = "ACCESS_TOKEN_VALUE"
        fake_user.refresh_token = "REFRESH_TOKEN_VALUE"

        with (
            patch.object(ur, "oauth", fake_oauth),
            patch.object(
                ur, "authenticate_user_google", new=AsyncMock(return_value=fake_user)
            ),
        ):
            response = await ur.auth_callback_user(request=MagicMock())

        location = response.headers["location"]
        assert "access_token" not in location
        assert "refresh_token" not in location
        assert "ACCESS_TOKEN_VALUE" not in location
        # Tokens are delivered via Set-Cookie instead of the URL.
        set_cookies = " ".join(
            v.decode() for k, v in response.raw_headers if k == b"set-cookie"
        )
        assert "access_token=" in set_cookies
        assert response.headers.get("referrer-policy") == "no-referrer"


# ---------------------------------------------------------------------------
# Deactivated system user cannot refresh into new tokens
# ---------------------------------------------------------------------------


class TestDeactivatedUserRefresh:
    @pytest.mark.asyncio
    async def test_inactive_user_refresh_rejected(self):
        import services.system_user_service as svc
        from schemas.imports import AccountStatus
        from schemas.system_user_schema import SystemUserRefresh

        refresh_obj = MagicMock()
        refresh_obj.previousAccessToken = "expired-access-id"
        refresh_obj.userId = "0" * 24

        inactive_user = MagicMock()
        inactive_user.id = "0" * 24
        inactive_user.account_status = AccountStatus.INACTIVE

        with (
            patch.object(
                svc, "get_refresh_tokens", new=AsyncMock(return_value=refresh_obj)
            ),
            patch.object(
                svc, "get_system_user", new=AsyncMock(return_value=inactive_user)
            ),
            patch.object(svc, "delete_access_token", new=AsyncMock()),
            patch.object(svc, "delete_refresh_token", new=AsyncMock()),
            patch.object(
                svc, "delete_all_tokens_with_user_id", new=AsyncMock()
            ) as mock_revoke,
        ):
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc:
                await svc.refresh_system_user_tokens(
                    SystemUserRefresh(refresh_token="rt"),
                    expired_access_token="expired-access-id",
                )
            assert exc.value.status_code == 403
            mock_revoke.assert_awaited()  # all tokens torn down
