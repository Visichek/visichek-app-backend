"""KYC integration layer.

Same Manager + Provider pattern used by ``core/payments``: a singleton
``KYCManager`` is configured at app boot from ``Settings``, and routes
get a ``KYCProvider`` via ``KYCManager.get_instance().get_provider()``.

Currently the only provider is :class:`DojahKYCProvider` (sandbox /
production switch toggled by ``DOJAH_BASE_URL``). New providers
(e.g. Smile Identity, Premier Verify) only need to implement the
``KYCProvider`` protocol and register themselves in ``KYCManager``.
"""

from core.kyc.manager import KYCManager
from core.kyc.provider import KYCProvider
from core.kyc.types import (
    KYCInitiateRequest,
    KYCInitiateResponse,
    KYCVerificationDetails,
    KYCWebhookEvent,
)

__all__ = [
    "KYCManager",
    "KYCProvider",
    "KYCInitiateRequest",
    "KYCInitiateResponse",
    "KYCVerificationDetails",
    "KYCWebhookEvent",
]
