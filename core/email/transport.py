from __future__ import annotations

import inspect
import logging
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

from core.email.types import EmailMessage


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_email: str


class SMTPTransport:
    def __init__(
        self, config: SmtpConfig, logger: logging.Logger | None = None
    ) -> None:
        self._config = config
        self._logger = logger or logging.getLogger(__name__)

    def send_message(
        self, message: EmailMessage, *, idempotency_key: str | None = None
    ) -> None:
        # ``idempotency_key`` is part of the shared EmailTransport contract but
        # SMTP has no idempotency concept, so it is intentionally ignored here.
        del idempotency_key
        formatted_from = formataddr(
            (message.sender_display_name, self._config.from_email)
        )

        payload = MIMEMultipart("alternative")
        payload["From"] = formatted_from
        payload["To"] = message.to_email
        payload["Subject"] = message.subject
        payload.attach(MIMEText(message.text_body, "plain"))
        payload.attach(MIMEText(message.html_body, "html"))

        server: smtplib.SMTP | smtplib.SMTP_SSL | None = None
        try:
            if self._config.port == 465:
                server = smtplib.SMTP_SSL(self._config.host, self._config.port)
            elif self._config.port in (25, 587):
                server = smtplib.SMTP(self._config.host, self._config.port)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                raise ValueError("Unsupported SMTP port. Use 465, 587, or 25.")

            server.login(self._config.username, self._config.password)
            server.sendmail(
                self._config.from_email, message.to_email, payload.as_string()
            )
            self._logger.info("Email sent to %s", message.to_email)
        finally:
            if server is not None:
                server.quit()


@dataclass(frozen=True)
class ResendConfig:
    api_key: str
    # The ``from`` address. Its domain MUST be a verified domain on the Resend
    # account (or the ``onboarding@resend.dev`` sandbox sender), otherwise the
    # API rejects the send with HTTP 403.
    from_email: str


def _import_resend() -> Any:
    """Import the optional ``resend`` SDK with a clear, actionable error.

    Imported lazily so the email package keeps loading for SMTP-only
    deployments that never install the SDK; only selecting the Resend provider
    (or sending through it) pulls the dependency in.
    """
    try:
        import resend  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised only when unset
        raise RuntimeError(
            "EMAIL_PROVIDER=resend requires the 'resend' package. "
            "Install it with: pip install 'resend>=2.21.0'"
        ) from exc
    return resend


class ResendTransport:
    """Send transactional email through the Resend HTTPS API.

    A drop-in alternative to :class:`SMTPTransport` — same
    ``send_message`` contract, so ``EmailManager`` treats them identically.
    Resend's Python SDK is synchronous (a blocking HTTP call); the manager
    already runs ``send_message`` inside ``asyncio.to_thread`` so it never
    blocks the event loop. The SDK *raises* on API errors, which propagate up
    into the manager's existing retry loop.
    """

    # How the installed SDK accepts the idempotency key, resolved once.
    _IDEMP_OPTIONS = "options"  # send(params, {"idempotency_key": ...})
    _IDEMP_KWARG = "kwarg"  # send(params, idempotency_key=...)
    _IDEMP_NONE = "none"  # this build exposes no idempotency parameter

    def __init__(
        self, config: ResendConfig, logger: logging.Logger | None = None
    ) -> None:
        self._config = config
        self._logger = logger or logging.getLogger(__name__)
        # Import the SDK at construction — i.e. only once Resend is the chosen
        # provider — so a missing/broken install fails clearly at app startup
        # instead of on the first user-triggered email. SMTP-only deployments
        # never construct this class, so they never import the SDK.
        self._resend = _import_resend()
        # Resolve HOW this SDK build carries the idempotency key once, off the
        # per-send path. Current releases (2.x) take ``send(params, options)``;
        # some builds use an ``idempotency_key=`` kwarg.
        send_params = inspect.signature(self._resend.Emails.send).parameters
        if "options" in send_params:
            self._idemp_style = self._IDEMP_OPTIONS
        elif "idempotency_key" in send_params:
            self._idemp_style = self._IDEMP_KWARG
        else:
            self._idemp_style = self._IDEMP_NONE
            self._logger.warning(
                "Resend SDK send() exposes no idempotency parameter; retries "
                "will not be de-duplicated. Consider upgrading 'resend'."
            )

    def send_message(
        self, message: EmailMessage, *, idempotency_key: str | None = None
    ) -> None:
        # The SDK reads the API key off its module global; set it on each send
        # so the right key is active at call time. Safe for the single-config
        # EmailManager singleton this app builds — a future multi-key setup
        # would need a lock around the set + send.
        self._resend.api_key = self._config.api_key

        params: dict[str, Any] = {
            "from": formataddr((message.sender_display_name, self._config.from_email)),
            "to": [message.to_email],
            "subject": message.subject,
            "html": message.html_body,
            "text": message.text_body,
        }

        # Reuse the idempotency key across retries of the SAME send so a retry
        # never double-delivers (see EmailManager.send_message).
        if idempotency_key and self._idemp_style == self._IDEMP_OPTIONS:
            result = self._resend.Emails.send(
                params, {"idempotency_key": idempotency_key}
            )
        elif idempotency_key and self._idemp_style == self._IDEMP_KWARG:
            result = self._resend.Emails.send(params, idempotency_key=idempotency_key)
        else:
            result = self._resend.Emails.send(params)

        if isinstance(result, dict):
            email_id = result.get("id")
        else:
            email_id = getattr(result, "id", None)
        self._logger.info(
            "Email sent to %s via Resend (id=%s)", message.to_email, email_id
        )
