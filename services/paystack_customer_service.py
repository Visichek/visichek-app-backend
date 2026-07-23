from __future__ import annotations

import logging
from typing import Optional

import requests  # type: ignore[import-untyped]
from bson import ObjectId

from core.errors import AppException, ErrorCode
from core.settings import get_settings
from repositories.tenant_repo import get_tenant, update_tenant
from schemas.tenant_schema import TenantUpdate

logger = logging.getLogger(__name__)

_PAYSTACK_BASE_URL = "https://api.paystack.co"


async def create_or_get_paystack_customer(
    tenant_id: str,
    email: str,
    name: str,
) -> str:
    """
    Create or retrieve a Paystack customer code for a tenant.

    If the tenant already has a paystack_customer_id, returns it immediately.
    Otherwise, creates a new customer via the Paystack Customer API and stores
    the returned customer_code (format "CUS_xxxxxxxx") on the tenant.

    Args:
        tenant_id: MongoDB tenant ID
        email: Customer email address
        name: Customer name (split into first/last for Paystack)

    Returns:
        Paystack customer code

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

    # Return existing customer code if available
    if tenant.paystack_customer_id:
        logger.debug(
            f"Using existing Paystack customer: tenant_id={tenant_id}, "
            f"customer_id={tenant.paystack_customer_id}"
        )
        return tenant.paystack_customer_id

    customer_code = await _create_paystack_customer(email=email, name=name)

    try:
        update_data = TenantUpdate(paystack_customer_id=customer_code)
        await update_tenant(
            filter_dict={"_id": ObjectId(tenant_id)},
            tenant_data=update_data,
        )
        logger.info(
            f"Created and stored Paystack customer: tenant_id={tenant_id}, "
            f"customer_id={customer_code}"
        )
        return customer_code
    except Exception as e:
        logger.error(
            f"Failed to update tenant with customer code: {str(e)}", exc_info=True
        )
        raise AppException(
            status_code=500,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Failed to store Paystack customer code on organization",
            details=str(e),
        ) from e


async def _create_paystack_customer(email: str, name: str) -> str:
    """
    Create a new customer in Paystack via POST /customer.

    Paystack returns a customer_code (e.g. "CUS_xxxxxxxx") that is used across
    all Paystack APIs to reference the customer for recurring billing.

    Args:
        email: Customer email
        name: Customer name (split on the first space into first/last name)

    Returns:
        Paystack customer code

    Raises:
        AppException: If the API call fails or the secret key is not configured
    """
    settings = get_settings()

    if not settings.paystack_secret_key:
        raise AppException(
            status_code=503,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Paystack is not configured",
        )

    first_name, _, last_name = (name or "").strip().partition(" ")

    response = requests.post(
        f"{_PAYSTACK_BASE_URL}/customer",
        json={
            "email": email,
            "first_name": first_name or None,
            "last_name": last_name or None,
        },
        headers={
            "Authorization": f"Bearer {settings.paystack_secret_key}",
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    data = response.json()
    if response.status_code >= 400 or not data.get("status"):
        raise AppException(
            status_code=502,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Paystack customer creation failed",
            details=data,
        )

    customer_code = data.get("data", {}).get("customer_code")
    if not customer_code:
        raise AppException(
            status_code=502,
            code=ErrorCode.PAYMENT_PROVIDER_ERROR,
            message="Paystack customer creation returned no customer_code",
            details=data,
        )

    logger.info(
        f"Created Paystack customer: customer_id={customer_code}, email={email}"
    )
    return customer_code


async def get_paystack_customer(tenant_id: str) -> Optional[str]:
    """
    Retrieve the Paystack customer code for a tenant.

    Args:
        tenant_id: MongoDB tenant ID

    Returns:
        Paystack customer code or None if not set

    Raises:
        AppException: If tenant not found
    """
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

    return tenant.paystack_customer_id
