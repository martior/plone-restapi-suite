"""Read-only basics of @vocabularies and @controlpanels, @actions per
persona, @aliases (automatic redirects after rename/move, manual aliases),
and one global-state round trip (self-registration) that uses the
`global_state` fixture from restapi_suite.services."""

from __future__ import annotations

from urllib.parse import urljoin

import pytest

from restapi_suite.api import uid
from restapi_suite.assertions import DENIED
from restapi_suite.services import default_global_state, global_state, login  # noqa: F401

pytestmark = pytest.mark.area("misc-services")

NICK = "nick"


def _actions(r) -> dict[str, list[str]]:
    assert r.status == 200 and isinstance(r.json, dict), r
    return {cat: [a["id"] for a in items] for cat, items in r.json.items()}


def _redirect_target(r) -> str:
    """Absolute redirect target (Location may be relative)."""
    return urljoin(r.url, r.headers.get("location", ""))


# ------------------------------------------------------------------ vocabularies

def test_vocabularies_list_shape(api):
    r = api.get("@vocabularies")
    assert r.status == 200 and isinstance(r.json, list) and r.json, r
    for v in r.json:
        assert set(v) == {"@id", "title"} and v["@id"].startswith(f"{api.t.base}/@vocabularies/"), v


def test_vocabularies_list_is_public(api):
    """Listing vocabulary names is public; individual vocabularies check permissions."""
    r = api.get("@vocabularies", as_="anonymous")
    assert r.status == 200 and isinstance(r.json, list), r


@pytest.mark.parametrize("name", ["plone.app.vocabularies.ReallyUserFriendlyTypes",
                                  "plone.app.vocabularies.WorkflowStates", "plone.app.vocabularies.Roles",
                                  "plone.app.vocabularies.Keywords", "plone.app.vocabularies.Users",
                                  "plone.app.vocabularies.Groups"])
def test_vocabularies_plone_names_exist(api, name):
    r = api.get(f"@vocabularies/{name}")
    assert r.status == 200, r
    assert r.json["@id"] == f"{api.t.base}/@vocabularies/{name}"
    assert "items" in r.json and "items_total" in r.json


def test_vocabulary_item_shape(api):
    r = api.get("@vocabularies/plone.app.vocabularies.WorkflowStates")
    assert r.status == 200, r
    for item in r.json["items"]:
        assert set(item) == {"title", "token"}, item
    tokens = {i["token"] for i in r.json["items"]}
    assert {"private", "pending", "published"} <= tokens, tokens


def test_vocabulary_title_filter(api):
    r = api.get("@vocabularies/plone.app.vocabularies.ReallyUserFriendlyTypes", params={"title": "fold"})
    assert r.status == 200, r
    assert [i["token"] for i in r.json["items"]] == [api.type("folder")], r.json


def test_vocabulary_token_filter(api):
    r = api.get("@vocabularies/plone.app.vocabularies.WorkflowStates", params={"token": "published"})
    assert r.status == 200 and [i["token"] for i in r.json["items"]] == ["published"], r


def test_vocabulary_batching(api):
    r = api.get("@vocabularies/plone.app.vocabularies.ReallyUserFriendlyTypes", params={"b_size": "2"})
    assert r.status == 200, r
    assert len(r.json["items"]) == 2 and r.json["items_total"] > 2, r.json
    assert "batching" in r.json, r.json


def test_vocabulary_unknown_is_404(api):
    r = api.get("@vocabularies/rs.no.such.vocabulary")
    assert r.status == 404, r


def test_vocabulary_protected_for_anonymous(api):
    """Keywords and Users need authentication / extra permissions."""
    for name in ("plone.app.vocabularies.Keywords", "plone.app.vocabularies.Users"):
        assert api.get(f"@vocabularies/{name}", as_="anonymous").status in DENIED, name


def test_vocabulary_users_denied_for_member(api):
    assert api.get("@vocabularies/plone.app.vocabularies.Users", as_="member").status in DENIED


# ------------------------------------------------------------------ controlpanels

