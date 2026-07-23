from __future__ import annotations

from typing import Any, Optional

from core.settings import get_settings


def resolve_browser_callback_url(metadata: Optional[dict[str, Any]]) -> Optional[str]:
    """The post-payment browser redirect for a hosted checkout page.

    A caller-supplied ``metadata.redirect_url`` wins; otherwise fall back to
    the configured ``payment_callback_url`` (which itself always resolves to
    the frontend return page — see ``core.settings``). Centralised here so no
    initiating flow (plan checkout, addon purchase, trial card capture,
    generic payment intents) can forget it and bounce the customer to the
    provider's dashboard-configured Callback URL.
    """
    redirect = str((metadata or {}).get("redirect_url") or "").strip()
    return redirect or get_settings().payment_callback_url or None
