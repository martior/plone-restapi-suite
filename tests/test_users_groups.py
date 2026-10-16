"""@users, @groups, @principals and @roles: CRUD, permissions per persona,
password policy, filtering/limit, and the effect of group membership on
access. Every user and group is created by the test (api.create_user /
create_group, deleted at teardown) or deleted explicitly."""

from __future__ import annotations

import secrets

import pytest

from restapi_suite.api import uid
from restapi_suite.assertions import DENIED, same_outcome
from restapi_suite.services import default_global_state, login, user_items  # noqa: F401

pytestmark = pytest.mark.area("users-groups")

NICK = "nick"
NO_MEMBER_ROLE = dict(reason="Nick has no Member role; users without roles get []", kind="deliberate")


def _pw() -> str:
    return f"Pw-{secrets.token_hex(8)}"


def _new_id(prefix="rsu") -> str:
    return f"{prefix}{uid(8)}"


def _post_user(api, as_="admin", cleanup=True, **body):
    """POST @users on every side; registers a cleanup for the username."""
    r = api.post("@users", as_=as_, json=body)
    if cleanup and body.get("username"):
        api.defer(lambda: api.delete_user(body["username"]))
    return r


def _ids(j) -> list[str]:
    return sorted(u["id"] for u in user_items(j))


def _user_groups(api, user_id) -> set[str]:
    """Group ids of a user from GET @users/<id> (Plone: {items: [...]}, Nick: a list)."""
    g = api.get(f"@users/{user_id}", compare=False).json["groups"]
    items = g["items"] if isinstance(g, dict) else g
    return {x["id"] if isinstance(x, dict) else x for x in items} - {"AuthenticatedUsers"}


# ------------------------------------------------------------------ create

def test_users_create_shape(api):
    uid_ = _new_id()
    r = _post_user(api, username=uid_, email=f"{uid_}@example.org", password=_pw(), fullname="Ada Lovelace")
    assert r.status == 201, r
    j = r.json
    assert j["@id"] == f"{api.t.base}/@users/{uid_}"
    assert (j["id"], j["username"], j["email"], j["fullname"]) == (uid_, uid_, f"{uid_}@example.org",
                                                                   "Ada Lovelace"), j
    assert "password" not in j and "roles" in j


def test_users_create_location_header(api):
    uid_ = _new_id()
    r = _post_user(api, username=uid_, email=f"{uid_}@example.org", password=_pw())
    assert r.headers.get("location") == f"{api.t.base}/@users/{uid_}", dict(r.headers)


@pytest.mark.known_divergence(NICK, **NO_MEMBER_ROLE)
def test_users_create_default_role_member(api):
    uid_ = _new_id()
    r = _post_user(api, username=uid_, email=f"{uid_}@example.org", password=_pw())
    assert r.json["roles"] == ["Member"], r


def test_users_create_with_global_role(api):
    u = api.create_user(roles=["Reviewer"])
    assert "Reviewer" in api.get(f"@users/{u.id}").json["roles"]


@pytest.mark.parametrize("persona,expected", [("anonymous", 401), ("member", 401), ("local_editor", 401),
                                              ("site_admin", 201), ("manager", 201)],
                         ids=["anonymous", "member", "local_editor", "site_admin", "manager"])
def test_users_create_permission(api, persona, expected, default_global_state):
    """Self-registration is off (default_global_state keeps it that way)."""
    uid_ = _new_id()
    r = _post_user(api, as_=persona, username=uid_, email=f"{uid_}@example.org", password=_pw())
    assert same_outcome(expected, r.status), r


@pytest.mark.parametrize("password", ["", "abc", "1234567"], ids=["empty", "3", "7"])
def test_users_create_short_password_is_400(api, password):
    uid_ = _new_id()
    r = _post_user(api, username=uid_, email=f"{uid_}@example.org", password=password)
    assert r.status == 400, r
    assert api.get(f"@users/{uid_}").status == 404


def test_users_create_password_of_8_is_ok(api):
    uid_ = _new_id()
    pw = "abcdefgh"
    assert _post_user(api, username=uid_, email=f"{uid_}@example.org", password=pw).status == 201
    assert login(api.t, uid_, pw).status == 200


