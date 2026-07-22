from __future__ import annotations

import logging
from typing import Optional

from bson import ObjectId

from core.errors import AppException, ErrorCode
from core.settings import get_settings
from repositories.tenant_repo import get_tenant, update_tenant
from schemas.tenant_schema import TenantUpdate

logger = logging.getLogger(__name__)


async def create_or_get_stripe_customer(
    tenant_id: str,
    email: str,
    name: str,
) -> str:
    """
    Create or retrieve a Stripe customer for a tenant.

    A Stripe customer is required for off-session recurring charging: the
    first PaymentIntent attaches its PaymentMethod to this customer (via
    ``setup_future_usage="off_session"``), and the renewal scheduler later
    charges that saved method against the customer.

    If the tenant already has a stripe_customer_id, returns it immediately.

    Args:
        tenant_id: MongoDB tenant ID
        email: Customer email address
        name: Customer name

    Returns:
        Stripe customer id (``cus_...``)

    Raises:
        AppException: If customer creation fails or tenant not found
    """
    if not ObjectId.is_valid(tenant_id):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Invalid organization ID",
            details={"tenant_id": tenant_id},
        )

    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Organization not found",
            details={"tenant_id": tenant_id},
        )

    if tenant.stripe_customer_id:
        logger.debug(
            f"Using existing Stripe customer: tenant_id={tenant_id}, "
            f"customer_id={tenant.stripe_customer_id}"
        )
        return tenant.stripe_customer_id

    customer_id = await _create_stripe_customer(
        email=email, name=name, tenant_id=tenant_id
    )

    try:
        await update_tenant(
            filter_dict={"_id": ObjectId(tenant_id)},
            tenant_data=TenantUpdate(stripe_customer_id=customer_id),
        )
        logger.info(
            f"Created and stored Stripe customer: tenant_id={tenant_id}, "
            f"customer_id={customer_id}"
        )
        return customer_id
    except Exception as e:
        logger.error(
            f"Failed to update tenant with Stripe customer id: {str(e)}",
            exc_info=True,
        )
        raise AppException(
            status_code=500,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Failed to store Stripe customer id on organization",
            details=str(e),
        ) from e


async def _create_stripe_customer(email: str, name: str, tenant_id: str) -> str:
    """Create a new customer in Stripe via the SDK."""
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise AppException(
            status_code=503,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Stripe is not configured",
        )

    try:
        import stripe
    except ModuleNotFoundError as err:
        raise AppException(
            status_code=503,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="stripe package is not installed",
        ) from err

    stripe.api_key = settings.stripe_secret_key
    create_kwargs: dict = {"email": email, "metadata": {"tenant_id": tenant_id}}
    if name:
        create_kwargs["name"] = name
    try:
        customer = stripe.Customer.create(**create_kwargs)
    except Exception as e:
        raise AppException(
            status_code=502,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Stripe customer creation failed",
            details=str(e),
        ) from e

    customer_id = getattr(customer, "id", None)
    if not customer_id:
        raise AppException(
            status_code=502,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Stripe customer creation returned no id",
        )
    logger.info(f"Created Stripe customer: customer_id={customer_id}, email={email}")
    return customer_id


async def get_stripe_customer(tenant_id: str) -> Optional[str]:
    """Retrieve the Stripe customer id for a tenant."""
    if not ObjectId.is_valid(tenant_id):
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Invalid organization ID",
        )
    tenant = await get_tenant({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise AppException(
            status_code=404,
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="Organization not found",
        )
    return tenant.stripe_customer_id
