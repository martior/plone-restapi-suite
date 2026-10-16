"""@lock: POST (lock, stealable, timeout), GET, PATCH (refresh), DELETE
(unlock, force), and the effect of a lock on edits and deletes, by the
lock owner and by other users. Locks live on test content inside the
sandbox, so teardown removes them with the content."""

from __future__ import annotations

import time

import pytest

from restapi_suite.assertions import DENIED

pytestmark = pytest.mark.area("locking")

NICK = "nick"
PLONE = "plone"
# "refused because of the lock": Plone answers 403 for edits and 423 for deletes of a locked
# item, Nick 401 'Already locked'; RFC 4918 section 11.3 has 423 Locked, RFC 9110 409 Conflict.
LOCKED = (401, 403, 409, 423)


@pytest.fixture
def locked_doc(api, sandbox):
    """A document, two local editors (a, b), and a lock held by a."""
    path = api.doc(sandbox, "d", title="t0")
    a, b = api.create_user(), api.create_user()
    api.share(path, a, ["Editor"])
    api.share(path, b, ["Editor"])
    r = api.post(f"{path}/@lock", as_=a)
    assert r.status == 200, r
    return path, a, b, r.json["token"]


def _lock(api, path, as_="admin"):
    r = api.get(f"{path}/@lock", as_=as_)
    assert r.status == 200, r
    return r.json


# ------------------------------------------------------------------ lock / get

def test_lock_get_unlocked(api, sandbox):
    path = api.doc(sandbox, "d")
    assert _lock(api, path) == {"locked": False, "stealable": True}


def test_lock_post_shape(api, sandbox):
    path = api.doc(sandbox, "d")
    r = api.post(f"{path}/@lock")
    assert r.status == 200, r
    j = r.json
    assert {"created", "creator", "creator_name", "creator_url", "locked", "stealable", "time", "timeout",
            "token"} <= set(j), j
    assert j["locked"] is True and j["stealable"] is True and j["creator"] == api.t.admin.id, j
    assert j["timeout"] == 600 and j["token"], j


def test_lock_get_returns_same_token(api, locked_doc):
    path, a, b, token = locked_doc
    j = _lock(api, path, as_=b)
    assert j["locked"] is True and j["creator"] == a.id and j["token"] == token, j


def test_lock_custom_timeout(api, sandbox):
    path = api.doc(sandbox, "d")
    r = api.post(f"{path}/@lock", json={"timeout": 3600})
    assert r.status == 200 and r.json["timeout"] == 3600, r


def test_lock_non_stealable_reported(api, sandbox):
    path = api.doc(sandbox, "d")
    a = api.create_user()
    api.share(path, a, ["Editor"])
    assert api.post(f"{path}/@lock", as_=a, json={"stealable": False}).status == 200
    assert _lock(api, path, as_="admin")["stealable"] is False


def test_lock_relock_by_owner_keeps_token(api, locked_doc):
    path, a, b, token = locked_doc
    r = api.post(f"{path}/@lock", as_=a)
    assert r.status == 200 and r.json["token"] == token, r


@pytest.mark.compat("POST @lock on an item locked by another user answers 200 with the holder's lock instead "
                    "of 409/423; Volto's edit view POSTs @lock on entry and breaks on a failure: volto 19.5.0 "
                    "packages/volto/src/components/manage/Edit/Edit.jsx:526-529 (lockContent whenever content.lock "
                    "is defined), reducers/content/content.js:293-318 (LOCK_CONTENT_FAIL sets content.data to null), "
                    "Edit.jsx:310 (formData={content}). The edit itself is refused by the lock "
                    "(test_lock_token_from_get_does_not_let_other_editor_edit)")
@pytest.mark.known_divergence(NICK, kind="bug", reason="Nick refuses with 401 'Already locked'; Volto's edit view "
                              "then loses the content (see the compat note)")
def test_lock_by_other_user_returns_existing_lock(api, locked_doc):
    """The second user gets the holder's lock back and a's lock is left alone, so the client
    can show who holds it (locking.md: "she will see a message that this object is locked")."""
    path, a, b, token = locked_doc
    r = api.post(f"{path}/@lock", as_=b)
    assert r.status == 200 and r.json["locked"] is True and r.json["creator"] == a.id, r
    j = _lock(api, path)
    assert j["creator"] == a.id and j["token"] == token, j


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_lock_requires_modify(api, sandbox, persona):
    api.publish(sandbox)
    path = api.doc(sandbox, "d")
    api.publish(path)
    assert api.get(path, as_=persona).status == 200
    assert api.post(f"{path}/@lock", as_=persona).status in DENIED
    assert _lock(api, path)["locked"] is False


def test_lock_get_needs_view_only(api, sandbox):
    api.publish(sandbox)
    path = api.doc(sandbox, "d")
    api.publish(path)
    api.post(f"{path}/@lock")
    j = _lock(api, path, as_="anonymous")
    assert j["locked"] is True


def test_lock_get_private_as_anonymous_denied(api, sandbox):
    path = api.doc(sandbox, "d")
    assert api.get(f"{path}/@lock", as_="anonymous").status in DENIED


