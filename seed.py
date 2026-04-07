"""
VisiChek Database Seeder

Run: python seed.py

Creates default tenant, departments, super admin, privacy notice, and retention policies.
"""
import asyncio
import os
import time

from dotenv import load_dotenv

load_dotenv()


async def seed():
    from core.database import db
    from schemas.tenant_schema import TenantCreate
    from schemas.department_schema import DepartmentCreate
    from schemas.system_user_schema import SystemUserCreate
    from schemas.privacy_notice_schema import PrivacyNoticeCreate
    from schemas.retention_policy_schema import RetentionPolicyCreate
    from schemas.sub_processor_schema import SubProcessorCreate
    from schemas.imports import (
        SystemUserRole, LawfulBasis, DeletionAction, NoticeDisplayMode, AccountStatus,
    )
    from repositories.tenant_repo import create_tenant, get_tenant
    from repositories.department_repo import create_department
    from repositories.system_user_repo import create_system_user, get_system_user
    from repositories.privacy_notice_repo import create_privacy_notice
    from repositories.retention_policy_repo import create_retention_policy
    from repositories.sub_processor_repo import create_sub_processor

    print("Seeding VisiChek database...")

    # 1. Create default tenant
    existing_tenant = await get_tenant({"name": "VisiChek Demo Company"})
    if existing_tenant:
        assert existing_tenant.id is not None
        tenant_id = existing_tenant.id
        print(f"  Tenant already exists: {tenant_id}")
    else:
        tenant = await create_tenant(TenantCreate(
            company_name="VisiChek Demo Company",
            lawful_basis=LawfulBasis.LEGITIMATE_INTEREST,
            notice_display_mode=NoticeDisplayMode.PASSIVE,
            retention_days=1095,
            privacy_policy_url="https://visicheck-demo.com/privacy",
            cross_border_approved=False,
        ))
        assert tenant.id is not None
        tenant_id = tenant.id
        print(f"  Created tenant: {tenant_id}")

    # 2. Create departments
    dept_codes = [
        ("ENG", "Engineering"),
        ("HR", "Human Resources"),
        ("EXEC", "Executive"),
    ]
    for code, name in dept_codes:
        from repositories.department_repo import get_department
        existing = await get_department({"tenant_id": tenant_id, "code": code})
        if not existing:
            await create_department(DepartmentCreate(
                tenant_id=tenant_id, code=code, name=name,
            ))
            print(f"  Created department: {name}")
        else:
            print(f"  Department already exists: {name}")

    # 3. Create super admin
    admin_email = os.getenv("SUPER_ADMIN_EMAIL", "superadmin@visicheck.com")
    admin_password = os.getenv("SUPER_ADMIN_PASSWORD", "changeme123")
    existing_admin = await get_system_user({"email": admin_email})
    if not existing_admin:
        await create_system_user(SystemUserCreate(
            tenant_id=tenant_id,
            full_name="Super Admin",
            email=admin_email,
            password_hash=admin_password,
            role=SystemUserRole.SUPER_ADMIN,
            account_status=AccountStatus.ACTIVE,
        ))
        print(f"  Created super admin: {admin_email}")
    else:
        print(f"  Super admin already exists: {admin_email}")

    # 4. Create default privacy notice
    from repositories.privacy_notice_repo import get_active_notice_for_tenant
    existing_notice = await get_active_notice_for_tenant(tenant_id)
    if not existing_notice:
        await create_privacy_notice(PrivacyNoticeCreate(
            tenant_id=tenant_id,
            version_code="v1.0",
            title="Visitor Privacy Notice",
            summary=(
                "We collect your name, photo, and contact details to manage "
                "visitor access and ensure premises security. Your data is retained "
                "for up to 3 years and is not shared with third parties except as "
                "required by law. You may request access, correction, or deletion "
                "of your data at any time."
            ),
            effective_from=int(time.time()),
            is_active=True,
        ))
        print("  Created default privacy notice v1.0")
    else:
        print("  Privacy notice already exists")

    # 5. Create default retention policies
    retention_scopes = [
        ("visit_sessions", 1095, DeletionAction.ANONYMISE),
        ("id_images", 30, DeletionAction.DELETE),
        ("visitor_profiles", 1095, DeletionAction.ANONYMISE),
    ]
    for scope, days, action in retention_scopes:
        from repositories.retention_policy_repo import get_retention_policy
        existing = await get_retention_policy({"tenant_id": tenant_id, "scope": scope})
        if not existing:
            await create_retention_policy(RetentionPolicyCreate(
                tenant_id=tenant_id, scope=scope, retention_days=days, action=action,
            ))
            print(f"  Created retention policy: {scope} ({days} days)")
        else:
            print(f"  Retention policy already exists: {scope}")

    # 6. Create OCR sub-processor entry
    from repositories.sub_processor_repo import get_sub_processor
    existing_sp = await get_sub_processor({"tenant_id": tenant_id, "provider": "StructOCR"})
    if not existing_sp:
        await create_sub_processor(SubProcessorCreate(
            tenant_id=tenant_id,
            provider="StructOCR",
            purpose="OCR extraction of visitor ID data",
            jurisdiction="Nigeria",
            dpa_signed=False,
            uses_data_for_training=False,
        ))
        print("  Created sub-processor: StructOCR")
    else:
        print("  Sub-processor already exists: StructOCR")

    print("\nSeeding complete!")


if __name__ == "__main__":
    asyncio.run(seed())
