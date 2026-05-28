"""Tenant Agreements subsystem.

Application admins author two master legal documents — the Data Processing
Agreement and the Visitor Privacy Policy — in the platform legal editor
(``/v1/legal-documents``). Each master carries fixed ``[placeholder]`` tokens
that are substituted per tenant from the tenant's own details. Every tenant
must accept the currently-published version of each master to keep using the
service; publishing a new version forces re-acceptance, enforced by a soft
gate (see ``security.auth._enforce_agreement_acceptance``).

Modules:

* ``config``      — the registry of agreements (key ↔ master slug ↔ doc type).
* ``templating``  — the fixed-allowlist ``[placeholder]`` substitution engine.
* ``master``      — load the master template + current version from the legal
  documents collection, with a short-lived per-slug version cache.
* ``seed``        — provision per-tenant agreement rows on tenant creation.
* ``bootstrap``   — ensure both master legal docs exist at startup.

The per-tenant acceptance store + business logic live one level up in
``services.tenant_agreement_service`` (mirrors the layout of the DPA service it
replaces).
"""