def test_lock_expires_after_timeout(api, sandbox):
    path = api.doc(sandbox, "d")
    a, b = api.create_user(), api.create_user()
    api.share(path, a, ["Editor"])
    api.share(path, b, ["Editor"])
    assert api.post(f"{path}/@lock", as_=a, json={"timeout": 1}).status == 200
    time.sleep(2.5)
    assert _lock(api, path)["locked"] is False
    assert api.patch(path, as_=b, json={"title": "after expiry"}).status == 204


# ------------------------------------------------------------------ edits under a lock

def test_lock_other_user_cannot_edit_without_token(api, locked_doc):
    path, a, b, token = locked_doc
    r = api.patch(path, as_=b, json={"title": "b"})
    assert r.status in (403, 401), r
    assert api.get(path).json["title"] == "t0"


def test_lock_edit_without_token_is_403_resource_locked(api, locked_doc):
    path, a, b, token = locked_doc
    r = api.patch(path, as_=b, json={"title": "b"})
    assert r.status == 403 and r.json["error"]["message"] == "Resource is locked.", r


def test_lock_owner_needs_token_too(api, locked_doc):
    """The token, not the user, unlocks editing: the owner without it is refused."""
    path, a, b, token = locked_doc
    assert api.patch(path, as_=a, json={"title": "a"}).status in (401, 403)
    assert api.get(path).json["title"] == "t0"


def test_lock_admin_cannot_edit_without_token(api, locked_doc):
    path, a, b, token = locked_doc
    assert api.patch(path, json={"title": "admin"}).status in (401, 403)


def test_lock_owner_edits_with_token(api, locked_doc):
    path, a, b, token = locked_doc
    r = api.patch(path, as_=a, json={"title": "a"}, headers={"Lock-Token": token})
    assert r.status == 204, r
    assert api.get(path).json["title"] == "a"


def test_lock_wrong_token_is_refused(api, locked_doc):
    path, a, b, token = locked_doc
    r = api.patch(path, as_=a, json={"title": "a"}, headers={"Lock-Token": "not-the-token"})
    assert r.status in (401, 403), r


@pytest.mark.known_divergence(PLONE, kind="plone-bug", ref="F1",
                              reason="the lock token alone authorizes the edit: another editor who learns the "
                                     "token (GET @lock and the content GET show it to anyone with View) can edit "
                                     "the locked item")
def test_lock_token_from_get_does_not_let_other_editor_edit(api, locked_doc):
    """The token proves that a request belongs to the lock holder's session; it must not
    let another user edit around the lock (locking.md "Updating a locked object")."""
    path, a, b, token = locked_doc
    leaked = _lock(api, path, as_=b)["token"]
    r = api.patch(path, as_=b, json={"title": "b"}, headers={"Lock-Token": leaked})
    assert r.status in LOCKED, r
    assert api.get(path).json["title"] == "t0"


def test_lock_survives_edit_with_token(api, locked_doc):
    path, a, b, token = locked_doc
    api.patch(path, as_=a, json={"title": "a"}, headers={"Lock-Token": token})
    j = _lock(api, path)
    assert j["locked"] is True and j["token"] == token, j


def test_lock_token_does_not_grant_permission(api, locked_doc):
    """A member without Modify cannot edit just by presenting the token."""
    path, a, b, token = locked_doc
    api.publish(path)
    u = api.create_user()
    r = api.patch(path, as_=u, json={"title": "u"}, headers={"Lock-Token": token})
    assert r.status in DENIED, r


def test_lock_blocks_delete_by_other_user(api, locked_doc):
    path, a, b, token = locked_doc
    r = api.delete(path, as_=b)
    assert r.status == 423, r
    assert api.get(path).status == 200


@pytest.mark.known_divergence(PLONE, kind="plone-bug", ref="F1", reason="workflow transitions ignore the lock")
def test_lock_blocks_workflow_transition_by_other_user(api, locked_doc):
    """A lock held by a blocks state changes by others, as it blocks edits and deletes. Classic
    Plone offers no transitions on an item locked for the current user
    (plone.app.contentmenu WorkflowMenu: is_locked_for_current_user)."""
    path, a, b, token = locked_doc
    assert api.post(f"{path}/@workflow/publish").status in LOCKED
    assert api.state(path) == "private"
    assert _lock(api, path)["locked"] is True


# ------------------------------------------------------------------ refresh

def test_lock_refresh_by_owner_keeps_token(api, locked_doc):
    path, a, b, token = locked_doc
    before = _lock(api, path)
    time.sleep(1.1)
    r = api.patch(f"{path}/@lock", as_=a)
    assert r.status == 200 and r.json["locked"] is True, r
    assert r.json["token"] == token, "refresh must not invalidate the client's token"
    assert api.patch(path, as_=a, json={"title": "after refresh"}, headers={"Lock-Token": token}).status == 204
    assert before["creator"] == r.json["creator"] == a.id


def test_lock_refresh_keeps_timeout(api, sandbox):
    path = api.doc(sandbox, "d")
    api.post(f"{path}/@lock", json={"timeout": 3600})
    r = api.patch(f"{path}/@lock")
    assert r.status == 200 and r.json["timeout"] == 3600, r


