"""@history: listing (shape, order, entry types), version numbering after
edits, changeNote, GET of a specific version, revert (PATCH) incl. its
permission and side effects. A2#5 (revert needs only View, may_revert,
reindex) is covered in test_a2_nick_defects.py."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from restapi_suite.assertions import DENIED
from restapi_suite.services import history_actions, versions

pytestmark = pytest.mark.area("history")

NICK = "nick"


def _doc_with_edits(api, sandbox, n=2, **kw):
    path = api.doc(sandbox, "d", title="v0")
    for i in range(1, n + 1):
        api.patch(path, json={"title": f"v{i}", **kw}, compare=False)
    return path


def _hist(api, path, as_="admin"):
    return api.get(f"{path}/@history", as_=as_)


def test_history_list_shape_versioning_entry(api, sandbox):
    path = api.doc(sandbox, "d", title="v0")
    r = _hist(api, path)
    v = versions(r)
    assert len(v) == 1, r.json
    e = v[0]
    assert {"@id", "action", "actor", "comments", "may_revert", "time", "transition_title", "type",
            "version"} <= set(e), e
    assert (e["action"], e["transition_title"], e["version"], e["comments"]) == ("Edited", "Edited", 0,
                                                                                  "Initial version")
    assert e["@id"] == f"{api.t.base}/{path}/@history/0"
    assert e["actor"]["id"] == api.t.admin.id and e["actor"]["@id"] == f"{api.t.base}/@users/{api.t.admin.id}"
    assert e["may_revert"] is True


def test_history_create_entry(api, sandbox):
    path = api.doc(sandbox, "d")
    h = _hist(api, path).json
    create = h[-1]
    assert (create["type"], create["action"], create["transition_title"]) == ("workflow", "Create", "Create"), h
    assert create["review_state"] == "private" and create["state_title"] == "Private", create


def test_history_versions_count_and_order(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=3)
    h = _hist(api, path)
    assert [e.get("version") for e in h.json] == [3, 2, 1, 0, None], h.json
    assert history_actions(h) == [("versioning", "Edited")] * 4 + [("workflow", "Create")]


def test_history_version_numbers_are_consecutive(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=4)
    assert sorted(e["version"] for e in versions(_hist(api, path))) == [0, 1, 2, 3, 4]


def test_history_is_newest_first(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=2)
    time.sleep(1.1)  # timestamps have second resolution
    api.publish(path)
    h = _hist(api, path).json
    times = [datetime.fromisoformat(e["time"]) for e in h]
    assert times == sorted(times, reverse=True), h
    assert h[0]["type"] == "workflow" and h[0]["action"] == "publish", h[0]


def test_history_workflow_entry_shape(api, sandbox):
    path = api.doc(sandbox, "d")
    api.publish(path)
    e = next(e for e in _hist(api, path).json if e.get("action") == "publish")
    assert {"action", "actor", "comments", "review_state", "state_title", "time", "transition_title",
            "type"} <= set(e), e
    assert (e["type"], e["review_state"], e["state_title"], e["transition_title"]) == (
        "workflow", "published", "Published", "Publish")
    assert "version" not in e and "may_revert" not in e, e


def test_history_change_note_becomes_comment(api, sandbox):
    path = api.doc(sandbox, "d", title="v0")
    api.patch(path, json={"title": "v1", "changeNote": "fixed a typo – æøå"}, compare=False)
    api.patch(path, json={"title": "v2"}, compare=False)
    v = {e["version"]: e for e in versions(_hist(api, path))}
    assert v[1]["comments"] == "fixed a typo – æøå", v[1]
    assert "comments" in v[2] and v[2]["comments"] is None, v[2]


def test_history_actor_is_the_editor(api, sandbox):
    path = api.doc(sandbox, "d", title="v0")
    le = api.personas.users["local_editor"].id
    assert api.patch(path, as_="local_editor", json={"title": "v1"}).status == 204
    latest = versions(_hist(api, path))[0]
    assert latest["version"] == 1 and latest["actor"]["id"] == le, latest


def test_history_workflow_transition_does_not_create_version(api, sandbox):
    path = api.doc(sandbox, "d")
    api.publish(path)
    api.transition(path, "retract")
    assert [e["version"] for e in versions(_hist(api, path))] == [0]


@pytest.mark.plone_bug("passing effective/expires to a transition deserializes them as an edit, which "
                       "fires ObjectModified and creates an unnamed version")
@pytest.mark.known_divergence(NICK, reason="Nick ignores transition dates, so no version", kind="plone-bug")
def test_history_transition_with_dates_creates_version(api, sandbox):
    path = api.doc(sandbox, "d")
    api.publish(path, effective="2030-01-01T00:00:00")
    assert [e["version"] for e in versions(_hist(api, path))] == [1, 0]


@pytest.mark.known_divergence(NICK, reason="Nick versions folders; Plone's Folder type has no versioning",
                              kind="deliberate")
def test_history_folder_is_not_versioned(api, sandbox):
    f = api.folder(sandbox, "f", title="f0")
    api.patch(f, json={"title": "f1"}, compare=False)
    r = _hist(api, f)
    assert versions(r) == [], r.json
    assert history_actions(r) == [("workflow", "Create")]


# GET version ------------------------------------------------------------------

def test_history_get_version_returns_old_content(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=2)
    for n in (0, 1, 2):
        r = api.get(f"{path}/@history/{n}")
        assert r.status == 200 and r.json["title"] == f"v{n}", r
    assert api.get(path).json["title"] == "v2"


def test_history_get_version_has_content_shape(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    j = api.get(f"{path}/@history/0").json
    assert j["@type"] == api.type("document") and j["id"] == "d", j
    assert "@components" in j


def test_history_content_reports_current_version(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    assert api.get(path).json.get("version") == "current"


@pytest.mark.plone_bug("a version that does not exist is a 500 (CMFEditions ArchivistRetrieveError), not 404")
def test_history_get_unknown_version_is_500(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    assert api.get(f"{path}/@history/99").status == 500


@pytest.mark.plone_bug("a non-integer version is a 500 (int() ValueError), not 400")
def test_history_get_non_integer_version_is_500(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    assert api.get(f"{path}/@history/abc").status == 500


@pytest.mark.parametrize("persona,allowed", [("anonymous", False), ("member", False), ("local_editor", True),
                                             ("site_admin", True)])
def test_history_list_permission_on_private(api, sandbox, persona, allowed):
    path = _doc_with_edits(api, sandbox, n=1)
    r = _hist(api, path, as_=persona)
    assert (r.status == 200) is allowed and (allowed or r.status in DENIED), r


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_history_list_on_published_needs_access_previous_versions(api, sandbox, persona):
    """Published content is viewable by everybody, but 'CMFEditions: Access
    previous versions' is only granted to editing roles."""
    api.publish(sandbox)
    path = _doc_with_edits(api, sandbox, n=1)
    api.publish(path)
    assert api.get(path, as_=persona).status == 200
    assert _hist(api, path, as_=persona).status in DENIED


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_history_get_version_on_published_is_denied(api, sandbox, persona):
    api.publish(sandbox)
    path = _doc_with_edits(api, sandbox, n=1)
    api.publish(path)
    assert api.get(f"{path}/@history/0", as_=persona).status in DENIED