def test_users_create_without_password_is_400(api):
    uid_ = _new_id()
    r = _post_user(api, username=uid_, email=f"{uid_}@example.org")
    assert r.status == 400, r


def test_users_create_without_username_is_400(api):
    r = _post_user(api, email=f"{_new_id()}@example.org", password=_pw())
    assert r.status == 400, r


def test_users_create_invalid_email_is_400(api):
    uid_ = _new_id()
    r = _post_user(api, username=uid_, email="not-an-email", password=_pw())
    assert r.status == 400, r
    assert api.get(f"@users/{uid_}").status == 404


def test_users_create_duplicate_is_400(api):
    u = api.create_user()
    r = _post_user(api, cleanup=False, username=u.id, email="other@example.org", password=_pw())
    assert r.status == 400, r
    assert login(api.t, u.id, u.password).status == 200, "original user's password must be untouched"


def test_users_create_cannot_overwrite_admin(api):
    r = _post_user(api, cleanup=False, username=api.t.admin.id, email="x@example.org", password=_pw())
    assert r.status == 400, r
    assert login(api.t, api.t.admin.id, api.t.admin.password).status == 200


def test_users_member_cannot_create_manager(api, default_global_state):
    uid_ = _new_id()
    r = _post_user(api, as_="member", username=uid_, email=f"{uid_}@example.org", password=_pw(),
                   roles=api.roles_for(api.t, ["manager"]))
    assert r.status in DENIED, r


# ------------------------------------------------------------------ read / list

@pytest.mark.parametrize("persona,allowed", [("anonymous", False), ("member", False), ("site_admin", True),
                                             ("manager", True)])
def test_users_list_permission(api, persona, allowed):
    r = api.get("@users", as_=persona)
    assert (r.status == 200) is allowed and (allowed or r.status in DENIED), r


def test_users_list_shape(api):
    r = api.get("@users")
    assert r.status == 200 and isinstance(r.json, dict), r
    assert r.json["@id"] == f"{api.t.base}/@users"
    assert r.json["items_total"] == len(r.json["items"])


def test_users_list_contains_created_user(api):
    u = api.create_user()
    assert u.id in _ids(api.get("@users", params={"query": u.id}).json)


def test_users_query_filters_by_id_substring(api):
    tag = uid(6)
    a = api.create_user(id=f"rsu{tag}a1")
    b = api.create_user(id=f"rsu{tag}b2")
    other = api.create_user()
    r = api.get("@users", params={"query": tag})
    ids = _ids(r.json)
    assert a.id in ids and b.id in ids and other.id not in ids, ids


def test_users_query_limit(api):
    tag = uid(6)
    for i in range(3):
        api.create_user(id=f"rsu{tag}{i:02d}")
    r = api.get("@users", params={"query": tag, "limit": "2"})
    assert len(user_items(r.json)) == 2, r


def test_users_search_by_fullname(api):
    u = api.create_user(fullname="Qwertzuiop Searchable")
    other = api.create_user()
    ids = _ids(api.get("@users", params={"search": "Qwertzuiop"}).json)
    assert u.id in ids and other.id not in ids, ids


def test_users_groups_filter(api):
    g = api.create_group()
    a, b = api.create_user(), api.create_user()
    api.add_to_group(g, a.id)
    ids = _ids(api.get("@users", params={"groups-filter:list": g}).json)
    assert a.id in ids and b.id not in ids, ids


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_users_query_denied(api, persona):
    assert api.get("@users", as_=persona, params={"query": "rs"}).status in DENIED


def test_users_read_self(api):
    u = api.create_user()
    r = api.get(f"@users/{u.id}", as_=u)
    assert r.status == 200 and r.json["id"] == u.id, r


@pytest.mark.parametrize("persona", ["anonymous", "member", "local_editor"])
def test_users_read_other_denied(api, persona):
    u = api.create_user()
    assert api.get(f"@users/{u.id}", as_=persona).status in DENIED


def test_users_read_other_as_site_admin(api):
    u = api.create_user()
    assert api.get(f"@users/{u.id}", as_="site_admin").status == 200


def test_users_read_unknown_is_404(api):
    assert api.get("@users/rs-no-such-user-zz").status == 404


