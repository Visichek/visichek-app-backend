"""Unsplash feature-image fallback.

Ported from ``visichek-blog-backend/services/unsplash.py``. When
``UNSPLASH_ACCESS_KEY`` is unset every call returns the hard-coded
fallback URL — same behaviour as the original.
"""

from __future__ import annotations

from typing import Optional

import httpx

from core.settings import get_settings

UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"

DEFAULT_FALLBACK_IMAGE = (
    "https://images.unsplash.com/photo-1497366754035-f200968a6e72"
    "?auto=format&fit=crop&w=1600&q=80"
)


async def search_unsplash_image(query: str) -> Optional[str]:
    """Search Unsplash for an image matching ``query``.

    Returns the regular-sized URL or ``None`` when the API isn't
    configured, the request fails, or no result was found.
    """
    settings = get_settings()
    if not query or not settings.unsplash_access_key:
        return None

    params: dict[str, str | int] = {
        "query": query,
        "per_page": 1,
        "orientation": "landscape",
    }
    headers = {"Authorization": f"Client-ID {settings.unsplash_access_key}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                UNSPLASH_SEARCH_URL, params=params, headers=headers
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.RequestError, httpx.HTTPStatusError):
        return None

    results = data.get("results") or []
    if not results:
        return None
    urls = results[0].get("urls") or {}
    return urls.get("regular") or urls.get("full") or urls.get("raw")


async def resolve_feature_image_url(title: str) -> str:
    """Return an Unsplash URL for ``title`` or the global fallback."""
    url = await search_unsplash_image(title)
    return url or DEFAULT_FALLBACK_IMAGE
