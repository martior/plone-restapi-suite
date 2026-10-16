"""One test per defect in reports/A2.md ("Surprises and defects") that is
observable over HTTP, plus the 2a permission-model item. Each encodes the
Plone 6.2 behaviour; known_divergence marks the expected Nick failure with
the A2 reference so a fixed Nick shows up as XPASS.

Not observable over HTTP with stock profiles (no test, see S4a report):
  A2#9 (one coarse "Add" permission): every stock Plone add permission is
       granted to the same roles as "Add portal content".
"""

from __future__ import annotations

import pytest

from restapi_suite.assertions import DENIED, item_ids, same_outcome

pytestmark = pytest.mark.area("a2-defects")

NICK = "nick"


def search_ids(api, path, persona, **params):
    r = api.get(f"{path}/@search", as_=persona, params={"portal_type": "{type:document}", **params})
    assert r.status == 200, r
    return sorted(i["@id"].rsplit("/", 1)[-1] for i in r.json["items"])


# ---------------------------------------------------------------- A2#1

def test_a2_1_search_sees_role_granted_after_creation(api, sandbox):
    u = api.create_user()
    api.publish(sandbox)
    api.doc(sandbox, "d")
    assert search_ids(api, sandbox, u) == []
    api.share(f"{sandbox}/d", u, ["Reader"])
    assert api.get(f"{sandbox}/d", as_=u).status == 200  # traversal sees the grant ...
    assert search_ids(api, sandbox, u) == ["d"]          # ... and so must search


def test_a2_1_search_hides_after_role_revoked(api, sandbox):
    u = api.create_user()
    api.publish(sandbox)
    api.folder(sandbox, "f")
    api.share(f"{sandbox}/f", u, ["Reader"])
    api.doc(f"{sandbox}/f", "d")  # indexed while u may view it
    assert search_ids(api, sandbox, u) == ["d"]
    api.share(f"{sandbox}/f", u, {"Reader": False})
    assert api.get(f"{sandbox}/f/d", as_=u).status in DENIED
    assert search_ids(api, sandbox, u) == [], "revoked user still finds the private document"


def test_a2_1_search_hides_after_inheritance_blocked(api, sandbox):
    api.publish(sandbox)
    api.folder(sandbox, "f")
    api.publish(f"{sandbox}/f")
    api.doc(f"{sandbox}/f", "d")
    u = api.create_user()
    api.share(sandbox, u, ["Reader"])  # inherited by f ...
    api.doc(f"{sandbox}/f", "d2")      # ... so d2 is indexed with u allowed
    api.block_inheritance(f"{sandbox}/f")
    assert api.get(f"{sandbox}/f/d2", as_=u).status in DENIED
    assert "d2" not in search_ids(api, sandbox, u)


# ---------------------------------------------------------------- A2#2

def test_a2_2_local_editor_finds_private_doc(api, sandbox):
    api.publish(sandbox)
    api.doc(sandbox, "d")
    assert api.get(f"{sandbox}/d", as_="local_editor").status == 200
    assert search_ids(api, sandbox, "local_editor") == ["d"]


def test_a2_2_local_contributor_finds_private_doc(api, sandbox):
    u = api.create_user()
    api.publish(sandbox)
    api.share(sandbox, u, ["Contributor"])
    api.doc(sandbox, "d")
    assert api.get(f"{sandbox}/d", as_=u).status == 200
    assert search_ids(api, sandbox, u) == ["d"]


def test_a2_2_local_editor_sees_private_doc_in_listing(api, sandbox):
    api.publish(sandbox)
    api.folder(sandbox, "f")
    api.doc(f"{sandbox}/f", "d")
    r = api.get(f"{sandbox}/f", as_="local_editor")
    assert r.status == 200, r
    assert [i["@id"].rsplit("/", 1)[-1] for i in r.json["items"]] == ["d"]


# ---------------------------------------------------------------- A2#3

@pytest.fixture
def role_named_group(api):
    """A group whose id equals a role id (Editor). Plone keeps principals and
    roles in separate namespaces; Nick puts both, unprefixed, into one ACL array."""
    gid = "Editor"
    for side in api.sides:  # leftovers from an aborted run
        side.request("DELETE", f"@groups/{gid}", side.admin)
    api.create_group(id=gid, title="Group named like a role")
    return gid