@pytest.mark.known_divergence(NICK, reason="user groups are a bare list without AuthenticatedUsers",
                              kind="deliberate")
def test_users_read_lists_groups(api):
    g = api.create_group()
    u = api.create_user()
    api.add_to_group(g, u.id)
    groups = api.get(f"@users/{u.id}").json["groups"]
    assert {x["id"] for x in groups["items"]} == {g, "AuthenticatedUsers"}, groups


# ------------------------------------------------------------------ update

def test_users_update_own_fullname(api):
    u = api.create_user()
    r = api.patch(f"@users/{u.id}", as_=u, json={"fullname": "Self Edited"})
    assert r.status == 204, r
    assert api.get(f"@users/{u.id}").json["fullname"] == "Self Edited"


def test_users_update_other_denied(api):
    a, b = api.create_user(), api.create_user()
    r = api.patch(f"@users/{b.id}", as_=a, json={"fullname": "Hijacked"})
    assert r.status in DENIED, r
    assert api.get(f"@users/{b.id}").json["fullname"] != "Hijacked"


def test_users_member_cannot_grant_itself_roles(api):
    """Security property only (Plone 204 and ignores the roles; denying is fine too)."""
    u = api.create_user()
    escalate = {r: True for r in api.roles_for(api.t, ["manager", "site_admin"])}
    r = api.patch(f"@users/{u.id}", as_=u, json={"roles": escalate})
    assert r.status in (204,) + DENIED, r
    roles = api.get(f"@users/{u.id}").json["roles"]
    assert not set(escalate) & set(roles), roles
    assert api.get("@users", as_=u).status in DENIED


def test_users_admin_grants_and_revokes_global_role(api, sandbox):
    u = api.create_user()
    path = api.doc(sandbox, "d")
    api.transition(path, "submit")
    assert api.get(path, as_=u).status in DENIED
    assert api.patch(f"@users/{u.id}", json={"roles": {"Reviewer": True}}).status == 204
    assert "Reviewer" in api.get(f"@users/{u.id}").json["roles"]
    assert api.get(path, as_=u).status == 200
    assert api.patch(f"@users/{u.id}", json={"roles": {"Reviewer": False}}).status == 204
    assert "Reviewer" not in api.get(f"@users/{u.id}").json["roles"]
    assert api.get(path, as_=u).status in DENIED


def test_users_admin_sets_password(api):
    u = api.create_user()
    new = _pw()
    assert api.patch(f"@users/{u.id}", json={"password": new}).status == 204
    assert login(api.t, u.id, new).status == 200
    assert login(api.t, u.id, u.password).status == 401


@pytest.mark.plone_bug("PATCH @users bypasses the password policy: a 5 character password is stored")
@pytest.mark.known_divergence(NICK, reason="Nick ignores the password on PATCH", kind="plone-bug")
def test_users_admin_patch_short_password_accepted(api):
    u = api.create_user()
    assert api.patch(f"@users/{u.id}", json={"password": "short"}).status == 204
    assert login(api.t, u.id, "short").status == 200


@pytest.mark.plone_bug("PATCH @users accepts an invalid email address")
def test_users_patch_invalid_email_accepted(api):
    u = api.create_user()
    assert api.patch(f"@users/{u.id}", json={"email": "not-an-email"}).status == 204


def test_users_reset_own_password(api):
    u = api.create_user()
    new = _pw()
    r = api.post(f"@users/{u.id}/reset-password", as_=u, json={"old_password": u.password, "new_password": new})
    assert r.status == 200, r
    assert login(api.t, u.id, new).status == 200
    assert login(api.t, u.id, u.password).status == 401


def test_users_reset_own_password_wrong_old_password(api):
    u = api.create_user()
    new = _pw()
    r = api.post(f"@users/{u.id}/reset-password", as_=u, json={"old_password": "wrong-old-pw", "new_password": new})
    assert login(api.t, u.id, new).status == 401, "password changed without the right old password"
    assert login(api.t, u.id, u.password).status == 200
    assert r.status == 403, r


