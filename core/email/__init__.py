from core.email.manager import EmailManager, build_email_transport
from core.email.transport import (
    ResendConfig,
    ResendTransport,
    SmtpConfig,
    SMTPTransport,
)
from core.email.types import (
    EmailDispatchRequest,
    EmailMessage,
    EmailSendResult,
    EmailTransport,
    MountedTemplate,
)

__all__ = [
    "EmailDispatchRequest",
    "EmailManager",
    "EmailMessage",
    "EmailSendResult",
    "EmailTransport",
    "MountedTemplate",
    "ResendConfig",
    "ResendTransport",
    "SMTPTransport",
    "SmtpConfig",
    "build_email_transport",
]
