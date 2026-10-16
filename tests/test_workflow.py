"""@workflow: GET (state, transitions per persona, history) and POST
transitions (comment, effective/expires, include_children, invalid
transitions, persona guards). Plone 6.2 simple_publication_workflow is the
reference; A2#4/#6 defects already covered in test_a2_nick_defects.py are
not repeated here."""

from __future__ import annotations

import pytest

from restapi_suite.assertions import DENIED, same_outcome
from restapi_suite.services import transition_ids

pytestmark = pytest.mark.area("workflow")

NICK = "nick"
HISTORY_EMPTY = dict(reason="@workflow GET returns history: [] (models/workflow/workflow.ts)", ref="A2 4d")


def _local(api, sandbox, role):
    u = api.create_user()
    api.share(sandbox, u, [role])
    return u


# ------------------------------------------------------------------ GET

def test_workflow_get_shape(api, sandbox):
    path = api.doc(sandbox, "d")
    r = api.get(f"{path}/@workflow")
    assert r.status == 200, r
    j = r.json
    assert j["@id"] == f"{api.t.base}/{path}/@workflow"
    assert j["state"] == {"id": "private", "title": "Private"}
    assert isinstance(j["history"], list)
    for t in j["transitions"]:
        assert set(t) == {"@id", "title"}, t
        assert t["@id"].startswith(f"{api.t.base}/{path}/@workflow/")
    assert {t["title"] for t in j["transitions"]} == {"Publish", "Submit for publication"}


@pytest.mark.known_divergence(NICK, **HISTORY_EMPTY)
def test_workflow_get_initial_history_entry(api, sandbox):
    path = api.doc(sandbox, "d")
    h = api.get(f"{path}/@workflow").json["history"]
    assert len(h) == 1, h
    e = h[0]
    assert e["action"] is None and e["review_state"] == "private" and e["title"] == "Private", e
    assert e["actor"] == api.t.admin.id and e["comments"] == "", e
    assert set(e) == {"action", "actor", "comments", "review_state", "time", "title"}, e


@pytest.mark.known_divergence(NICK, **HISTORY_EMPTY)
def test_workflow_history_grows_in_order(api, sandbox):
    path = api.doc(sandbox, "d")
    for t in ("submit", "reject", "publish", "retract"):
        api.transition(path, t)
    h = api.get(f"{path}/@workflow").json["history"]
    assert [e["action"] for e in h] == [None, "submit", "reject", "publish", "retract"], h
    assert [e["review_state"] for e in h] == ["private", "pending", "private", "published", "private"]
    assert [e["time"] for e in h] == sorted(e["time"] for e in h)


STATE_TRANSITIONS = {
    # persona-like role -> (private, pending, published) transitions
    "anonymous": (None, None, []),
    "member": (None, None, []),
    "Editor": (["submit"], ["retract"], ["retract"]),
    "Reviewer": (None, ["publish", "reject"], ["reject"]),
    "Contributor": ([], [], []),
    "Reader": ([], [], []),
    "site_admin": (["publish", "submit"], ["publish", "reject", "retract"], ["reject", "retract"]),
    "manager": (["publish", "submit"], ["publish", "reject", "retract"], ["reject", "retract"]),
}


@pytest.mark.parametrize("state", ["private", "pending", "published"])
@pytest.mark.parametrize("who", list(STATE_TRANSITIONS))
def test_workflow_available_transitions(api, sandbox, who, state):
    """None = the persona may not even view the item (GET @workflow denied).
    Local roles are granted on the sandbox and inherited by the document."""
    path = api.doc(sandbox, "d")
    if state == "pending":
        api.transition(path, "submit")
    elif state == "published":
        api.publish(path)
    persona = _local(api, sandbox, who) if who[0].isupper() else who
    r = api.get(f"{path}/@workflow", as_=persona)
    expected = STATE_TRANSITIONS[who][("private", "pending", "published").index(state)]
    if expected is None:
        assert r.status in DENIED, r
    else:
        assert transition_ids(r) == expected


