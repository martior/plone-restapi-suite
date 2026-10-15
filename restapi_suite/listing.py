"""Helpers for the search, querystring and navigation tests.

- `phys(side, rel)`: the catalog (physical) path of a site-relative path on
  one side. Plone indexes `/plone/<rel>`, Nick `/<rel>`; without a virtual
  host both equal the base URL's path plus `/<rel>`.
- `get_each(api, path, as_, params_fn)`: GET on every side with
  side-specific query parameters (for `path.query`). Not diffed.
- `post_each(api, path, as_, json_fn)`: the same for POST bodies.
- `ids(resp)`: sorted last `@id` segments of a listing.
- `walk(items)` / `find_node(items, rel)`: navigate navigation trees.
- `QS`: the plone.app.querystring operation prefix used by Volto.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from .assertions import item_ids
from .client import Resp, Side

QS = "plone.app.querystring.operation."


def phys(side: Side, rel: str) -> str:
    root = urlparse(side.base).path.rstrip("/")
    rel = rel.strip("/")
    return f"{root}/{rel}" if rel else (root or "/")


def get_each(api, path: str, as_: Any, params_fn: Callable[[Side], dict]) -> Resp:
    return api.each(lambda side: side.request("GET", path, api._principal_for(side, as_), params=params_fn(side)))


def post_each(api, path: str, as_: Any, json_fn: Callable[[Side], Any]) -> Resp:
    return api.each(lambda side: side.request("POST", path, api._principal_for(side, as_), json=json_fn(side)))


def ids(r: Resp) -> list[str]:
    assert r.status == 200 and isinstance(r.json, dict) and "items" in r.json, r
    return sorted(item_ids(r.json))


def ordered_ids(r: Resp) -> list[str]:
    assert r.status == 200 and isinstance(r.json, dict) and "items" in r.json, r
    return item_ids(r.json)


def walk(items, depth: int = 1) -> Iterator[tuple[int, dict]]:
    for i in items or []:
        yield depth, i
        yield from walk(i.get("items"), depth + 1)


def find_node(items, side_base: str, rel: str) -> dict | None:
    want = f"{side_base.rstrip('/')}/{rel.strip('/')}"
    for _, node in walk(items):
        if node.get("@id", "").rstrip("/") == want:
            return node
    return None


def child_ids(node: dict | None) -> list[str]:
    if node is None:
        return []
    return sorted(i["@id"].rstrip("/").rsplit("/", 1)[-1] for i in node.get("items") or [])
