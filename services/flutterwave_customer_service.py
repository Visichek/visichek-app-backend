from __future__ import annotations

import logging
from typing import Optional


from core.errors import AppException, ErrorCode
from core.settings import get_settings
from repositories.tenant_repo import get_tenant, update_tenant
from schemas.tenant_schema import TenantUpdate

logger = logging.getLogger(__name__)


async def create_or_get_flutterwave_customer(
    tenant_id: str,
    email: str,
    name: str,
) -> str:
    """
    Create or retrieve a Flutterwave customer token for a tenant.

    If the tenant already has a flutterwave_customer_id, returns it immediately.
    Otherwise, creates a new customer via Flutterwave API and stores the ID.

    Args:
        tenant_id: MongoDB tenant ID
        email: Customer email address
        name: Customer name

    Returns:
        Flutterwave customer ID/token

    Raises:
        AppException: If customer creation fails or tenant not found
    """
    from bson import ObjectId

    # Validate tenant exists
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

    # Return existing customer ID if available
    if tenant.flutterwave_customer_id:
        logger.debug(
            f"Using existing Flutterwave customer: tenant_id={tenant_id}, "
            f"customer_id={tenant.flutterwave_customer_id}"
        )
        return tenant.flutterwave_customer_id

    # Create new customer
    customer_id = await _create_flutterwave_customer(email=email, name=name)

    # Update tenant with new customer ID
    try:
        update_data = TenantUpdate(flutterwave_customer_id=customer_id)
        await update_tenant(
            filter_dict={"_id": ObjectId(tenant_id)},
            tenant_data=update_data,
        )
        logger.info(
            f"Created and stored Flutterwave customer: tenant_id={tenant_id}, "
            f"customer_id={customer_id}"
        )
        return customer_id
    except Exception as e:
        logger.error(
            f"Failed to update tenant with customer ID: {str(e)}", exc_info=True
        )
        # Still return the customer ID even if we couldn't store it,
        # next attempt will create another or we can retry the update
        raise AppException(
            status_code=500,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Failed to store Flutterwave customer ID on organization",
            details=str(e),
        ) from e


async def _create_flutterwave_customer(email: str, name: str) -> str:
    """
    Create a new customer in Flutterwave.

    Flutterwave doesn't have a traditional "customer" object like Stripe,
    but we can use the customer details in payment intents and store a reference.
    This function acts as a tokenization endpoint for recurring billing.

    Args:
        email: Customer email
        name: Customer name

    Returns:
        Customer identifier (in Flutterwave, this is typically the email or a composite)

    Raises:
        AppException: If API call fails
    """
    settings = get_settings()

    if not settings.flutterwave_secret_key:
        raise AppException(
            status_code=503,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Flutterwave is not configured",
        )

    # Flutterwave doesn't have a dedicated customer creation endpoint,
    # but we can create a saved payment method or use customer details.
    # For now, we'll use a composite ID format: "fw_<email>_<timestamp>"
    # This serves as a reference for recurring billing lookups.
    import time

    customer_id = f"fw_{email.replace('@', '_').replace('.', '_')}_{int(time.time())}"

    logger.info(
        f"Created Flutterwave customer reference: customer_id={customer_id}, "
        f"email={email}"
    )
    return customer_id


async def get_flutterwave_customer(tenant_id: str) -> Optional[str]:
    """
    Retrieve the Flutterwave customer ID for a tenant.

    Args:
        tenant_id: MongoDB tenant ID

    Returns:
        Flutterwave customer ID or None if not set

    Raises:
        AppException: If tenant not found
    """
    from bson import ObjectId

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

    return tenant.flutterwave_customer_id
