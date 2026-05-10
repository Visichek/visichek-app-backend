"""Enterprise sub-app registry.

Enterprise plans on VisiChek are bespoke per-customer. Sometimes a
customer pays for endpoints that nobody else has — a custom integration,
a private dashboard, a regulator-specific export, etc. Rather than bolt
those onto the global API surface (and have to gate every endpoint
individually), we mount each enterprise feature pack as its own FastAPI
APIRouter under ``/v1/enterprise/<plan-slug>/*``.

How it fits together
====================

1. Sales / app-admin creates an Enterprise plan in MongoDB with a
   stable, URL-safe slug as its ``name`` (e.g. ``"enterprise-acme"``).
   This is the slug the customer's tenant will subscribe to.

2. The dev who builds the custom feature creates a module under
   ``services/enterprise/`` (or anywhere) that imports this registry
   and calls :func:`register_enterprise_app` with the same slug at
   import time. The module exposes an ``APIRouter`` carrying its
   custom endpoints.

3. The registrations module is imported once at app boot (we extend
   ``core/queue/registrations.py`` for this). When ``main.py`` finishes
   mounting the auto-routes, it calls :func:`include_enterprise_apps`
   which iterates the registry and includes each router on the parent
   FastAPI instance with prefix ``/v1/enterprise/<slug>``.

4. ``core/plan_enforcement.py`` adds a routing-level guard: when a
   request hits ``/v1/enterprise/<slug>/...``, the active subscription's
   plan ``name`` MUST equal ``<slug>``. Otherwise the request is
   rejected with HTTP 403 ``ENTERPRISE_PLAN_MISMATCH``.

Why APIRouter and not a mounted FastAPI sub-app?
================================================

A mounted ``FastAPI`` sub-app runs its own middleware stack — the
parent's ``PlanEnforcementMiddleware`` would NOT fire on its requests.
Using ``APIRouter`` instead keeps the sub-app inside the parent's
middleware boundary, so plan gating, rate limiting, request-id, etc.
all still work. Logically they behave like sub-apps; physically they
share the parent's middleware tree.

Slug rules
==========

* Lowercase ASCII letters, digits, and hyphens only.
* Must match ``^[a-z][a-z0-9-]{2,40}$``.
* Cannot collide with any singleton plan name (``free`` / ``starter``
  / ``premium``) or with the reserved template name ``enterprise``.
"""

from __future__ import annotations

import logging
import re
from typing import Dict

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)


_REGISTRY: Dict[str, APIRouter] = {}

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
_RESERVED_SLUGS = frozenset({"free", "starter", "premium", "enterprise"})


def register_enterprise_app(slug: str, router: APIRouter) -> None:
    """Register an APIRouter as the sub-app for an enterprise plan.

    Idempotent: re-registering the same slug with the same router is a
    no-op (useful for hot-reload during dev). Re-registering the same
    slug with a different router is a hard error so we don't silently
    swap behaviour at runtime.
    """
    if not slug or not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid enterprise slug '{slug}'. Must match {_SLUG_RE.pattern}"
        )
    if slug in _RESERVED_SLUGS:
        raise ValueError(
            f"Slug '{slug}' is reserved (singleton or template). "
            "Use a customer-specific name like 'enterprise-acme'."
        )

    existing = _REGISTRY.get(slug)
    if existing is None:
        _REGISTRY[slug] = router
        logger.info("enterprise_apps: registered slug=%s", slug)
        return

    if existing is router:
        # Hot-reload friendly — same router instance re-registered.
        return

    raise RuntimeError(
        f"Enterprise app slug '{slug}' is already registered with a "
        "different router. Pick a unique slug per feature pack."
    )


def all_enterprise_apps() -> Dict[str, APIRouter]:
    """Return a copy of the current slug → APIRouter registry."""
    return dict(_REGISTRY)


def include_enterprise_apps(parent: FastAPI) -> None:
    """Include every registered enterprise sub-app onto the parent FastAPI.

    Mount path is ``/v1/enterprise/<slug>``. Tags are set per-slug so
    Swagger groups them sensibly under "Enterprise (<slug>)".
    """
    for slug, router in _REGISTRY.items():
        parent.include_router(
            router,
            prefix=f"/v1/enterprise/{slug}",
            tags=[f"Enterprise ({slug})"],
        )
        logger.info(
            "enterprise_apps: mounted slug=%s at /v1/enterprise/%s", slug, slug
        )


def is_enterprise_app_path(path: str) -> bool:
    """Return True if ``path`` starts with ``/v1/enterprise/<slug>/``."""
    return path.startswith("/v1/enterprise/")


def extract_enterprise_slug(path: str) -> str | None:
    """Pull the slug segment out of an enterprise sub-app URL.

    Returns ``None`` for paths that aren't enterprise sub-app paths or
    that lack a slug (e.g. ``"/v1/enterprise/"`` with no slug).
    """
    if not is_enterprise_app_path(path):
        return None
    parts = path.split("/", maxsplit=4)
    # Expected: ['', 'v1', 'enterprise', '<slug>', '<rest...>']
    if len(parts) < 4:
        return None
    slug = parts[3]
    return slug or None