def test_a2_3_group_named_like_role_does_not_leak_private_docs(api, sandbox, role_named_group):
    u = api.create_user()
    api.add_to_group(role_named_group, u.id)
    api.publish(sandbox)
    api.doc(sandbox, "secret")
    assert api.get(f"{sandbox}/secret", as_=u).status in DENIED
    assert search_ids(api, sandbox, u) == [], "member of group 'Editor' finds a private doc via search"


def test_a2_3_user_named_like_role_does_not_leak_private_docs(api, sandbox):
    for side in api.sides:
        side.request("DELETE", "@users/Reader", side.admin)
    u = api.create_user(id="Reader")
    api.publish(sandbox)
    api.doc(sandbox, "secret")
    assert api.get(f"{sandbox}/secret", as_=u).status in DENIED
    assert search_ids(api, sandbox, u) == [], "user 'Reader' finds a private doc via search"


# ---------------------------------------------------------------- A2#4

def test_a2_4_transition_not_available_in_state_is_400(api, sandbox):
    """published -> submit is not a transition of 'published' (only reject/retract)."""
    api.doc(sandbox, "d")
    api.publish(f"{sandbox}/d")
    r = api.post(f"{sandbox}/d/@workflow/submit")
    assert r.status == 400, r
    assert api.state(f"{sandbox}/d") == "published"


def test_a2_4_same_state_transition_is_a_noop_200(api, sandbox):
    """Plone 6.2 tolerates a transition whose destination is the current state
    (services/workflow/transition.py is_same_state): 200, state unchanged."""
    api.doc(sandbox, "d")
    api.publish(f"{sandbox}/d")
    r = api.post(f"{sandbox}/d/@workflow/publish")
    assert r.status == 200, r
    assert api.state(f"{sandbox}/d") == "published"


def test_a2_4_unknown_transition_is_400(api, sandbox):
    api.doc(sandbox, "d")
    r = api.post(f"{sandbox}/d/@workflow/no-such-transition")
    assert r.status == 400, r


def test_a2_4_include_children_checks_each_child(api, sandbox):
    """A local Reviewer of f publishes f with include_children. f/sub blocks
    inheritance, so the reviewer has no rights on f/sub/d: d must stay private."""
    u = api.create_user()
    api.folder(sandbox, "f")
    api.folder(f"{sandbox}/f", "sub")
    api.doc(f"{sandbox}/f/sub", "d")
    api.block_inheritance(f"{sandbox}/f/sub")
    api.share(f"{sandbox}/f", u, ["Reviewer"])
    api.post(f"{sandbox}/f/@workflow/publish", as_=u, json={"include_children": True})
    assert api.state(f"{sandbox}/f/sub/d") == "private"


# ---------------------------------------------------------------- A2#5

@pytest.mark.parametrize("persona,expected", [("anonymous", 401), ("member", 401)], ids=["anonymous", "member"])
def test_a2_5_revert_requires_revert_permission(api, sandbox, persona, expected):
    api.publish(sandbox)
    api.doc(sandbox, "d", title="v0")
    api.patch(f"{sandbox}/d", json={"title": "v1"}, compare=False)
    api.publish(f"{sandbox}/d")
    r = api.patch(f"{sandbox}/d/@history", as_=persona, json={"version": 0})
    assert same_outcome(expected, r.status), r
    assert api.get(f"{sandbox}/d", compare=False).json["title"] == "v1"


def test_a2_5_may_revert_false_for_reader(api, sandbox):
    u = api.create_user()
    api.doc(sandbox, "d", title="v0")
    api.patch(f"{sandbox}/d", json={"title": "v1"}, compare=False)
    api.share(f"{sandbox}/d", u, ["Reader"])
    r = api.get(f"{sandbox}/d/@history", as_=u)
    if r.status in (401, 403):
        return  # Plone: a Reader may not even list versions; that also satisfies "no revert"
    versions = [e for e in r.json if e.get("type") == "versioning"]
    assert versions and not any(e.get("may_revert") for e in versions), versions


def test_a2_5_revert_reindexes(api, sandbox):
    api.doc(sandbox, "d", title="alphaoriginal")
    api.patch(f"{sandbox}/d", json={"title": "betachanged"}, compare=False)
    assert api.patch(f"{sandbox}/d/@history", json={"version": 0}).status == 200
    assert search_ids(api, sandbox, "admin", SearchableText="alphaoriginal") == ["d"]
    assert search_ids(api, sandbox, "admin", SearchableText="betachanged") == []


# ---------------------------------------------------------------- A2#6

