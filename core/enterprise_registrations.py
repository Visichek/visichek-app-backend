"""Side-effect module that imports every enterprise feature pack.

Mirror of ``core/queue/registrations.py`` for enterprise sub-apps.

Each enterprise feature pack is a module that:

  1. Defines a ``fastapi.APIRouter`` carrying its custom endpoints.
  2. Calls ``register_enterprise_app(slug, router)`` at import time
     where ``slug`` matches the ``name`` of an Enterprise plan in
     MongoDB.

When a new pack ships, add a side-effect import here so it is loaded
during app boot. ``main.py`` imports this module once and then walks
the registry to mount each router on the parent FastAPI instance.

This file intentionally starts EMPTY — there are no enterprise feature
packs in the open-source baseline. Customer-specific packs live in
private modules (e.g. ``services.enterprise.acme``) and add their
import here. Keeping the aggregator means the import path is stable
(``core.enterprise_registrations``) regardless of how many packs are
installed in a given deployment.
"""

from __future__ import annotations

# Add side-effect imports for enterprise feature packs below.
# Example:
#     from services.enterprise import acme as _acme  # noqa: F401
#     from services.enterprise import globex as _globex  # noqa: F401
