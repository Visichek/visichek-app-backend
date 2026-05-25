"""Legal documents feature package.

Platform-level legal documents (privacy policy, terms of service, service
agreements, etc.) managed by application admins and served to the public
marketing website. Mirrors the ``blog/`` package layout (schemas /
repositories / services / writers / routes) and reuses the host backend's
queued-write + precompute + tables-list infrastructure.

This is *separate* from the tenant-scoped ``privacy_notice`` compliance
feature (``api/v1/privacy_notice_route.py``), which governs per-tenant
visitor consent notices. Legal documents here are Visichek's own public
legal copy.
"""