def test_workflow_transitions_follow_state(api, sandbox):
    path = api.doc(sandbox, "d")
    assert transition_ids(api.get(f"{path}/@workflow")) == ["publish", "submit"]
    api.transition(path, "submit")
    assert transition_ids(api.get(f"{path}/@workflow")) == ["publish", "reject", "retract"]
    assert api.get(f"{path}/@workflow").json["state"]["id"] == "pending"


def test_workflow_get_on_folder(api, sandbox):
    f = api.folder(sandbox, "f")
    r = api.get(f"{f}/@workflow")
    assert r.status == 200 and r.json["state"]["id"] == "private", r


# ------------------------------------------------------------------ POST

def test_workflow_post_response_shape(api, sandbox):
    path = api.doc(sandbox, "d")
    r = api.post(f"{path}/@workflow/publish")
    assert r.status == 200, r
    j = r.json
    assert set(j) == {"action", "actor", "comments", "review_state", "time", "title"}, j
    assert (j["action"], j["review_state"], j["title"], j["actor"]) == ("publish", "published", "Published",
                                                                         api.t.admin.id)


@pytest.mark.known_divergence(NICK, reason="transition comments are dropped (comments: '')", ref="A2#6")
def test_workflow_comment_unicode_in_history(api, sandbox):
    path = api.doc(sandbox, "d")
    note = "Godkjent av redaksjonen – ærlig talt ✓"
    r = api.post(f"{path}/@workflow/submit", json={"comment": note})
    assert r.status == 200 and r.json["comments"] == note, r
    h = api.get(f"{path}/@workflow").json["history"]
    assert h and h[-1]["comments"] == note, h


def test_workflow_publish_with_effective_and_expires(api, sandbox):
    path = api.doc(sandbox, "d")
    r = api.post(f"{path}/@workflow/publish",
                 json={"effective": "2030-01-02T03:04:00", "expires": "2031-05-06T07:08:00"})
    assert r.status == 200, r
    j = api.get(path).json
    assert j["review_state"] == "published"
    assert j["effective"].startswith("2030-01-02T03:04"), j["effective"]
    assert j["expires"].startswith("2031-05-06T07:08"), j["expires"]


def test_workflow_invalid_effective_is_400_and_state_unchanged(api, sandbox):
    path = api.doc(sandbox, "d")
    r = api.post(f"{path}/@workflow/publish", json={"effective": "not-a-date"})
    assert r.status == 400, r
    assert api.state(path) == "private"


def test_workflow_first_publish_sets_effective(api, sandbox):
    path = api.doc(sandbox, "d")
    assert api.get(path).json.get("effective") in (None, "None")
    api.publish(path)
    eff = api.get(path).json.get("effective")
    assert eff and eff != "None", eff


def test_workflow_republish_keeps_first_effective(api, sandbox):
    """Plone sets effective only when it is empty (transition.py)."""
    path = api.doc(sandbox, "d")
    api.publish(path, effective="2029-03-03T10:00:00")
    api.transition(path, "retract")
    api.publish(path)
    assert api.get(path).json["effective"].startswith("2029-03-03T10:00")


@pytest.mark.plone_bug("any transition (even submit) stamps effective with 'now' when it is empty, "
                       "so a never-published item gets a publication date")
@pytest.mark.known_divergence(NICK, reason="transitions do not set effective", kind="plone-bug")
def test_workflow_submit_also_sets_effective(api, sandbox):
    path = api.doc(sandbox, "d")
    api.transition(path, "submit")
    eff = api.get(path).json.get("effective")
    assert eff and eff != "None", eff


def test_workflow_publish_makes_anonymous_view_possible_and_retract_revokes(api, sandbox):
    path = api.doc(sandbox, "d")
    api.publish(sandbox)
    assert api.get(path, as_="anonymous").status in DENIED
    api.publish(path)
    assert api.get(path, as_="anonymous").status == 200
    api.transition(path, "retract")
    assert api.get(path, as_="anonymous").status in DENIED


# include_children ----------------------------------------------------------

