"""Blog backend ported into the Visichek app backend.

This package owns blog/media/article CRUD that originally lived in the
standalone ``visichek-blog-backend`` service. It is intentionally
namespaced under ``blog/`` so it doesn't collide with the host
backend's existing ``schemas/``, ``services/``, ``repositories/``,
``api/``, ``security/`` modules (which already define unrelated
``admin`` / ``user`` / ``token`` symbols).

Layering inside this package mirrors the host backend's 4-layer rule:

    blog/routes/      → HTTP contract, security deps, @document_response
    blog/writers/     → @write_handler queued mutations + @register_precompute loaders
    blog/services/    → business logic, R2 / Unsplash / FreeImage integrations
    blog/repositories → MongoDB access (no business logic)
    blog/schemas/     → Pydantic models

All writes are routed through ``core.queue.write_pipeline.enqueue_write``
so heavy work (R2 / local-disk uploads, image fallbacks) happens on
the celery ``worker-writes`` queue and routes respond with
``202 + job_id`` immediately. Hot list reads
(``blogs.list_published``, ``media.list``, ``categories.list``) are
served from the Redis precompute cache.
"""
