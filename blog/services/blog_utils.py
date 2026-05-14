"""Blog-utility helpers (sort map, blog-type filter, category image).

Ported from ``visichek-blog-backend/sub_app1/services/utils.py``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from blog.schemas.imports import PublicBlogType

BLOG_TYPE_MAP = {
    PublicBlogType.hero_section.value: {"blogType": "hero section"},
    PublicBlogType.editors_pick.value: {"blogType": "editors pick"},
    PublicBlogType.featured_story.value: {"blogType": "featured story"},
    PublicBlogType.normal.value: {"blogType": "normal"},
}


def get_path_filter(blog_type: PublicBlogType) -> dict:
    try:
        return BLOG_TYPE_MAP[blog_type.value]
    except KeyError:
        raise ValueError(f"Unsupported blog_type: {blog_type.value!r}")


SORT_MAP = {
    "newest": {"sort_field": "date_created", "sort_order": -1},
    "oldest": {"sort_field": "date_created", "sort_order": 1},
    "mostRecentlyUpdated": {"sort_field": "last_updated", "sort_order": -1},
    "leastRecentlyUpdated": {"sort_field": "last_updated", "sort_order": 1},
    "latestPublished": {"sort_field": "publishDate", "sort_order": -1},
    "earliestPublished": {"sort_field": "publishDate", "sort_order": 1},
}


def get_sort(sort_param: Optional[str]) -> Optional[dict]:
    if sort_param is None:
        return None
    try:
        return SORT_MAP[sort_param]
    except KeyError:
        raise ValueError(f"Unsupported sort option: {sort_param!r}")


CATEGORY_IMAGE_MAP = {
    "physical-security": "https://images.unsplash.com/photo-1557597774-9d273605dfa9?auto=format&fit=crop&w=1600&q=80",
    "access-control": "https://images.unsplash.com/photo-1555421689-491a97ff2040?auto=format&fit=crop&w=1600&q=80",
    "data-privacy-and-compliance": "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=1600&q=80",
    "workplace-management": "https://images.unsplash.com/photo-1497366216548-37526070297c?auto=format&fit=crop&w=1600&q=80",
    "front-desk-operations": "https://images.unsplash.com/photo-1606836591695-4d58a73eba1e?auto=format&fit=crop&w=1600&q=80",
    "visitor-experience": "https://images.unsplash.com/photo-1542744173-8e7e53415bb0?auto=format&fit=crop&w=1600&q=80",
    "digital-transformation": "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1600&q=80",
    "ai-and-ocr-technology": "https://images.unsplash.com/photo-1677442136019-21780ecad995?auto=format&fit=crop&w=1600&q=80",
    "product-updates": "https://images.unsplash.com/photo-1504384308090-c894fdcc538d?auto=format&fit=crop&w=1600&q=80",
    "industry-insights": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&w=1600&q=80",
    "case-studies": "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?auto=format&fit=crop&w=1600&q=80",
    "best-practices": "https://images.unsplash.com/photo-1434030216411-0b793f4b4173?auto=format&fit=crop&w=1600&q=80",
}

DEFAULT_CATEGORY_IMAGE = (
    "https://images.unsplash.com/photo-1497366754035-f200968a6e72"
    "?auto=format&fit=crop&w=1600&q=80"
)


@lru_cache(maxsize=100)
def get_category_image_url(category: str) -> str:
    """Return an image URL for a blog category (slug or display name)."""
    if not category:
        return DEFAULT_CATEGORY_IMAGE
    key = category.strip().lower().replace("&", "and")
    key = "-".join(key.split())
    return CATEGORY_IMAGE_MAP.get(key, DEFAULT_CATEGORY_IMAGE)
