"""Regression tests for bulk route registration order."""

from __future__ import annotations

from typing import Any


def test_bulk_routes_are_not_shadowed_by_dynamic_routes() -> None:
    from main import app

    routes: list[tuple[str, set[str], Any]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", []) or [])
        path_regex = getattr(route, "path_regex", None)
        if path and methods and path_regex is not None:
            routes.append((path, methods, path_regex))

    shadowed: list[str] = []
    for index, (path, methods, _path_regex) in enumerate(routes):
        if "/bulk" not in path:
            continue
        for earlier_path, earlier_methods, earlier_regex in routes[:index]:
            common_methods = methods & earlier_methods
            if not common_methods or "{" not in earlier_path:
                continue
            if earlier_regex.fullmatch(path):
                method_list = ",".join(sorted(common_methods))
                shadowed.append(f"{method_list} {path} shadowed by {earlier_path}")

    assert shadowed == []