def _tree(api, sandbox):
    f = api.folder(sandbox, "f")
    api.doc(f, "d1")
    api.folder(f, "sub")
    api.doc(f"{f}/sub", "d2")
    return f


def test_workflow_include_children_publishes_tree(api, sandbox):
    f = _tree(api, sandbox)
    r = api.post(f"{f}/@workflow/publish", json={"include_children": True, "comment": "tree"})
    assert r.status == 200 and r.json["review_state"] == "published", r
    for p in (f, f"{f}/d1", f"{f}/sub", f"{f}/sub/d2"):
        assert api.state(p) == "published", p


def test_workflow_without_include_children_leaves_children(api, sandbox):
    f = _tree(api, sandbox)
    api.publish(f)
    assert [api.state(p) for p in (f"{f}/d1", f"{f}/sub", f"{f}/sub/d2")] == ["private"] * 3


def test_workflow_include_children_tolerates_child_already_in_target_state(api, sandbox):
    f = _tree(api, sandbox)
    api.publish(f"{f}/d1")
    r = api.post(f"{f}/@workflow/publish", json={"include_children": True})
    assert r.status == 200, r
    assert api.state(f"{f}/sub/d2") == "published"


@pytest.mark.plone_bug("include_children is not atomic: the WorkflowException of a child becomes a 400 "
                       "response, but the transaction is committed, so the parent stays transitioned")
@pytest.mark.known_divergence(NICK, reason="no per-child validation: the published child is submitted, 200",
                              ref="A2#4")
def test_workflow_include_children_invalid_child_is_400_but_not_atomic(api, sandbox):
    """submit is invalid for a published child. Plone answers 400 but keeps
    the parent's transition and stops at the failing child."""
    f = _tree(api, sandbox)
    api.publish(f"{f}/d1")
    r = api.post(f"{f}/@workflow/submit", json={"include_children": True})
    assert r.status == 400, r
    assert api.state(f) == "pending"
    assert api.state(f"{f}/d1") == "published"
    assert api.state(f"{f}/sub") == "private"


def test_workflow_include_children_applies_dates(api, sandbox):
    f = _tree(api, sandbox)
    api.post(f"{f}/@workflow/publish", json={"include_children": True, "effective": "2030-06-01T12:00:00"})
    assert api.get(f"{f}/sub/d2").json["effective"].startswith("2030-06-01T12:00")


@pytest.mark.known_divergence(NICK, reason="transition comments are dropped (comments: '')", ref="A2#6")
def test_workflow_include_children_comment_on_children(api, sandbox):
    f = _tree(api, sandbox)
    api.post(f"{f}/@workflow/publish", json={"include_children": True, "comment": "batch-publish"})
    h = api.get(f"{f}/sub/d2/@history").json
    assert any(e.get("action") == "publish" and e.get("comments") == "batch-publish" for e in h), h


# invalid / not allowed ----------------------------------------------------

@pytest.mark.known_divergence(NICK, reason="transition not checked against the current state's transitions",
                              ref="A2#4")
def test_workflow_submit_on_published_is_400_with_message(api, sandbox):
    """The only cross-state invalid transition of simple_publication_workflow
    (every other one ends in the current state, see the no-op test)."""
    path = api.doc(sandbox, "d")
    api.publish(path)
    r = api.post(f"{path}/@workflow/submit")
    assert r.status == 400, r
    assert r.json["error"]["type"] == "WorkflowException", r
    assert api.state(path) == "published"


@pytest.mark.parametrize("state,transition", [("private", "retract"), ("private", "reject"),
                                              ("pending", "submit"), ("published", "publish")])
def test_workflow_same_state_transition_is_noop(api, sandbox, state, transition):
    """Plone 6.2 tolerates a transition whose destination is the current state
    (transition.py is_same_state): 200, state unchanged, no history entry."""
    path = api.doc(sandbox, "d")
    if state == "pending":
        api.transition(path, "submit")
    elif state == "published":
        api.publish(path)
    before = [e for e in api.get(f"{path}/@history", compare=False).json if e.get("type") == "workflow"]
    r = api.post(f"{path}/@workflow/{transition}")
    assert r.status == 200, r
    assert api.state(path) == state
    after = [e for e in api.get(f"{path}/@history", compare=False).json if e.get("type") == "workflow"]
    assert len(after) == len(before), (before, after)


