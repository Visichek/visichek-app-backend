from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

TemplateRenderer = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class MountedTemplate:
    key: str
    subject: str
    render_html: TemplateRenderer
    render_text: TemplateRenderer


@dataclass(frozen=True)
class EmailDispatchRequest:
    to_email: str
    template_key: str
    context: Mapping[str, Any] = field(default_factory=dict)
    dispatch: Literal["auto", "sync", "queued"] = "auto"


@dataclass(frozen=True)
class EmailMessage:
    to_email: str
    subject: str
    html_body: str
    text_body: str
    sender_display_name: str


@dataclass(frozen=True)
class EmailSendResult:
    # ``skipped`` = recipient is a test-domain address (non-production
    # only) and the send was suppressed — see core/test_mode.py.
    status: Literal["sent", "queued", "skipped"]
    attempts: int
    task_id: str | None = None


class EmailTransport(Protocol):
    """Structural contract every email transport satisfies.

    ``SMTPTransport`` and ``ResendTransport`` both implement this, so
    ``EmailManager`` can hold either behind one type. ``idempotency_key``
    lets a transport that supports it (Resend) de-duplicate retry attempts of
    the *same* logical send; transports that don't (SMTP) accept and ignore it.
    """

    def send_message(
        self, message: EmailMessage, *, idempotency_key: str | None = None
    ) -> None: ...
