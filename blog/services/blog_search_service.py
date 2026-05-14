"""Public-website blog search.

Ported from ``visichek-blog-backend/sub_app1/services/blog.py``. Builds
a Mongo filter from ``SearchQuery`` and returns ``ListOfBlogs`` (the
website-facing shape).
"""

from __future__ import annotations

from blog.repositories.blog_repo import search_blogs_repo
from blog.schemas.blog_schema import BlogOutLessDetailUserVersion, ListOfBlogs
from blog.schemas.imports import SearchQuery


async def search_blogs_service(query_params: SearchQuery) -> ListOfBlogs:
    """Run a case-insensitive title/author search and return matches."""
    filters: dict = {}

    if query_params.title:
        filters["title"] = {"$regex": query_params.title, "$options": "i"}
    if query_params.author:
        filters["author.name"] = {"$regex": query_params.author, "$options": "i"}

    filters["state"] = "published"

    skip = query_params.start or 0
    limit = query_params.stop or 100

    results = await search_blogs_repo(filters, skip=skip, limit=limit)

    return ListOfBlogs(
        totalItems=len(results),
        blogs=[BlogOutLessDetailUserVersion.model_validate(b) for b in results],
    )
