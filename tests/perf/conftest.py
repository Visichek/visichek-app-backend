"""Shared fixtures for the perf smoke suite.

The perf suite reuses the integration-test environment (real Mongo + Redis)
but isolates itself via a dedicated DB name so perf runs don't poison the
integration dataset.
"""

from __future__ import annotations

# Delegate the heavy setup (patching the ``db`` binding across every module)
# to the integration conftest — the logic is identical and duplicating it
# would drift. The ``tests.integration.conftest`` fixtures are discovered
# automatically when this file is loaded as part of the ``tests`` package.
from tests.integration.conftest import (  # noqa: F401  (fixtures re-exported)
    integration_app,
    integration_client,
    mongo_db,
    redis_client,
    seeded_system_user,
    seeded_tenant,
    auth_headers,
    unique_password_suffix,
)
