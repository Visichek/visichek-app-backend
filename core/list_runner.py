"""Mongo executor for parsed ``ListQuery`` instances.

Pulls together :func:`core.list_params.parse_list_query` output and a
Mongo collection to produce the ``{items, meta}`` envelope. The repo
layer provides this helper a collection handle and a row-mapper, then
the route hands back the result via ``@document_response(include_meta=True)``.

Concurrency: the count and the page query run in parallel via
``asyncio.gather`` so list endpoints don't pay an extra round-trip for
``total``. Facet aggregations (when requested) also run in parallel
with each other.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, Mapping, Optional

from core.list_params import ListQuery, list_response


async def _count(collection: Any, filter_doc: Mapping[str, Any]) -> int:
    return int(await collection.count_documents(filter_doc))


async def _facet(
    collection: Any, filter_doc: Mapping[str, Any], field: str
) -> dict[str, int]:
    pipeline = [
        {"$match": filter_doc},
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
    ]
    out: dict[str, int] = {}
    async for doc in collection.aggregate(pipeline):
        key = doc.get("_id")
        if key is None:
            continue
        out[str(key)] = int(doc.get("count", 0))
    return out


async def _facet_status_with_all(
    collection: Any, filter_doc: Mapping[str, Any], field: str
) -> dict[str, int]:
    """Special facet that also reports an ``all`` total — frontend uses
    this for tab badges (Active | Inactive | All).
    """
    base = await _facet(collection, filter_doc, field)
    total = await _count(collection, filter_doc)
    base["all"] = total
    return base


async def run_list(
    *,
    collection: Any,
    query: ListQuery,
    base_filter: Optional[dict[str, Any]] = None,
    map_doc: Callable[[dict[str, Any]], Any] = lambda d: d,
    facet_runner: Optional[
        Callable[[Any, dict[str, Any], str], Coroutine[Any, Any, dict[str, int]]]
    ] = None,
) -> dict[str, Any]:
    """Execute the parsed query and return the standard envelope."""
    filter_doc = query.to_mongo(base_filter)
    sort_spec = query.mongo_sort()

    cursor = (
        collection.find(filter_doc)
        .sort(sort_spec)
        .skip(query.skip)
        .limit(query.limit)
    )

    async def _gather_page() -> list[Any]:
        out: list[Any] = []
        async for doc in cursor:
            out.append(map_doc(doc))
        return out

    page_task = asyncio.create_task(_gather_page())
    count_task = asyncio.create_task(_count(collection, filter_doc))

    facet_tasks: dict[str, asyncio.Task[dict[str, int]]] = {}
    runner = facet_runner or _facet_status_with_all
    for field in query.facets:
        facet_tasks[field] = asyncio.create_task(
            runner(collection, filter_doc, field)
        )

    items, total = await asyncio.gather(page_task, count_task)
    facets: dict[str, dict[str, int]] = {}
    for field, task in facet_tasks.items():
        facets[field] = await task

    return list_response(
        items,
        total=total,
        skip=query.skip,
        limit=query.limit,
        facets=facets or None,
    )


__all__ = ["run_list"]