# revert -----------------------------------------------------------------------

def test_history_revert_restores_and_adds_version(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=2)
    r = api.patch(f"{path}/@history", json={"version": 0})
    assert r.status == 200 and r.json == {"message": "v0 has been reverted to revision 0."}, r
    assert api.get(path).json["title"] == "v0"
    latest = versions(_hist(api, path))[0]
    assert latest["version"] == 3, latest


def test_history_revert_comment(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=2)
    api.patch(f"{path}/@history", json={"version": 1})
    assert versions(_hist(api, path))[0]["comments"] == "Reverted to revision 1"


def test_history_revert_keeps_old_versions(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=2)
    api.patch(f"{path}/@history", json={"version": 0})
    assert api.get(f"{path}/@history/2").json["title"] == "v2"
    assert api.get(f"{path}/@history/3").json["title"] == "v0"


def test_history_revert_does_not_change_workflow_state(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    api.publish(path)
    api.patch(f"{path}/@history", json={"version": 0})
    assert api.state(path) == "published"


def test_history_revert_by_local_editor(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    r = api.patch(f"{path}/@history", as_="local_editor", json={"version": 0})
    assert r.status == 200, r
    assert api.get(path).json["title"] == "v0"


@pytest.mark.known_divergence(NICK, reason="PATCH @history (revert) only requires View", ref="A2#5")
def test_history_revert_denied_for_local_reader(api, sandbox):
    """Reader may view the item, but revert needs edit rights
    (CMFEditions: Revert to previous versions)."""
    path = _doc_with_edits(api, sandbox, n=1)
    u = api.create_user()
    api.share(path, u, ["Reader"])
    assert api.get(path, as_=u).status == 200
    assert api.patch(f"{path}/@history", as_=u, json={"version": 0}).status in DENIED
    assert api.get(path).json["title"] == "v1"


@pytest.mark.plone_bug("revert does not follow edit permission: in pending an Editor cannot PATCH the "
                       "item but can still revert it ('Revert to previous versions' is not workflow-managed)")
def test_history_editor_reverts_pending_item_it_cannot_edit(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    api.transition(path, "submit")
    assert api.patch(path, as_="local_editor", json={"title": "x"}).status in DENIED
    assert api.patch(f"{path}/@history", as_="local_editor", json={"version": 0}).status == 200
    assert api.get(path).json["title"] == "v0"


@pytest.mark.plone_bug("PATCH @history without 'version' is an unhandled KeyError, answered as 503")
@pytest.mark.known_divergence(NICK, reason="Nick answers 500; neither validates the body", kind="plone-bug")
def test_history_revert_without_version_is_error(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    r = api.patch(f"{path}/@history", json={})
    assert r.status == 503, r
    assert api.get(path).json["title"] == "v1"


@pytest.mark.plone_bug("reverting to a version that does not exist is a 500, not 404/400")
def test_history_revert_unknown_version_is_500(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    assert api.patch(f"{path}/@history", json={"version": 99}).status == 500
    assert api.get(path).json["title"] == "v1"


def test_history_edit_after_revert_continues_numbering(api, sandbox):
    path = _doc_with_edits(api, sandbox, n=1)
    api.patch(f"{path}/@history", json={"version": 0})
    api.patch(path, json={"title": "after"}, compare=False)
    assert [e["version"] for e in versions(_hist(api, path))] == [3, 2, 1, 0]
