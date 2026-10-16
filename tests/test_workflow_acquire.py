"""Workflow permission models (A2 2a): with Plone semantics a workflow state
whose permission is not acquired replaces the global role grants, so a state
can take View away from a role that has it globally. Nick-only: Plone can't
be given custom roles, workflows and types over HTTP, and the stock
simple_publication_workflow lists every role with global View in every state.

Needs a Nick deployed with the fixture profile
targets/nick-profiles/restapi_suite_acquire:

    NICK_EXTRA_PROFILES=nick-profiles/restapi_suite_acquire targets/nick-up.sh

It adds the global role Auditor (permission View), three workflows with the
states internal (View and Modify for Editor and Administrator only) and open
(the same, plus "acquire": ["View"]), one per permission model (acquire,
additive, unset = Nick's default), and the types AcquirePage, AdditivePage and
DefaultPage using them. Without the fixture (or against Plone, or in
differential mode) the module is skipped."""

from __future__ import annotations

import pytest

from restapi_suite.assertions import DENIED

pytestmark = pytest.mark.area("workflow-acquire")

FIXTURE_TYPES = ("AcquirePage", "AdditivePage", "DefaultPage")


@pytest.fixture(autouse=True)
def _requires_fixture(api, target, reference):
    if target.kind != "nick":
        pytest.skip("Nick-only: Plone can't be configured with custom roles and workflows over HTTP")
    if reference:
        pytest.skip("the fixture types only exist on the Nick target")
    r = api.get("@types", compare=False)
    have = {t["@id"].rsplit("/", 1)[-1] for t in r.json} if r.status == 200 else set()
    if not set(FIXTURE_TYPES) <= have:
        pytest.skip("target lacks the restapi_suite_acquire fixture profile (NICK_EXTRA_PROFILES)")


@pytest.fixture
def auditor(api):
    """A member with the global Auditor role, which has View globally."""
    return api.create_user(roles=["member", "Auditor"])


def create(api, parent, type_, id):
    r = api.post(parent, json={"@type": type_, "id": id, "title": id})
    assert r.status == 201, r
    return f"{parent}/{id}"


def search_ids(api, path, persona):
    """Ids of the fixture items found below `path`, which must be viewable by
    `persona` (publish the sandbox). Filtered here rather than with a list of
    portal_type values, which is a search-conformance topic of its own."""
    r = api.get(f"{path}/@search", as_=persona)
    assert r.status == 200, r
    return sorted(i["@id"].rsplit("/", 1)[-1] for i in r.json["items"] if i.get("@type") in FIXTURE_TYPES)


def test_acquire_state_removes_global_view(api, sandbox, auditor):
    path = create(api, sandbox, "AcquirePage", "a")
    assert api.state(path) == "internal"
    assert api.get(path, as_=auditor).status in DENIED


def test_acquire_state_with_acquire_flag_keeps_global_view(api, sandbox, auditor):
    path = create(api, sandbox, "AcquirePage", "a")
    api.transition(path, "open")
    assert api.get(path, as_=auditor).status == 200
    api.transition(path, "close")
    assert api.get(path, as_=auditor).status in DENIED


def test_acquire_state_roles_still_apply(api, sandbox):
    """Roles listed in the state keep their permissions: the sandbox's local Editor."""
    path = create(api, sandbox, "AcquirePage", "a")
    assert api.get(path, as_="local_editor").status == 200
    assert api.patch(path, as_="local_editor", json={"title": "edited"}).status == 204


def test_acquire_search_follows_the_state(api, sandbox, auditor):
    api.publish(sandbox)
    create(api, sandbox, "AcquirePage", "internal-item")
    api.transition(create(api, sandbox, "AcquirePage", "open-item"), "open")
    assert search_ids(api, sandbox, auditor) == ["open-item"]


def test_additive_model_keeps_global_view(api, sandbox, auditor):
    """The pre-2.9 Nick behaviour, kept when a workflow sets "additive"."""
    api.publish(sandbox)
    path = create(api, sandbox, "AdditivePage", "a")
    assert api.state(path) == "internal"
    assert api.get(path, as_=auditor).status == 200
    assert search_ids(api, sandbox, auditor) == ["a"]


def test_default_model_is_acquire(api, sandbox, auditor):
    """A workflow that doesn't choose a model behaves like Plone's DCWorkflow."""
    api.publish(sandbox)
    path = create(api, sandbox, "DefaultPage", "a")
    assert api.get(path, as_=auditor).status in DENIED
    assert search_ids(api, sandbox, auditor) == []