def test_users_reset_own_password_policy(api):
    u = api.create_user()
    r = api.post(f"@users/{u.id}/reset-password", as_=u, json={"old_password": u.password, "new_password": "x"})
    assert r.status == 400, r
    assert login(api.t, u.id, u.password).status == 200


def test_users_reset_other_users_password_denied(api):
    a, b = api.create_user(), api.create_user()
    new = _pw()
    r = api.post(f"@users/{b.id}/reset-password", as_=a, json={"old_password": a.password, "new_password": new})
    assert login(api.t, b.id, new).status == 401, "a member changed another user's password"
    assert login(api.t, b.id, b.password).status == 200
    assert r.status in (400,) + DENIED, r


# ------------------------------------------------------------------ delete

@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_users_delete_denied(api, persona):
    u = api.create_user()
    assert api.delete(f"@users/{u.id}", as_=persona).status in DENIED
    assert api.get(f"@users/{u.id}").status == 200


def test_users_delete_self_denied(api):
    u = api.create_user()
    assert api.delete(f"@users/{u.id}", as_=u).status in DENIED


def test_users_delete_then_404_and_login_fails(api):
    u = api.create_user(cleanup=False)
    assert api.delete(f"@users/{u.id}").status == 204
    assert api.get(f"@users/{u.id}").status == 404
    assert login(api.t, u.id, u.password).status == 401


def test_users_delete_unknown_is_404(api):
    assert api.delete("@users/rs-no-such-user-zz").status == 404


def test_users_deleted_users_token_stops_working(api):
    u = api.create_user(cleanup=False)
    tok = api.t.token(u, fresh=True)
    assert api.delete(f"@users/{u.id}").status == 204
    api.t.forget(u.id)
    r = api.t.request("GET", "@users", None, headers={"Authorization": f"Bearer {tok}"})
    assert r.status in DENIED, r


# ------------------------------------------------------------------ groups

def test_groups_create_shape(api):
    gid = f"rsg{uid(8)}"
    u = api.create_user()
    r = api.post("@groups", json={"groupname": gid, "title": "G T", "description": "D", "users": [u.id]})
    api.defer(lambda: [s.request("DELETE", f"@groups/{gid}", s.admin) for s in api.sides])
    assert r.status == 201, r
    j = r.json
    assert j["@id"] == f"{api.t.base}/@groups/{gid}"
    assert (j["id"], j["groupname"], j["title"], j["description"]) == (gid, gid, "G T", "D"), j


def test_groups_get_members(api):
    u = api.create_user()
    g = api.create_group(users=[u.id])
    j = api.get(f"@groups/{g}").json
    assert j["members"]["items"] == [u.id] and j["members"]["items_total"] == 1, j


def test_groups_patch_title_and_membership(api):
    a, b = api.create_user(), api.create_user()
    g = api.create_group(users=[a.id])
    r = api.patch(f"@groups/{g}", json={"title": "Renamed", "users": {a.id: False, b.id: True}})
    assert r.status == 204, r
    assert api.get(f"@groups/{g}").json["title"] == "Renamed"
    assert _user_groups(api, a.id) == set() and _user_groups(api, b.id) == {g}


def test_groups_create_duplicate_is_400(api):
    g = api.create_group()
    r = api.post("@groups", json={"groupname": g, "title": "dup"})
    assert r.status == 400, r


def test_groups_create_without_groupname_is_400(api):
    r = api.post("@groups", json={"title": "nameless"})
    assert r.status == 400, r


@pytest.mark.parametrize("persona,allowed", [("anonymous", False), ("member", False), ("site_admin", True)])
def test_groups_list_permission(api, persona, allowed):
    r = api.get("@groups", as_=persona)
    assert (r.status == 200) is allowed and (allowed or r.status in DENIED), r


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_groups_create_denied(api, persona):
    gid = f"rsg{uid(8)}"
    r = api.post("@groups", as_=persona, json={"groupname": gid, "title": gid})
    api.defer(lambda: [s.request("DELETE", f"@groups/{gid}", s.admin) for s in api.sides])
    assert r.status in DENIED, r


