"""@sharing: GET shape, available roles per persona, principal search,
POST semantics (grant, revoke, inherit on/off, multiple entries, groups,
unknown principals and roles) and their permission effects, checked by
subsequent reads as each persona. The basic local-role tests in
test_permissions.py (entry listed, acquired marker, POST permission) are not
repeated here."""

from __future__ import annotations

import pytest

from restapi_suite.assertions import DENIED, same_outcome

pytestmark = pytest.mark.area("sharing")

NICK = "nick"
PLONE_ROLES = ["Contributor", "Editor", "Reader", "Reviewer"]


def entries(r) -> dict[str, dict]:
    assert r.status == 200, r
    return {e["id"]: e for e in r.json["entries"]}


def role_ids(r) -> list[str]:
    assert r.status == 200, r
    return [x["id"] for x in r.json["available_roles"]]


def sharing(api, path, as_="admin", **params):
    return api.get(f"{path}/@sharing", as_=as_, params=params or None)


# ------------------------------------------------------------------ GET

def test_sharing_get_shape(api, sandbox):
    r = sharing(api, sandbox)
    assert r.status == 200, r
    assert set(r.json) == {"available_roles", "entries", "inherit"}, r.json
    assert r.json["inherit"] is True
    for role in r.json["available_roles"]:
        assert set(role) == {"id", "title"}, role
    for e in r.json["entries"]:
        assert {"id", "roles", "title", "type"} <= set(e), e
        assert e["type"] in ("user", "group"), e
        assert set(e["roles"]) == set(role_ids(r)), e


@pytest.mark.known_divergence(NICK, reason="Nick offers every role (Anonymous, Authenticated, Owner, "
                              "Administrator) and has no Plone role titles", kind="deliberate")
def test_sharing_available_roles_as_admin(api, sandbox):
    r = sharing(api, sandbox)
    assert role_ids(r) == PLONE_ROLES
    assert {x["id"]: x["title"] for x in r.json["available_roles"]} == {
        "Contributor": "Can add", "Editor": "Can edit", "Reader": "Can view", "Reviewer": "Can review"}


def test_sharing_available_roles_as_local_editor(api, sandbox):
    """An Editor may only delegate Editor and Reader (Plone's 'Sharing page:
    Delegate ... role' permissions)."""
    assert role_ids(sharing(api, sandbox, as_="local_editor")) == ["Editor", "Reader"]


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_sharing_get_denied_without_permission(api, sandbox, persona):
    assert sharing(api, sandbox, as_=persona).status in DENIED


def test_sharing_get_denied_for_local_reader(api, sandbox):
    """Reader can view the item but not its sharing information."""
    u = api.create_user()
    api.share(sandbox, u, ["Reader"])
    assert api.get(sandbox, as_=u).status == 200
    assert sharing(api, sandbox, as_=u).status in DENIED


def test_sharing_get_on_published_as_member_is_denied(api, sandbox):
    api.publish(sandbox)
    assert api.get(sandbox, as_="member").status == 200
    assert sharing(api, sandbox, as_="member").status in DENIED


def test_sharing_lists_authenticated_users_group(api, sandbox):
    e = entries(sharing(api, sandbox))
    assert "AuthenticatedUsers" in e, e
    assert e["AuthenticatedUsers"]["type"] == "group"
    assert not any(e["AuthenticatedUsers"]["roles"].values())


def test_sharing_own_entry_is_disabled_for_local_editor(api, sandbox):
    """The editor's own entry is listed but cannot be changed by them."""
    le = api.personas.users["local_editor"].id
    e = entries(sharing(api, sandbox, as_="local_editor"))
    assert e[le]["roles"]["Editor"] is True and e[le]["disabled"] is True, e[le]


def test_sharing_global_role_marked_global(api, sandbox):
    u = api.create_user(roles=["Reviewer"])
    e = entries(sharing(api, sandbox, search=u.id))
    assert e[u.id]["roles"]["Reviewer"] == "global", e.get(u.id)


def test_sharing_group_entry(api, sandbox):
    g = api.create_group()
    api.share(sandbox, g, ["Reader"], type_="group")
    e = entries(sharing(api, sandbox))
    assert e[g]["type"] == "group" and e[g]["roles"]["Reader"] is True, e.get(g)