def test_a2_6_workflow_history_cannot_be_forged_on_create(api, sandbox):
    forged = [{"action": "publish", "actor": "mallory-forged", "review_state": "published",
               "time": "2001-01-01T00:00:00+00:00", "comments": "forged entry"}]
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "d", "title": "d", "workflow_history": forged})
    assert r.status == 201, r
    h = api.get(f"{sandbox}/d/@history")
    assert h.status == 200, h
    assert "mallory-forged" not in h.text and "forged entry" not in h.text, h


def test_a2_6_transition_comment_is_recorded(api, sandbox):
    api.doc(sandbox, "d")
    r = api.post(f"{sandbox}/d/@workflow/publish", json={"comment": "because-s4a"})
    assert r.status == 200, r
    assert r.json.get("comments") == "because-s4a", r
    h = api.get(f"{sandbox}/d/@history").json
    entry = next(e for e in h if e.get("action") == "publish")
    assert entry["comments"] == "because-s4a", entry


def test_a2_6_workflow_get_lists_history(api, sandbox):
    api.doc(sandbox, "d")
    api.publish(f"{sandbox}/d")
    h = api.get(f"{sandbox}/d/@workflow").json["history"]
    assert [e["review_state"] for e in h] == ["private", "published"], h


# ---------------------------------------------------------------- A2#7

def test_a2_7_deleting_user_keeps_their_content(api, sandbox):
    u = api.create_user(cleanup=False)
    api.share(sandbox, u, ["Contributor"])
    r = api.post(sandbox, as_=u, json={"@type": "{type:folder}", "id": "theirs", "title": "theirs"})
    assert r.status == 201, r
    api.post(f"{sandbox}/theirs", as_=u, json={"@type": "{type:document}", "id": "deep", "title": "deep"})
    assert api.delete(f"@users/{u.id}").status == 204
    assert api.get(f"{sandbox}/theirs").status == 200
    assert api.get(f"{sandbox}/theirs/deep").status == 200


def test_a2_7_deleting_user_keeps_versions_they_authored(api, sandbox):
    u = api.create_user(cleanup=False)
    api.doc(sandbox, "d", title="v0")
    api.share(f"{sandbox}/d", u, ["Editor"])
    api.patch(f"{sandbox}/d", as_=u, json={"title": "v1 by user"})
    before = [e for e in api.get(f"{sandbox}/d/@history", compare=False).json if e.get("type") == "versioning"]
    assert api.delete(f"@users/{u.id}").status == 204
    after = [e for e in api.get(f"{sandbox}/d/@history", compare=False).json if e.get("type") == "versioning"]
    assert len(after) == len(before), (before, after)


# ---------------------------------------------------------------- A2#8

def test_a2_8_owner_can_view_and_edit_own_private_item(api, sandbox):
    u = api.create_user()
    api.share(sandbox, u, ["Contributor"])
    assert api.post(sandbox, as_=u, json={"@type": "{type:document}", "id": "mine", "title": "m"}).status == 201
    api.share(sandbox, u, {"Contributor": False})  # now only Owner of 'mine'
    assert api.get(f"{sandbox}/mine", as_=u).status == 200
    assert api.patch(f"{sandbox}/mine", as_=u, json={"title": "m2"}).status == 204


# ---------------------------------------------------------------- A2 2a

def test_a2_2a_global_reader_views_private(api, sandbox):
    """With stock profiles the additive model is not observable for View: the
    only Nick role with global View is Reader, and Plone's private state grants
    Reader View too. Kept as a guard for the (breaking) 2a rewrite."""
    u = api.create_user(roles=["Reader"])
    api.doc(sandbox, "d")
    assert api.get(f"{sandbox}/d", as_=u).status == 200


def test_a2_2a_global_reviewer_cannot_view_private(api, sandbox):
    u = api.create_user(roles=["Reviewer"])
    api.doc(sandbox, "d")
    assert api.get(f"{sandbox}/d", as_=u).status in DENIED


def test_a2_2a_pending_removes_modify_from_global_editor(api, sandbox):
    """pending: Modify portal content = Manager, Reviewer, Site Administrator only.
    Passes on Nick too: Nick's Editor role holds no global Modify (only Submit),
    so the additive model does not bite here either."""
    u = api.create_user(roles=["Editor"])
    api.doc(sandbox, "d")
    api.transition(f"{sandbox}/d", "submit")
    assert api.get(f"{sandbox}/d", as_=u).status == 200
    assert api.patch(f"{sandbox}/d", as_=u, json={"title": "x"}).status in DENIED
