"""Basic permission matrix on content plus local-role semantics.

Matrix: persona x review_state (private, published) x action (view, edit,
delete, add child). The sandbox itself is published so traversal is never the
limiting factor; only the object's own state and roles matter.

Reference semantics (Plone 6.2, simple_publication_workflow + CMFPlone
rolemap): private View/Modify = Manager, Site Administrator, Owner, Editor,
Reader(View), Contributor(View); published View = Anonymous; Delete objects
and Add portal content are not workflow-managed.
"""

from __future__ import annotations

import pytest

from restapi_suite.api import PERSONAS
from restapi_suite.assertions import DENIED, item_ids, same_outcome

pytestmark = pytest.mark.area("permissions")

OK = {"view": 200, "edit": 204, "delete": 204, "add": 201}

# expected status per (state, action) -> persona. Plone 6.2 decides; see the
# report for measured values.
MATRIX = {
    ("private", "view"): dict(anonymous=401, member=401, local_editor=200, site_admin=200, manager=200),
    ("published", "view"): dict(anonymous=200, member=200, local_editor=200, site_admin=200, manager=200),
    ("private", "edit"): dict(anonymous=401, member=401, local_editor=204, site_admin=204, manager=204),
    ("published", "edit"): dict(anonymous=401, member=401, local_editor=204, site_admin=204, manager=204),
    ("private", "delete"): dict(anonymous=401, member=401, local_editor=204, site_admin=204, manager=204),
    ("published", "delete"): dict(anonymous=401, member=401, local_editor=204, site_admin=204, manager=204),
    ("private", "add"): dict(anonymous=401, member=401, local_editor=401, site_admin=201, manager=201),
    ("published", "add"): dict(anonymous=401, member=401, local_editor=401, site_admin=201, manager=201),
}

CASES = [
    pytest.param(state, action, persona, MATRIX[(state, action)][persona], id=f"{action}-{state}-{persona}")
    for (state, action) in MATRIX
    for persona in PERSONAS
]


def do(api, action, path, persona):
    if action == "view":
        return api.get(path, as_=persona)
    if action == "edit":
        return api.patch(path, as_=persona, json={"title": f"edited by {persona}"})
    if action == "delete":
        return api.delete(path, as_=persona)
    if action == "add":
        return api.post(path, as_=persona, json={"@type": "{type:document}", "id": "child", "title": "child"})
    raise ValueError(action)


@pytest.mark.parametrize("state,action,persona,expected", CASES)
def test_matrix(api, sandbox, state, action, persona, expected):
    api.publish(sandbox)
    # 'add' needs a container; the others act on a document.
    path = api.folder(sandbox, "obj") if action == "add" else api.doc(sandbox, "obj")
    if state == "published":
        api.publish(path)
    r = do(api, action, path, persona)
    assert same_outcome(expected, r.status), r
    # side effects only on success
    after = api.get(path, compare=False)
    if action == "delete":
        assert (after.status == 404) == (expected == OK["delete"]), after
    if action == "edit":
        assert (after.json["title"] == f"edited by {persona}") == (expected == OK["edit"])
    if action == "add":
        assert (api.get(f"{path}/child", compare=False).status == 200) == (expected == OK["add"])


# ------------------------------------------------------------ 401 vs 403

def test_unauthorized_authenticated_gets_401(api, sandbox):
    """plone.restapi maps zExceptions.Unauthorized to 401 for authenticated users too."""
    api.doc(sandbox, "d")
    api.publish(sandbox)
    r = api.get(f"{sandbox}/d", as_="member")
    assert r.status == 401, r
    assert r.json.get("type") == "Unauthorized", r


# ------------------------------------------------------------ local roles

def test_local_role_is_inherited_by_subfolders(api, sandbox):
    api.folder(sandbox, "sub")
    api.doc(f"{sandbox}/sub", "d")
    assert api.patch(f"{sandbox}/sub/d", as_="local_editor", json={"title": "x"}).status == 204


def test_blocked_inheritance_removes_local_editor(api, sandbox):
    api.folder(sandbox, "sub")
    api.doc(f"{sandbox}/sub", "d")
    api.block_inheritance(f"{sandbox}/sub")
    assert api.get(f"{sandbox}/sub/d", as_="local_editor").status in DENIED
    assert api.patch(f"{sandbox}/sub/d", as_="local_editor", json={"title": "x"}).status in DENIED


def test_blocked_inheritance_keeps_global_roles(api, sandbox):
    api.folder(sandbox, "sub")
    api.doc(f"{sandbox}/sub", "d")
    api.block_inheritance(f"{sandbox}/sub")
    assert api.get(f"{sandbox}/sub/d", as_="site_admin").status == 200
    assert api.patch(f"{sandbox}/sub/d", as_="site_admin", json={"title": "x"}).status == 204