def test_sharing_acquired_marker_disappears_when_inheritance_blocked(api, sandbox):
    sub = api.folder(sandbox, "sub")
    le = api.personas.users["local_editor"].id
    assert entries(sharing(api, sub, search=le))[le]["roles"]["Editor"] == "acquired"
    api.block_inheritance(sub)
    r = sharing(api, sub, search=le)
    assert r.json["inherit"] is False
    assert entries(r)[le]["roles"]["Editor"] is False


def test_sharing_local_and_acquired_together(api, sandbox):
    """A role granted locally on top of an acquired one is still reported as
    "acquired"; the purely local one as True."""
    sub = api.folder(sandbox, "sub")
    le = api.personas.users["local_editor"].id
    api.share(sub, le, ["Editor", "Reader"])
    roles = entries(sharing(api, sub))[le]["roles"]
    assert roles["Editor"] == "acquired" and roles["Reader"] is True, roles


# search --------------------------------------------------------------------

def test_sharing_search_finds_user_without_entry(api, sandbox):
    u = api.create_user()
    assert u.id not in entries(sharing(api, sandbox))
    e = entries(sharing(api, sandbox, search=u.id))
    assert u.id in e and e[u.id]["type"] == "user", list(e)
    assert not any(e[u.id]["roles"].values()), e[u.id]


def test_sharing_search_finds_group(api, sandbox):
    g = api.create_group(title="Sharing Search Group")
    e = entries(sharing(api, sandbox, search=g))
    assert g in e and e[g]["type"] == "group", list(e)


def test_sharing_search_by_fullname_fragment(api, sandbox):
    u = api.create_user(fullname="Zyxquorp Sharingtest")
    e = entries(sharing(api, sandbox, search="Zyxquorp"))
    assert u.id in e, list(e)


def test_sharing_search_no_match_keeps_existing_entries(api, sandbox):
    le = api.personas.users["local_editor"].id
    e = entries(sharing(api, sandbox, search="nobody-matches-this-qq"))
    assert le in e, list(e)


# ------------------------------------------------------------------ POST

def test_sharing_post_returns_204_and_grants(api, sandbox):
    api.doc(sandbox, "d")
    u = api.create_user()
    r = api.post(f"{sandbox}/d/@sharing", json={"entries": [{"id": u.id, "type": "user", "roles": {"Reader": True}}]})
    assert r.status == 204, r
    assert entries(sharing(api, f"{sandbox}/d"))[u.id]["roles"]["Reader"] is True
    assert api.get(f"{sandbox}/d", as_=u).status == 200


def test_sharing_post_multiple_entries(api, sandbox):
    a, b = api.create_user(), api.create_user()
    g = api.create_group()
    r = api.post(f"{sandbox}/@sharing", json={"entries": [
        {"id": a.id, "type": "user", "roles": {"Reader": True}},
        {"id": b.id, "type": "user", "roles": {"Contributor": True, "Reviewer": True}},
        {"id": g, "type": "group", "roles": {"Editor": True}}]})
    assert r.status == 204, r
    e = entries(sharing(api, sandbox))
    assert e[a.id]["roles"]["Reader"] is True
    assert e[b.id]["roles"]["Contributor"] is True and e[b.id]["roles"]["Reviewer"] is True
    assert e[g]["roles"]["Editor"] is True


def test_sharing_post_entry_replaces_managed_roles(api, sandbox):
    """An entry is the complete set of the principal's sharing roles: roles
    not named (or named False) are removed (plone.app.workflow
    update_role_settings)."""
    u = api.create_user()
    api.share(sandbox, u, ["Reader", "Contributor"])
    api.share(sandbox, u, {"Editor": True})
    roles = entries(sharing(api, sandbox))[u.id]["roles"]
    assert roles == {"Contributor": False, "Editor": True, "Reader": False, "Reviewer": False}, roles


def test_sharing_post_false_for_one_role_drops_the_others(api, sandbox):
    u = api.create_user()
    api.share(sandbox, u, ["Reader", "Contributor"])
    api.share(sandbox, u, {"Contributor": False})
    assert u.id not in entries(sharing(api, sandbox))
    assert api.get(sandbox, as_=u).status in DENIED


