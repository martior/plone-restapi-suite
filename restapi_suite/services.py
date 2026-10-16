"""Helpers for the workflow, sharing, history, users/groups, auth, locking
and misc-service tests (S4c).

Global site state
-----------------
Tests that change global state (registry, control panels, global roles of
shared principals, ...) must restore it and must not race with tests that
rely on the default. Two fixtures coordinate this across xdist workers with
an fcntl lock file per target base URL:

- `global_state` (exclusive): yields a `GlobalState`.
  `.controlpanel(name, {field: value})` PATCHes the fields each side's panel
  schema knows and restores their previous values at teardown;
  `.set(path, body, restore)` PATCHes anything on every side and registers
  the restoring request; `.defer(fn)` for anything else. Restores run in
  reverse order before the lock is released.
- `default_global_state` (shared): taken by tests that assert behaviour
  which a `global_state` test could change temporarily (for example "an
  anonymous user cannot register"). Many shared holders run in parallel;
  an exclusive holder waits for them and blocks new ones.

Import the fixtures into a test module with
`from restapi_suite.services import global_state, default_global_state  # noqa: F401`.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import tempfile
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import pytest

from .client import Resp, Side


def last_segment(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def transition_ids(r: Resp) -> list[str]:
    """Sorted transition ids of a GET @workflow response."""
    assert r.status == 200, r
    return sorted(last_segment(t["@id"]) for t in r.json.get("transitions", []))


def history_actions(r: Resp) -> list[tuple[str | None, str | None]]:
    """[(type, action)] of a GET @history response, newest first."""
    assert r.status == 200 and isinstance(r.json, list), r
    return [(e.get("type"), e.get("action")) for e in r.json]


def versions(r: Resp) -> list[dict]:
    assert r.status == 200 and isinstance(r.json, list), r
    return [e for e in r.json if e.get("type") == "versioning"]


def jwt_payload(token: str) -> dict:
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def with_token(side: Side, method: str, path: str, token: str | None, **kw) -> Resp:
    """Request with an explicit bearer token (or none), bypassing the cache."""
    headers = dict(kw.pop("headers", None) or {})
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return side.request(method, path, None, headers=headers, **kw)


def login(side: Side, login_: str, password: str) -> Resp:
    return side.request("POST", "@login", None, json={"login": login_, "password": password})


def user_items(j: Any) -> list[dict]:
    """@users / @groups listings: Plone 6.2 returns {items, items_total}, older
    plone.restapi and Nick a bare list."""
    if isinstance(j, dict):
        return j.get("items", [])
    return j or []


# -- global state coordination ------------------------------------------------

@contextmanager
def _flock(base: str, mode: int) -> Iterator[None]:
    key = hashlib.sha1(base.encode()).hexdigest()[:12]
    with open(f"{tempfile.gettempdir()}/restapi-suite-global-{key}.lock", "a+") as fh:
        fcntl.flock(fh, mode)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


class GlobalState:
    def __init__(self, api):
        self.api = api
        self._undo: list[Callable[[], Any]] = []

    def set(self, path: str, body: Callable[[Side], Any] | Any, restore: Callable[[Side], Any] | Any,
            method: str = "PATCH", expect=(200, 204)) -> Resp:
        """Change global state on every side; `restore` undoes it at teardown.
        Bodies may be callables of the side (side-specific payloads)."""
        def call(b):
            def fn(side: Side) -> Resp:
                return side.request(method, path, side.admin, json=b(side) if callable(b) else b)
            return fn

        r = self.api.each(call(body), expect=expect, what=f"global {method} {path}")
        self._undo.append(lambda: self.api.each(call(restore)))
        return r

    def controlpanel(self, name: str, values: dict) -> dict[str, dict]:
        """PATCH @controlpanels/<name> on every side with the subset of
        `values` that the side's panel schema knows (unknown keys are never
        sent, so no foreign keys get stored). The previous values are
        restored at teardown. Returns {side name: values actually set}."""
        applied: dict[str, dict] = {}
        for side in self.api.sides:
            r = side.request("GET", f"@controlpanels/{name}", side.admin)
            assert r.status == 200, r
            props = (r.json.get("schema") or {}).get("properties", {})
            keys = [k for k in values if k in props]
            applied[side.target.name] = {k: values[k] for k in keys}
            if not keys:
                continue
            old = {k: r.json["data"].get(k) for k in keys}
            p = side.request("PATCH", f"@controlpanels/{name}", side.admin, json=applied[side.target.name])
            assert p.status in (200, 204), p
            self._undo.append(lambda side=side, old=old: side.request(
                "PATCH", f"@controlpanels/{name}", side.admin, json=old))
        return applied

    def defer(self, fn: Callable[[], Any]) -> None:
        self._undo.append(fn)

    def restore(self) -> None:
        while self._undo:
            fn = self._undo.pop()
            try:
                fn()
            except Exception:  # best effort, keep restoring
                pass


@pytest.fixture
def global_state(api):
    with _flock(api.t.base, fcntl.LOCK_EX):
        gs = GlobalState(api)
        try:
            yield gs
        finally:
            gs.restore()


@pytest.fixture
def default_global_state(api):
    with _flock(api.t.base, fcntl.LOCK_SH):
        yield
