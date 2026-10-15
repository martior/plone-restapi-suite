"""Assertion helpers shared by test modules.

Convention: permission tests assert the *outcome* (allowed vs denied), not
the exact deny code, because Plone answers 401 to authenticated users where
other backends answer 403. The exact codes have their own tests
(test_permissions.py::test_unauthorized_authenticated_gets_401,
test_content_crud.py::test_get_private_as_anonymous_is_401).
"""

from __future__ import annotations

DENIED = (401, 403)


def outcome(status: int) -> str:
    if status in DENIED:
        return "denied"
    if 200 <= status < 300:
        return "allowed"
    return str(status)


def same_outcome(expected: int, actual: int) -> bool:
    return expected == actual or (expected in DENIED and actual in DENIED)


def assert_denied(r) -> None:
    assert r.status in DENIED, r


def assert_allowed(r, status: int | None = None) -> None:
    assert (r.status == status) if status else (200 <= r.status < 300), r


def item_ids(j) -> list[str]:
    """Ids of a listing/search result (Plone summaries have no "id" key)."""
    return [i["@id"].rstrip("/").rsplit("/", 1)[-1] for i in j["items"]]


def err_message(r) -> str:
    j = r.json or {}
    if isinstance(j, dict) and isinstance(j.get("error"), dict):
        return j["error"].get("message", "")
    if isinstance(j, dict):
        return j.get("message") or (j.get("error") if isinstance(j.get("error"), str) else "") or ""
    return ""