def test_local_role_below_blocked_folder_applies(api, sandbox):
    api.folder(sandbox, "sub")
    api.doc(f"{sandbox}/sub", "d")
    api.block_inheritance(f"{sandbox}/sub")
    le = api.personas.users["local_editor"]
    api.share(f"{sandbox}/sub", le, ["Reader"], inherit=False)
    assert api.get(f"{sandbox}/sub/d", as_="local_editor").status == 200
    assert api.patch(f"{sandbox}/sub/d", as_="local_editor", json={"title": "x"}).status in DENIED


def test_unblocking_inheritance_restores_access(api, sandbox):
    api.folder(sandbox, "sub")
    api.doc(f"{sandbox}/sub", "d")
    api.block_inheritance(f"{sandbox}/sub")
    api._seed("POST", f"{sandbox}/sub/@sharing", json={"entries": [], "inherit": True})
    assert api.get(f"{sandbox}/sub/d", as_="local_editor").status == 200


def test_local_reader_can_view_not_edit(api, sandbox):
    u = api.create_user()
    api.doc(sandbox, "d")
    api.share(f"{sandbox}/d", u, ["Reader"])
    api.publish(sandbox)
    assert api.get(f"{sandbox}/d", as_=u).status == 200
    assert api.patch(f"{sandbox}/d", as_=u, json={"title": "x"}).status in DENIED
    assert api.delete(f"{sandbox}/d", as_=u).status in DENIED


def test_local_role_revocation(api, sandbox):
    u = api.create_user()
    api.doc(sandbox, "d")
    api.publish(sandbox)
    api.share(f"{sandbox}/d", u, ["Editor"])
    assert api.patch(f"{sandbox}/d", as_=u, json={"title": "x"}).status == 204
    api.share(f"{sandbox}/d", u, {"Editor": False})
    assert api.patch(f"{sandbox}/d", as_=u, json={"title": "y"}).status in DENIED


def test_group_local_role(api, sandbox):
    u = api.create_user()
    g = api.create_group(users=[u.id])
    api.doc(sandbox, "d")
    api.share(sandbox, g, ["Editor"], type_="group")
    api.publish(sandbox)
    assert api.patch(f"{sandbox}/d", as_=u, json={"title": "via group"}).status == 204


def test_group_global_role(api, sandbox):
    u = api.create_user()
    api.create_group(roles=["site_admin"], users=[u.id])
    api.doc(sandbox, "d")
    assert api.get(f"{sandbox}/d", as_=u).status == 200


def test_local_contributor_owns_what_it_creates(api, sandbox):
    u = api.create_user()
    api.share(sandbox, u, ["Contributor"])
    r = api.post(sandbox, as_=u, json={"@type": "{type:document}", "id": "mine", "title": "Mine"})
    assert r.status == 201, r
    assert api.patch(f"{sandbox}/mine", as_=u, json={"title": "Still mine"}).status == 204
    assert api.delete(f"{sandbox}/mine", as_=u).status == 204


def test_local_contributor_cannot_edit_others_content(api, sandbox):
    u = api.create_user()
    api.share(sandbox, u, ["Contributor"])
    api.doc(sandbox, "admins")
    assert api.get(f"{sandbox}/admins", as_=u).status == 200  # Contributor has View on private
    assert api.patch(f"{sandbox}/admins", as_=u, json={"title": "x"}).status in DENIED


def test_local_reviewer_on_pending(api, sandbox):
    u = api.create_user()
    api.publish(sandbox)
    api.doc(sandbox, "d")
    api.transition(f"{sandbox}/d", "submit")
    api.share(f"{sandbox}/d", u, ["Reviewer"])
    assert api.get(f"{sandbox}/d", as_=u).status == 200
    assert api.patch(f"{sandbox}/d", as_=u, json={"title": "reviewed"}).status == 204


def test_local_reviewer_cannot_delete_pending(api, sandbox):
    """Plone: Delete objects is not granted to Reviewer, although pending grants it Modify."""
    u = api.create_user()
    api.publish(sandbox)
    api.doc(sandbox, "d")
    api.transition(f"{sandbox}/d", "submit")
    api.share(f"{sandbox}/d", u, ["Reviewer"])
    assert api.delete(f"{sandbox}/d", as_=u).status in DENIED
    assert api.get(f"{sandbox}/d", compare=False).status == 200


@pytest.mark.parametrize("persona,expected", [("member", 401), ("local_editor", 200), ("site_admin", 200)],
                         ids=["member", "local_editor", "site_admin"])
def test_pending_view(api, sandbox, persona, expected):
    api.publish(sandbox)
    api.doc(sandbox, "d")
    api.transition(f"{sandbox}/d", "submit")
    assert same_outcome(expected, api.get(f"{sandbox}/d", as_=persona).status)


def test_published_doc_in_private_folder_anonymous(api, sandbox):
    """Plone 6.2: a published item below a private folder is reachable by URL."""
    api.folder(sandbox, "priv")
    api.doc(f"{sandbox}/priv", "d")
    api.publish(f"{sandbox}/priv/d")
    assert api.get(f"{sandbox}/priv/d", as_="anonymous").status == 200
    assert api.get(f"{sandbox}/priv", as_="anonymous").status in DENIED