def test_sharing_post_keeps_other_principals(api, sandbox):
    a, b = api.create_user(), api.create_user()
    api.share(sandbox, a, ["Reader"])
    api.share(sandbox, b, ["Reader"])
    e = entries(sharing(api, sandbox))
    assert e[a.id]["roles"]["Reader"] is True and e[b.id]["roles"]["Reader"] is True


def test_sharing_revoke_all_removes_entry(api, sandbox):
    u = api.create_user()
    api.share(sandbox, u, ["Reader"])
    api.share(sandbox, u, {"Reader": False})
    assert u.id not in entries(sharing(api, sandbox))


def test_sharing_post_inherit_only(api, sandbox):
    sub = api.folder(sandbox, "sub")
    r = api.post(f"{sub}/@sharing", json={"inherit": False})
    assert r.status == 204, r
    assert sharing(api, sub).json["inherit"] is False
    assert api.get(sub, as_="local_editor").status in DENIED
    r = api.post(f"{sub}/@sharing", json={"inherit": True})
    assert r.status == 204, r
    assert api.get(sub, as_="local_editor").status == 200


def test_sharing_inherit_false_keeps_local_entries(api, sandbox):
    sub = api.folder(sandbox, "sub")
    u = api.create_user()
    api.share(sub, u, ["Reader"], inherit=False)
    r = sharing(api, sub)
    assert r.json["inherit"] is False
    assert entries(r)[u.id]["roles"]["Reader"] is True
    assert api.get(sub, as_=u).status == 200
    assert api.get(sub, as_="local_editor").status in DENIED


def test_sharing_inherit_false_on_published_keeps_anonymous_view(api, sandbox):
    """Blocking local-role inheritance does not touch workflow permissions."""
    api.publish(sandbox)
    sub = api.folder(sandbox, "sub")
    api.publish(sub)
    api.block_inheritance(sub)
    assert api.get(sub, as_="anonymous").status == 200


def test_sharing_inherit_false_keeps_global_editor_access_to_private(api, sandbox):
    """Global roles are not local roles: site admins still see a blocked subtree."""
    sub = api.folder(sandbox, "sub")
    api.block_inheritance(sub)
    assert api.get(sub, as_="site_admin").status == 200


@pytest.mark.plone_bug("local roles for a principal that does not exist are stored and listed (no 400)")
@pytest.mark.known_divergence(NICK, reason="unknown principals are not listed (Nick lists no entries at all)",
                              kind="plone-bug")
def test_sharing_post_unknown_principal(api, sandbox):
    r = api.post(f"{sandbox}/@sharing", json={"entries": [{"id": "rs-no-such-user-q", "type": "user",
                                                          "roles": {"Reader": True}}]})
    assert r.status == 204, r
    assert "rs-no-such-user-q" in entries(sharing(api, sandbox))


def test_sharing_post_undelegatable_roles_ignored(api, sandbox):
    """Roles outside available_roles (Manager, Site Administrator, unknown)
    are silently dropped, even for an admin."""
    u = api.create_user()
    r = api.post(f"{sandbox}/@sharing", json={"entries": [{"id": u.id, "type": "user", "roles": {
        "Manager": True, "Site Administrator": True, "Administrator": True, "NoSuchRole": True}}]})
    assert r.status == 204, r
    assert u.id not in entries(sharing(api, sandbox))
    assert api.get(sandbox, as_=u).status in DENIED


def test_sharing_post_empty_body_is_noop(api, sandbox):
    before = sharing(api, sandbox)
    r = api.post(f"{sandbox}/@sharing", json={})
    assert r.status == 204, r
    after = sharing(api, sandbox)
    assert entries(after) == entries(before) and after.json["inherit"] is True


def test_sharing_local_editor_can_grant_reader(api, sandbox):
    u = api.create_user()
    r = api.post(f"{sandbox}/@sharing", as_="local_editor",
                 json={"entries": [{"id": u.id, "type": "user", "roles": {"Reader": True}}]})
    assert r.status == 204, r
    assert api.get(sandbox, as_=u).status == 200