def test_controlpanels_list_shape(api):
    r = api.get("@controlpanels")
    assert r.status == 200 and isinstance(r.json, list), r
    for p in r.json:
        assert {"@id", "group", "title"} <= set(p), p
        assert p["@id"].startswith(f"{api.t.base}/@controlpanels/")
    ids = {p["@id"].rsplit("/", 1)[-1] for p in r.json}
    assert {"security", "mail", "navigation", "site", "language", "usergroup", "dexterity-types"} <= ids, ids


@pytest.mark.parametrize("persona", ["anonymous", "member", "local_editor"])
def test_controlpanels_denied(api, persona):
    assert api.get("@controlpanels", as_=persona).status in DENIED
    assert api.get("@controlpanels/security", as_=persona).status in DENIED


def test_controlpanels_site_admin_allowed(api):
    assert api.get("@controlpanels/security", as_="site_admin").status == 200


def test_controlpanel_shape(api):
    r = api.get("@controlpanels/security")
    assert r.status == 200, r
    j = r.json
    assert j["@id"] == f"{api.t.base}/@controlpanels/security" and j["group"] == "Security", j
    assert set(j["data"]) == set(j["schema"]["properties"]), j
    fields = [f for fs in j["schema"]["fieldsets"] for f in fs["fields"]]
    assert sorted(fields) == sorted(j["schema"]["properties"])


@pytest.mark.known_divergence(NICK, reason="Nick's security panel only has use_email_as_login", kind="deliberate")
def test_controlpanel_security_fields(api):
    data = api.get("@controlpanels/security").json["data"]
    assert {"enable_self_reg", "enable_user_pwd_choice", "use_email_as_login"} <= set(data), data
    assert data["enable_self_reg"] is False and data["use_email_as_login"] is False


def test_controlpanel_unknown_is_404(api):
    assert api.get("@controlpanels/rs-no-such-panel").status == 404


@pytest.mark.parametrize("persona", ["member", "local_editor"])
def test_controlpanel_patch_denied(api, persona, default_global_state):
    r = api.patch("@controlpanels/security", as_=persona, json={"enable_self_reg": True})
    assert r.status in DENIED, r
    assert api.get("@controlpanels/security").json["data"].get("enable_self_reg", False) is False


# global state round trip -------------------------------------------------------

def test_self_registration_toggle_is_restored(api, global_state):
    """Enable self registration, register anonymously, restore. The
    restore runs in teardown under the exclusive global-state lock, so
    tests holding `default_global_state` never see the change."""
    global_state.controlpanel("security", {"enable_self_reg": True, "enable_user_pwd_choice": True})
    assert api.get("@controlpanels/security").json["data"].get("enable_self_reg") is True
    uid_ = f"rsu{uid(8)}"
    pw = "Pw-selfreg-12345"
    api.defer(lambda: api.delete_user(uid_))
    r = api.post("@users", as_="anonymous", json={"username": uid_, "email": f"{uid_}@example.org", "password": pw})
    assert r.status == 201, r
    assert login(api.t, uid_, pw).status == 200


def test_self_registration_cannot_choose_roles(api, global_state):
    global_state.controlpanel("security", {"enable_self_reg": True, "enable_user_pwd_choice": True})
    uid_ = f"rsu{uid(8)}"
    api.defer(lambda: api.delete_user(uid_))
    r = api.post("@users", as_="anonymous", json={"username": uid_, "email": f"{uid_}@example.org",
                                                  "password": "Pw-selfreg-12345",
                                                  "roles": api.roles_for(api.t, ["manager"])})
    assert r.status == 400 or r.status in DENIED, r
    got = api.get(f"@users/{uid_}")
    assert got.status == 404 or not set(api.roles_for(api.t, ["manager"])) & set(got.json["roles"]), got


def test_self_registration_off_by_default(api, default_global_state):
    uid_ = f"rsu{uid(8)}"
    api.defer(lambda: api.delete_user(uid_))
    r = api.post("@users", as_="anonymous", json={"username": uid_, "email": f"{uid_}@example.org",
                                                  "password": "Pw-selfreg-12345"})
    assert r.status in DENIED, r