def test_private_folder_listing_hides_private_children_from_member(api, sandbox):
    """Folder items are filtered by View: a member sees only the published child."""
    api.publish(sandbox)
    api.doc(sandbox, "priv")
    api.doc(sandbox, "pub")
    api.publish(f"{sandbox}/pub")
    r = api.get(sandbox, as_="member")
    assert item_ids(r.json) == ["pub"], r.json


def test_folder_listing_shows_private_children_to_local_editor(api, sandbox):
    api.doc(sandbox, "priv")
    r = api.get(sandbox, as_="local_editor")
    assert [i["@id"].rsplit("/", 1)[-1] for i in r.json["items"]] == ["priv"]


# ------------------------------------------------------------ @sharing

def test_sharing_get_lists_local_role(api, sandbox):
    r = api.get(f"{sandbox}/@sharing")
    assert r.status == 200, r
    le = api.personas.users["local_editor"].id
    entry = next((e for e in r.json["entries"] if e["id"] == le), None)
    assert entry and entry["roles"]["Editor"] is True, r.json["entries"]
    assert r.json["inherit"] is True


def test_sharing_get_shows_inherited_role(api, sandbox):
    api.folder(sandbox, "sub")
    le = api.personas.users["local_editor"].id
    r = api.get(f"{sandbox}/sub/@sharing", params={"search": le})
    entry = next((e for e in r.json["entries"] if e["id"] == le), None)
    assert entry and entry["roles"]["Editor"] == "acquired", r.json["entries"]


def test_sharing_post_without_inherit_key(api, sandbox):
    """inherit is optional in plone.restapi; omitting it must not change inheritance."""
    u = api.create_user()
    api.folder(sandbox, "sub")
    r = api.post(f"{sandbox}/sub/@sharing", json={"entries": [{"id": u.id, "type": "user", "roles": {"Reader": True}}]})
    assert r.status == 204, r
    assert api.get(f"{sandbox}/sub/@sharing", compare=False).json["inherit"] is True
    assert api.get(f"{sandbox}/sub", as_="local_editor").status == 200


@pytest.mark.parametrize("persona,expected", [("anonymous", 403), ("member", 403), ("local_editor", 204)],
                         ids=["anonymous", "member", "local_editor"])
@pytest.mark.known_divergence("plone", kind="plone-bug", ref="F1", ids=["anonymous", "member"],
                              reason="@sharing POST without permission answers 403 with the JSON body null, even "
                                     "for anonymous (every other service gives 401 + an error object)")
def test_sharing_post_permission(api, sandbox, persona, expected):
    """Plone: Change local roles is granted to Editor (CMFPlone rolemap 'Sharing page: Delegate roles').
    A refusal is an error object like on every other service, and anonymous gets 401 (RFC 9110
    15.5.2: authentication would help)."""
    u = api.create_user()
    api.publish(sandbox)
    r = api.post(f"{sandbox}/@sharing", as_=persona,
                 json={"entries": [{"id": u.id, "type": "user", "roles": {"Reader": True}}], "inherit": True})
    assert same_outcome(expected, r.status), r
    if expected in DENIED:
        assert isinstance(r.json, dict) and r.json, r
    if persona == "anonymous":
        assert r.status == 401, r


def test_sharing_editor_cannot_delegate_manager_role(api, sandbox):
    """Only delegatable roles can be granted; Manager is not one of them for an Editor."""
    u = api.create_user()
    api.publish(sandbox)
    api.post(f"{sandbox}/@sharing", as_="local_editor",
             json={"entries": [{"id": u.id, "type": "user", "roles": {"Manager": True}}], "inherit": True})
    assert api.get(f"{sandbox}/@types", as_=u, compare=False).status == 200
    r = api.post(sandbox, as_=u, json={"@type": "{type:document}", "id": "x", "title": "x"})
    assert r.status in DENIED, r


# ------------------------------------------------------------ search visibility

def _search_ids(api, path, persona):
    r = api.get(f"{path}/@search", as_=persona, params={"portal_type": "{type:document}"})
    assert r.status == 200, r
    return sorted(i["@id"].rsplit("/", 1)[-1] for i in r.json["items"])


@pytest.mark.parametrize("persona,expected", [
    ("anonymous", ["pub"]), ("member", ["pub"]), ("local_editor", ["priv", "pub"]),
    ("site_admin", ["priv", "pub"]), ("manager", ["priv", "pub"]),
], ids=list(PERSONAS))
def test_search_respects_view(api, sandbox, persona, expected):
    api.publish(sandbox)
    api.doc(sandbox, "priv")
    api.doc(sandbox, "pub")
    api.publish(f"{sandbox}/pub")
    assert _search_ids(api, sandbox, persona) == expected
