"""
Create MongoDB indexes for VisiChek collections.

Run: python scripts/create_indexes.py
"""

import asyncio
from dotenv import load_dotenv

load_dotenv()


async def create_indexes():
    from core.database import db

    print("Creating MongoDB indexes...")

    # Tenant Companies
    await db.tenant_companies.create_index("name", unique=True)
    print("  tenant_companies: unique index on 'name'")

    # Departments
    await db.departments.create_index([("tenant_id", 1), ("code", 1)], unique=True)
    print("  departments: unique compound index on (tenant_id, code)")

    # System Users
    await db.system_users.create_index([("tenant_id", 1), ("email", 1)], unique=True)
    print("  system_users: unique compound index on (tenant_id, email)")

    # User Sessions
    await db.user_sessions.create_index([("user_id", 1), ("started_at", -1)])
    print("  user_sessions: index on (user_id, started_at)")

    # Visitor Profiles
    await db.visitor_profiles.create_index(
        [("tenant_id", 1), ("phone", 1)], unique=True, sparse=True
    )
    await db.visitor_profiles.create_index(
        [("tenant_id", 1), ("email", 1)], sparse=True
    )
    print(
        "  visitor_profiles: unique compound on (tenant_id, phone), sparse on (tenant_id, email)"
    )

    # Visit Sessions
    await db.visit_sessions.create_index([("tenant_id", 1), ("status", 1)])
    await db.visit_sessions.create_index([("tenant_id", 1), ("check_in_time", -1)])
    await db.visit_sessions.create_index("badge_qr_token", unique=True, sparse=True)
    print(
        "  visit_sessions: (tenant_id, status), (tenant_id, check_in_time), unique sparse badge_qr_token"
    )

    # Expected Appointments
    await db.expected_appointments.create_index(
        [("tenant_id", 1), ("scheduled_datetime", -1)]
    )
    print("  expected_appointments: index on (tenant_id, scheduled_datetime)")

    # Privacy Notice Versions
    await db.privacy_notice_versions.create_index([("tenant_id", 1), ("is_active", 1)])
    print("  privacy_notice_versions: index on (tenant_id, is_active)")

    # System Audit Logs
    await db.system_audit_logs.create_index([("tenant_id", 1), ("occurred_at", -1)])
    await db.system_audit_logs.create_index([("tenant_id", 1), ("actor_id", 1)])
    await db.system_audit_logs.create_index(
        [("tenant_id", 1), ("target_entity", 1), ("target_id", 1)]
    )
    print(
        "  system_audit_logs: indexes on (tenant_id, occurred_at), (tenant_id, actor_id), (tenant_id, target)"
    )

    # Incident Logs
    await db.incident_logs.create_index([("tenant_id", 1), ("status", 1)])
    print("  incident_logs: index on (tenant_id, status)")

    # Data Subject Requests
    await db.data_subject_requests.create_index([("tenant_id", 1), ("status", 1)])
    print("  data_subject_requests: index on (tenant_id, status)")

    print("\nAll indexes created!")


if __name__ == "__main__":
    asyncio.run(create_indexes())