def test_workflow_post_without_transition_is_400(api, sandbox):
    path = api.doc(sandbox, "d")
    r = api.post(f"{path}/@workflow")
    assert r.status == 400, r


@pytest.mark.plone_bug("a guard failure is 400 WorkflowException with an untranslated "
                       "\"No workflow provides the '${action_id}' action.\" instead of 401/403")
@pytest.mark.known_divergence(NICK, reason="Nick answers 401 (arguably right)", kind="plone-bug")
def test_workflow_editor_cannot_publish(api, sandbox):
    path = api.doc(sandbox, "d")
    r = api.post(f"{path}/@workflow/publish", as_="local_editor")
    assert r.status == 400, r
    assert api.state(path) == "private"


@pytest.mark.parametrize("persona", ["anonymous", "member"])
def test_workflow_transition_without_view_is_denied(api, sandbox, persona):
    path = api.doc(sandbox, "d")
    r = api.post(f"{path}/@workflow/publish", as_=persona)
    assert r.status in DENIED, r
    assert api.state(path) == "private"


@pytest.mark.parametrize("persona", ["anonymous", "member"])
@pytest.mark.plone_bug("guard failure is a 400 WorkflowException, even for anonymous")
@pytest.mark.known_divergence(NICK, reason="guard failure gives 401 instead of 400", kind="plone-bug")
def test_workflow_publish_published_by_non_editor(api, sandbox, persona):
    """Published items are viewable, so the service runs and the guard
    decides. retract would be a same-state no-op here, so use reject."""
    expected = 400
    api.publish(sandbox)
    path = api.doc(sandbox, "d")
    api.publish(path)
    r = api.post(f"{path}/@workflow/reject", as_=persona)
    assert same_outcome(expected, r.status), r
    assert api.state(path) == "published"


def test_workflow_reviewer_publishes_pending(api, sandbox):
    path = api.doc(sandbox, "d")
    u = _local(api, sandbox, "Reviewer")
    api.transition(path, "submit")
    r = api.post(f"{path}/@workflow/publish", as_=u)
    assert r.status == 200 and r.json["actor"] == u.id, r
    assert api.state(path) == "published"


def test_workflow_owner_submits_and_retracts(api, sandbox):
    u = _local(api, sandbox, "Contributor")
    r = api.post(sandbox, as_=u, json={"@type": "{type:document}", "id": "mine", "title": "mine"})
    assert r.status == 201, r
    assert transition_ids(api.get(f"{sandbox}/mine/@workflow", as_=u)) == ["submit"]
    assert api.post(f"{sandbox}/mine/@workflow/submit", as_=u).status == 200
    assert transition_ids(api.get(f"{sandbox}/mine/@workflow", as_=u)) == ["retract"]
    assert api.post(f"{sandbox}/mine/@workflow/retract", as_=u).status == 200
    assert api.state(f"{sandbox}/mine") == "private"


def test_workflow_editor_cannot_edit_pending(api, sandbox):
    """pending removes Modify from Editor; transitions are the way back."""
    path = api.doc(sandbox, "d")
    api.transition(path, "submit")
    assert api.patch(path, as_="local_editor", json={"title": "x"}).status in DENIED
    assert api.post(f"{path}/@workflow/retract", as_="local_editor").status == 200
    assert api.patch(path, as_="local_editor", json={"title": "x"}).status == 204


@pytest.mark.known_divergence(NICK, reason="unknown transition id -> TypeError -> 500", ref="A2#4")
def test_workflow_unknown_transition_on_published_is_400(api, sandbox):
    path = api.doc(sandbox, "d")
    api.publish(path)
    r = api.post(f"{path}/@workflow/{'x' * 200}")
    assert r.status in (400, 404), r
    assert api.state(path) == "published"