@pytest.mark.known_divergence(PLONE, kind="plone-bug", ref="F1",
                              reason="PATCH @lock by another editor refreshes someone else's lock (200) and "
                                     "returns its token instead of refusing")
def test_lock_refresh_by_other_user_is_refused(api, locked_doc):
    """Only the holder may extend a lock; otherwise anyone with edit rights keeps a's lock alive."""
    path, a, b, token = locked_doc
    r = api.patch(f"{path}/@lock", as_=b)
    assert r.status in LOCKED, r
    j = _lock(api, path)
    assert j["creator"] == a.id and j["token"] == token, j


def test_lock_refresh_unlocked_item(api, sandbox):
    """Refreshing an unlocked item does not create a lock."""
    path = api.doc(sandbox, "d")
    r = api.patch(f"{path}/@lock")
    assert r.status == 200, r
    assert r.json["locked"] is False and _lock(api, path)["locked"] is False


# ------------------------------------------------------------------ unlock

def test_unlock_by_owner(api, locked_doc):
    path, a, b, token = locked_doc
    r = api.delete(f"{path}/@lock", as_=a)
    assert r.status == 200 and r.json == {"locked": False, "stealable": True}, r
    assert _lock(api, path)["locked"] is False
    assert api.patch(path, as_=b, json={"title": "b"}).status == 204


def test_unlock_by_other_user_without_force_keeps_lock(api, locked_doc):
    path, a, b, token = locked_doc
    r = api.delete(f"{path}/@lock", as_=b)
    assert r.status in (200, 401, 403), r
    assert _lock(api, path)["locked"] is True


def test_unlock_force_steals_stealable_lock(api, locked_doc):
    path, a, b, token = locked_doc
    r = api.delete(f"{path}/@lock", as_=b, json={"force": True})
    assert r.status == 200 and r.json["locked"] is False, r
    assert _lock(api, path)["locked"] is False
    assert api.patch(path, as_=b, json={"title": "b"}).status == 204


def test_unlock_force_cannot_steal_non_stealable_lock(api, sandbox):
    path = api.doc(sandbox, "d")
    a, b = api.create_user(), api.create_user()
    api.share(path, a, ["Editor"])
    api.share(path, b, ["Editor"])
    api.post(f"{path}/@lock", as_=a, json={"stealable": False})
    api.delete(f"{path}/@lock", as_=b, json={"force": True})
    assert _lock(api, path)["locked"] is True


def test_unlock_force_by_admin_cannot_remove_non_stealable_lock(api, sandbox):
    path = api.doc(sandbox, "d")
    a = api.create_user()
    api.share(path, a, ["Editor"])
    api.post(f"{path}/@lock", as_=a, json={"stealable": False})
    r = api.delete(f"{path}/@lock", json={"force": True})
    assert r.status in (200, 401, 403), r
    assert _lock(api, path)["locked"] is True


@pytest.mark.known_divergence(PLONE, kind="plone-bug", ref="F1",
                              reason="a refused force-unlock still removes the non-stealable marker "
                                     "(services/locking/delete.py: noLongerProvides runs after the failed unlock), "
                                     "so a second force-unlock steals the lock")
def test_unlock_failed_force_keeps_lock_non_stealable(api, sandbox):
    """locking.md: a lock created with "stealable": false cannot be unlocked by another user,
    however often they try."""
    path = api.doc(sandbox, "d")
    a, b = api.create_user(), api.create_user()
    api.share(path, a, ["Editor"])
    api.share(path, b, ["Editor"])
    api.post(f"{path}/@lock", as_=a, json={"stealable": False})
    api.delete(f"{path}/@lock", as_=b, json={"force": True})
    assert _lock(api, path)["locked"] is True
    api.delete(f"{path}/@lock", as_=b, json={"force": True})
    j = _lock(api, path)
    assert j["locked"] is True and j["stealable"] is False, j


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_unlock_requires_modify(api, sandbox, persona):
    api.publish(sandbox)
    path = api.doc(sandbox, "d")
    api.publish(path)
    api.post(f"{path}/@lock")
    assert api.delete(f"{path}/@lock", as_=persona, json={"force": True}).status in DENIED
    assert _lock(api, path)["locked"] is True


def test_unlock_unlocked_item_is_noop(api, sandbox):
    path = api.doc(sandbox, "d")
    r = api.delete(f"{path}/@lock")
    assert r.status == 200 and r.json == {"locked": False, "stealable": True}, r


def test_lock_unlock_relock_gives_new_token(api, sandbox):
    path = api.doc(sandbox, "d")
    t1 = api.post(f"{path}/@lock").json["token"]
    api.delete(f"{path}/@lock")
    t2 = api.post(f"{path}/@lock").json["token"]
    assert t1 != t2
    assert api.patch(path, json={"title": "x"}, headers={"Lock-Token": t1}).status in (401, 403)
    assert api.patch(path, json={"title": "x"}, headers={"Lock-Token": t2}).status == 204