def test_sharing_local_editor_cannot_grant_reviewer(api, sandbox):
    """Reviewer is not in an Editor's available_roles: silently ignored."""
    u = api.create_user()
    api.doc(sandbox, "d")
    api.transition(f"{sandbox}/d", "submit")
    api.post(f"{sandbox}/@sharing", as_="local_editor",
             json={"entries": [{"id": u.id, "type": "user", "roles": {"Reviewer": True}}]})
    assert api.get(f"{sandbox}/d", as_=u).status in DENIED
    assert u.id not in entries(sharing(api, sandbox))


def test_sharing_local_editor_cannot_block_inheritance_for_admins(api, sandbox):
    """Blocking inheritance as local editor only blocks local roles; it would
    lock out the editor themselves, never a site admin."""
    sub = api.folder(sandbox, "sub")
    r = api.post(f"{sub}/@sharing", as_="local_editor", json={"inherit": False, "entries": []})
    assert r.status in (204,) + DENIED, r
    assert api.get(sub, as_="site_admin").status == 200


# effects ---------------------------------------------------------------------

@pytest.mark.parametrize("role,can_view,can_edit,can_add", [
    ("Reader", True, False, False),
    ("Editor", True, True, False),
    ("Contributor", True, False, True),
    ("Reviewer", False, False, False),
])
def test_sharing_role_effects_on_private_folder(api, sandbox, role, can_view, can_edit, can_add):
    f = api.folder(sandbox, "f")
    u = api.create_user()
    api.share(f, u, [role])
    assert (api.get(f, as_=u).status == 200) is can_view
    assert (api.patch(f, as_=u, json={"title": "changed"}).status == 204) is can_edit
    r = api.post(f, as_=u, json={"@type": "{type:document}", "id": "new", "title": "new"})
    assert (r.status == 201) is can_add, r


def test_sharing_reviewer_effect_on_pending(api, sandbox):
    path = api.doc(sandbox, "d")
    api.transition(path, "submit")
    u = api.create_user()
    assert api.get(path, as_=u).status in DENIED
    api.share(sandbox, u, ["Reviewer"])
    assert api.get(path, as_=u).status == 200
    assert api.post(f"{path}/@workflow/publish", as_=u).status == 200


def test_sharing_authenticated_users_group_grants_every_member(api, sandbox):
    path = api.doc(sandbox, "d")
    assert api.get(path, as_="member").status in DENIED
    api.share(path, "AuthenticatedUsers", ["Reader"], type_="group")
    assert api.get(path, as_="member").status == 200
    assert api.get(path, as_="anonymous").status in DENIED


def test_sharing_group_role_follows_membership(api, sandbox):
    path = api.doc(sandbox, "d")
    g = api.create_group()
    api.share(path, g, ["Editor"], type_="group")
    u = api.create_user()
    assert api.patch(path, as_=u, json={"title": "x"}).status in DENIED
    api.add_to_group(g, u.id)
    assert api.patch(path, as_=u, json={"title": "x"}).status == 204
    api._seed("PATCH", f"@groups/{g}", json={"users": {u.id: False}})
    assert api.patch(path, as_=u, json={"title": "y"}).status in DENIED


def test_sharing_revoke_on_parent_removes_access_to_child(api, sandbox):
    f = api.folder(sandbox, "f")
    d = api.doc(f, "d")
    u = api.create_user()
    api.share(f, u, ["Reader"])
    assert api.get(d, as_=u).status == 200
    api.share(f, u, {"Reader": False})
    assert api.get(d, as_=u).status in DENIED


@pytest.mark.parametrize("persona,expected", [("member", 401), ("local_editor", 200), ("site_admin", 200)],
                         ids=["member", "local_editor", "site_admin"])
def test_sharing_effect_seen_per_persona(api, sandbox, persona, expected):
    """After sharing a private doc with one user only, the persona matrix is
    unchanged for everybody else."""
    path = api.doc(sandbox, "d")
    api.share(path, api.create_user(), ["Reader"])
    assert same_outcome(expected, api.get(path, as_=persona).status)


def test_sharing_on_site_root_requires_manage(api):
    r = api.get("@sharing", as_="member")
    assert r.status in DENIED, r
    r = api.get("@sharing")
    assert r.status == 200 and "AuthenticatedUsers" in entries(r), r
