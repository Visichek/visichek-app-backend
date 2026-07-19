"""Every admin-gated route MUST have a key in ADMIN_PERMISSIONS.

The runtime check (``assert_admin_permission_coverage``) only logs a
warning at boot, so a missing registration silently 403s every admin in
production (see CLAUDE.md "MANDATORY: Add new endpoints to
config/role_permissions.py"). This test makes the drift fail CI instead.

Found the hard way: ``GET /v1/admins`` was registered with a trailing
slash ("/v1/admins/") — key matching is exact, so the permission never
matched and the route 403'd for everyone.
"""

from __future__ import annotations


def test_every_admin_gated_route_is_registered() -> None:
    from main import app
    from security.permissions import find_unregistered_admin_routes

    missing = find_unregistered_admin_routes(app)
    assert missing == [], (
        "Admin-gated routes missing from config.role_permissions."
        f"ADMIN_PERMISSIONS (every admin will 403 on them): {missing}"
    )