def test_groups_member_cannot_add_itself(api, sandbox):
    g = api.create_group(roles=["Reviewer"])
    u = api.create_user()
    path = api.doc(sandbox, "d")
    api.transition(path, "submit")
    r = api.patch(f"@groups/{g}", as_=u, json={"users": {u.id: True}})
    assert g not in _user_groups(api, u.id), "a plain member joined a Reviewer group"
    assert api.get(path, as_=u).status in DENIED
    assert r.status in DENIED, r


def test_groups_query(api):
    tag = uid(6)
    g = api.create_group(id=f"rsg{tag}01")
    other = api.create_group()
    r = api.get("@groups", params={"query": tag})
    ids = _ids(r.json)
    assert g in ids and other not in ids, ids


def test_groups_delete_then_404(api):
    g = api.create_group()
    assert api.delete(f"@groups/{g}").status == 204
    assert api.get(f"@groups/{g}").status == 404


def test_groups_delete_unknown_is_404(api):
    assert api.delete("@groups/rsg-no-such-group").status == 404


def test_groups_global_role_via_membership(api, sandbox):
    path = api.doc(sandbox, "d")
    api.transition(path, "submit")
    g = api.create_group(roles=["Reviewer"])
    u = api.create_user()
    assert api.get(path, as_=u).status in DENIED
    api.add_to_group(g, u.id)
    assert api.get(path, as_=u).status == 200
    assert api.post(f"{path}/@workflow/publish", as_=u).status == 200


def test_groups_removal_revokes_global_role(api, sandbox):
    path = api.doc(sandbox, "d")
    api.transition(path, "submit")
    u = api.create_user()
    g = api.create_group(roles=["Reviewer"], users=[u.id])
    assert api.get(path, as_=u).status == 200
    assert api.patch(f"@groups/{g}", json={"users": {u.id: False}}).status == 204
    assert api.get(path, as_=u).status in DENIED


def test_groups_deleting_group_revokes_local_role(api, sandbox):
    path = api.doc(sandbox, "d")
    u = api.create_user()
    g = api.create_group(users=[u.id])
    api.share(path, g, ["Reader"], type_="group")
    assert api.get(path, as_=u).status == 200
    assert api.delete(f"@groups/{g}").status == 204
    assert api.get(path, as_=u).status in DENIED


def test_groups_role_change_applies_to_members(api, sandbox):
    path = api.doc(sandbox, "d")
    u = api.create_user()
    g = api.create_group(users=[u.id])
    assert api.get(path, as_=u).status in DENIED
    assert api.patch(f"@groups/{g}", json={"roles": api.roles_for(api.t, ["Reader"])}).status == 204
    assert api.get(path, as_=u).status == 200


# ------------------------------------------------------------------ principals / roles

def test_principals_search_users_and_groups(api):
    tag = uid(6)
    u = api.create_user(id=f"rsu{tag}pp")
    g = api.create_group(id=f"rsg{tag}pp")
    r = api.get("@principals", params={"search": tag})
    assert r.status == 200 and set(r.json) == {"users", "groups"}, r
    assert [x["id"] for x in r.json["users"]] == [u.id]
    assert [x["id"] for x in r.json["groups"]] == [g]


def test_principals_search_without_term_is_400(api):
    assert api.get("@principals").status == 400


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_principals_denied(api, persona):
    assert api.get("@principals", as_=persona, params={"search": "rs"}).status in DENIED


def test_roles_list_shape(api):
    r = api.get("@roles")
    assert r.status == 200 and isinstance(r.json, list), r
    for role in r.json:
        assert set(role) == {"@id", "@type", "id", "title"} and role["@type"] == "role", role
        assert role["@id"] == f"{api.t.base}/@roles/{role['id']}"
    ids = {x["id"] for x in r.json}
    assert {"Contributor", "Editor", "Reader", "Reviewer"} <= ids, ids


@pytest.mark.known_divergence(NICK, reason="Nick's role list is its own (Administrator, Anonymous, "
                              "Authenticated, Owner; no Member/Manager/Site Administrator)", kind="deliberate")
def test_roles_list_plone_ids(api):
    assert [x["id"] for x in api.get("@roles").json] == [
        "Contributor", "Editor", "Member", "Reader", "Reviewer", "Site Administrator", "Manager"]


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_roles_denied_without_manage_portal(api, persona):
    assert api.get("@roles", as_=persona).status in DENIED