# ------------------------------------------------------------------ actions

def test_actions_shape(api, sandbox):
    r = api.get(f"{sandbox}/@actions")
    a = _actions(r)
    assert {"object", "object_buttons", "site_actions", "user"} <= set(a), a
    for items in r.json.values():
        for item in items:
            assert {"id", "title"} <= set(item), item


@pytest.mark.parametrize("persona,expected", [
    ("anonymous", {"object": ["view"], "user": ["login"]}),
    ("member", {"object": ["view"], "user": ["preferences", "logout"]}),
    ("local_editor", {"object": ["view", "edit", "folderContents", "history", "local_roles"],
                      "user": ["preferences", "logout"]}),
    ("site_admin", {"object": ["view", "edit", "folderContents", "history", "local_roles"],
                    "user": ["preferences", "plone_setup", "logout"]}),
])
def test_actions_per_persona(api, sandbox, persona, expected, default_global_state):
    """On a published folder; categories that differ in Nick (dashboard,
    register, contentrules) are not compared. Self-registration adds 'join'
    for anonymous, hence default_global_state."""
    api.publish(sandbox)
    a = _actions(api.get(f"{sandbox}/@actions", as_=persona))
    ignore = {"dashboard", "register", "contentrules"}
    for cat, ids in expected.items():
        assert [i for i in a.get(cat, []) if i not in ignore] == ids, (cat, a)


def test_actions_object_buttons_need_edit(api, sandbox):
    """On a private child of the sandbox the local Editor may cut/delete/rename
    (delete needs 'Delete objects' on the parent too). Published: a member
    sees none of them."""
    f = api.folder(sandbox, "f")
    editor = _actions(api.get(f"{f}/@actions", as_="local_editor")).get("object_buttons", [])
    api.publish(sandbox)
    api.publish(f)
    member = _actions(api.get(f"{f}/@actions", as_="member")).get("object_buttons", [])
    assert "delete" not in member and "cut" not in member and "rename" not in member, member
    assert {"cut", "copy", "delete"} <= set(editor), editor  # rename also needs Add on the parent


def test_actions_private_denied_for_anonymous(api, sandbox):
    assert api.get(f"{sandbox}/@actions", as_="anonymous").status in DENIED


def test_actions_categories_filter(api, sandbox):
    r = api.get(f"{sandbox}/@actions", params={"categories:list": "user"})
    assert list(_actions(r)) == ["user"], r.json


def test_actions_site_root_anonymous(api, default_global_state):
    a = _actions(api.get("@actions", as_="anonymous"))
    assert a["site_actions"] == ["sitemap", "accessibility", "contact"], a
    assert "login" in a["user"] and "logout" not in a["user"], a


# ------------------------------------------------------------------ aliases

def test_aliases_after_rename(api, sandbox):
    path = api.doc(sandbox, "old")
    assert api.patch(path, json={"id": "new"}).status == 204
    r = api.get(f"{sandbox}/new/@aliases")
    assert r.status == 200, r
    items = r.json["items"]
    assert r.json["items_total"] == len(items) == 1, items
    assert items[0]["path"] == f"/{sandbox}/old" and items[0]["redirect-to"] == f"/{sandbox}/new", items
    assert items[0]["manual"] is False


def test_rename_redirects_old_url(api, sandbox):
    path = api.doc(sandbox, "old")
    api.patch(path, json={"id": "new"})
    r = api.get(path)
    assert r.status in (301, 302), r
    assert _redirect_target(r) == f"{api.t.base}/{sandbox}/new"


def test_move_redirects_old_url(api, sandbox):
    path = api.doc(sandbox, "d")
    target = api.folder(sandbox, "target")
    r = api.post(f"{target}/@move", json={"source": f"/{sandbox}/d"})
    assert r.status == 200, r
    r = api.get(path)
    assert r.status in (301, 302), r
    assert _redirect_target(r) == f"{api.t.base}/{target}/d"
    items = api.get(f"{target}/d/@aliases").json["items"]
    assert [i["path"] for i in items] == [f"/{sandbox}/d"], items


