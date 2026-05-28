"""Load agreement master templates from the legal-documents collection.

The master copy of each agreement is a normal legal document (one head record
per reserved slug). We always prefer the live ``published_body`` (what tenants
must accept) and fall back to the working ``body`` for preview before a first
publish.

``current_master_version`` is the gate's source of truth for "what must be
accepted now": it returns the published ``current_version`` (an int) or
``None`` when the master has never been published — in which case the
agreement is simply not required yet (safe rollout: no tenant is blocked until
the admin publishes the masters). The version is cached per slug in Redis for a
short window to keep the hot-path gate cheap; the legal publish writer
invalidates it on every publish.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple, cast

from core.redis_cache import cache_db

logger = logging.getLogger(__name__)

Block = dict

_VERSION_KEY_PREFIX = "agreement_master_version:"
_VERSION_TTL_SECONDS = 120
# Sentinel cached when a master is not published yet, so a miss isn't re-queried
# on every request.
_NONE_SENTINEL = "none"


def _version_key(slug: str) -> str:
    return f"{_VERSION_KEY_PREFIX}{slug}"


async def _load_master(slug: str):
    """Fetch the master legal document head record by slug, or ``None``."""
    try:
        from legal.repositories.legal_document_repo import get_legal_document_by_slug

        return await get_legal_document_by_slug(slug)
    except Exception:
        logger.warning("agreement master load failed for slug=%s", slug, exc_info=True)
        return None


async def current_master_version(slug: str) -> Optional[int]:
    """Published version that must be accepted now, or ``None`` if unpublished.

    Cached per slug (short TTL) for the gate. Fails open to ``None`` on any
    cache/DB error so a blip never blocks tenants.
    """
    try:
        cached = cast(Optional[str], cache_db.get(_version_key(slug)))
    except Exception:
        cached = None
    if cached == _NONE_SENTINEL:
        return None
    if cached:
        try:
            return int(cached)
        except (TypeError, ValueError):
            pass

    doc = await _load_master(slug)
    version = None
    if doc is not None and getattr(doc, "published_body", None):
        version = getattr(doc, "current_version", None)
    try:
        cache_db.setex(
            _version_key(slug),
            _VERSION_TTL_SECONDS,
            str(version) if version is not None else _NONE_SENTINEL,
        )
    except Exception:
        pass
    return version


def invalidate_master_version(slug: str) -> None:
    """Drop the cached published version for a slug (call on publish)."""
    try:
        cache_db.delete(_version_key(slug))
    except Exception:
        pass


async def get_master_for_build(
    slug: str,
) -> Optional[Tuple[List[Block], int, Optional[str], Optional[str]]]:
    """Return ``(body, version, title, summary)`` for building a tenant copy.

    Prefers the published body (``version`` = ``current_version``); falls back
    to the working draft body (``version`` = 0) so the document can be previewed
    before the first publish. Returns ``None`` when the master doesn't exist or
    has no content at all.
    """
    doc = await _load_master(slug)
    if doc is None:
        return None
    published = getattr(doc, "published_body", None)
    if published:
        version = int(getattr(doc, "current_version", 0) or 0)
        body = published
    else:
        body = getattr(doc, "body", None) or []
        version = 0
    if not body:
        return None
    return body, version, getattr(doc, "title", None), getattr(doc, "summary", None)
