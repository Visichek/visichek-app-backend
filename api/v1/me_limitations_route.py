"""GET /v1/me/limitations — what the calling tenant CAN and CANNOT do.

The frontend calls this once on app load (and again whenever the
subscription changes) to know:

  * which features are denied by the current plan,
  * which entity caps apply,
  * which existing branches / departments are locked because the
    tenant has more of them than the plan now allows,
  * whether the tenant has access to enterprise sub-app endpoints.

See ``backend-docs/limitations.txt`` for the full FE contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.response_envelope import document_response
from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.me_limitations_service import build_me_limitations

router = APIRouter(prefix="/me", tags=["Me — limitations"])


@router.get("/limitations")
@document_response(
    message="Plan limitations retrieved",
    description=(
        "Returns the plan-driven limitations for the authenticated user. "
        "Drives client-side rendering of nav items, locked rows, and "
        "upgrade CTAs without round-tripping to discover denials. "
        "Application admin / application user (no tenant) get an empty "
        "structure since platform-side calls are not gated by tenant plans."
    ),
    summary="Get my plan limitations",
)
async def get_me_limitations(
    principal: AuthPrincipal = Depends(verify_any_token),
):
    return await build_me_limitations(principal)