def test_folder_rename_redirects_children(api, sandbox):
    f = api.folder(sandbox, "f")
    api.doc(f, "child")
    api.patch(f, json={"id": "g"})
    r = api.get(f"{sandbox}/f/child")
    assert r.status in (301, 302), r
    assert _redirect_target(r) == f"{api.t.base}/{sandbox}/g/child"


def test_double_rename_chain_redirects_to_latest(api, sandbox):
    path = api.doc(sandbox, "a")
    api.patch(path, json={"id": "b"})
    api.patch(f"{sandbox}/b", json={"id": "c"})
    for old in ("a", "b"):
        r = api.get(f"{sandbox}/{old}")
        assert r.status in (301, 302) and _redirect_target(r) == f"{api.t.base}/{sandbox}/c", (old, r)


def test_manual_alias_add_list_delete(api, sandbox):
    path = api.doc(sandbox, "d")
    alias = f"/{sandbox}/manual-{uid(4)}"
    r = api.post(f"{path}/@aliases", json={"items": [{"path": alias}]})
    assert r.status in (201, 204), r
    items = api.get(f"{path}/@aliases").json["items"]
    assert [(i["path"], i["manual"]) for i in items] == [(alias, True)], items
    g = api.get(alias.lstrip("/"))
    assert g.status in (301, 302) and _redirect_target(g) == f"{api.t.base}/{path}", g
    r = api.delete(f"{path}/@aliases", json={"items": [{"path": alias}]})
    assert r.status == 204, r
    assert api.get(f"{path}/@aliases").json["items"] == []
    assert api.get(alias.lstrip("/")).status == 404


@pytest.mark.compat("an alias that collides with existing content is refused with 200 and "
                    "{type: error, failed: [...]} instead of a 4xx; Volto's per-item Aliases form only "
                    "recognises the refusal on a 2xx: volto 19.5.0 packages/volto/src/reducers/aliases/aliases.js:67-75 "
                    "(ADD_ALIASES_SUCCESS: error = result.failed) and components/manage/Aliases/Aliases.jsx:136-138 "
                    "(isAliasAlready only when add.loaded, which ADD_ALIASES_FAIL leaves false)")
def test_manual_alias_onto_existing_content_is_refused(api, sandbox):
    path = api.doc(sandbox, "d")
    api.doc(sandbox, "taken")
    r = api.post(f"{path}/@aliases", json={"items": [{"path": f"/{sandbox}/taken"}]})
    assert r.status == 200 and r.json["type"] == "error" and len(r.json["failed"]) == 1, r
    assert api.get(f"{sandbox}/taken").status == 200
    assert api.get(f"{path}/@aliases").json["items"] == []


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_aliases_add_denied(api, sandbox, persona):
    api.publish(sandbox)
    path = api.doc(sandbox, "d")
    api.publish(path)
    r = api.post(f"{path}/@aliases", as_=persona, json={"items": [{"path": f"/{sandbox}/sneaky"}]})
    assert r.status in DENIED, r
    assert api.get(f"{sandbox}/sneaky").status == 404


def test_aliases_get_denied_without_view(api, sandbox):
    path = api.doc(sandbox, "d")
    assert api.get(f"{path}/@aliases", as_="anonymous").status in DENIED


def test_redirect_preserves_view_permission(api, sandbox):
    """The redirect itself may be public, but the target stays protected."""
    path = api.doc(sandbox, "old")
    api.patch(path, json={"id": "new"})
    r = api.get(path, as_="anonymous")
    if r.status in (301, 302):
        follow = api.get(_redirect_target(r)[len(api.t.base):].lstrip("/"), as_="anonymous")
        assert follow.status in DENIED, follow
    else:
        assert r.status in DENIED + (404,), r


def test_deleting_content_removes_its_aliases(api, sandbox):
    path = api.doc(sandbox, "old")
    api.patch(path, json={"id": "new"})
    assert api.delete(f"{sandbox}/new").status == 204
    assert api.get(path).status == 404
