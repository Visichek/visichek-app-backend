from __future__ import annotations

import logging

from fastapi import Depends, Request

from security.auth import verify_admin_token
from security.principal import AuthPrincipal

logger = logging.getLogger(__name__)


async def log_what_admin_does(
    request: Request,
    principal: AuthPrincipal = Depends(verify_admin_token),
) -> None:
    endpoint = request.scope.get("endpoint")
    endpoint_name = endpoint.__name__ if endpoint else "unknown"
    logger.info(
        "Admin action: admin_id=%s route=%s function=%s",
        principal.user_id,
        request.url.path,
        endpoint_name,
    )
