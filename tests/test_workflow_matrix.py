"""Workflow conformance matrix: for every standard Plone 6.2 workflow, every
state, every transition and every role, is the transition offered by
GET @workflow?

Opt-in (marker ``workflow_matrix``). The reference answers were captured
from Plone 6.2 over HTTP and are stored in
``tests/fixtures/workflow_matrix_plone62.json``; the test computes the same
matrix on a target and compares it cell by cell.

    WORKFLOW_MATRIX_URL     target, e.g. http://127.0.0.1:18640/plone
    WORKFLOW_MATRIX_KIND    plone or nick
    WORKFLOW_MATRIX_ADMIN   user:password (default admin:admin)

On Plone the test creates one Dexterity type per workflow
(``wfmatrix_<short name>``, @controlpanels/dexterity-types) and assigns the
workflow with the types control panel form (@@content-controlpanel; there is
no REST endpoint for workflow chains). Afterwards it resets the chain and
deletes the types, users and content again. On Nick the types come from the
fixture profile ``targets/nick-profiles/restapi_suite_workflows``, which
loads the same definition.xml files with Nick's DCWorkflow loader:

    NICK_EXTRA_PROFILES="nick-profiles/restapi_suite_workflows" targets/nick-up.sh

Roles: Anonymous, Member (a user without roles; Authenticated on Nick),
Owner (the creator of the item, without other local roles), the local roles
Reader, Contributor, Editor and Reviewer on the folder, and the global roles
Site Administrator and Manager (both Administrator on Nick). A role that
can't view the item gets no transitions (GET @workflow needs View on both).

Capture (rewrites the fixture) and compare by hand:

    python tests/test_workflow_matrix.py capture http://127.0.0.1:18640/plone plone
    python tests/test_workflow_matrix.py compare http://127.0.0.1:18641 nick

comment_review_workflow and comment_one_state_workflow are not installed on
the reference site (no plone.app.discussion), so they are not part of the
captured matrix.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.area("workflow-matrix"), pytest.mark.workflow_matrix]

FIXTURE = Path(__file__).parent / "fixtures" / "workflow_matrix_plone62.json"
PASSWORD = "Matrix-secret-1"

# Workflow -> all transitions and, per state, the transitions leading there
# from the initial state (taken from the definition.xml files of
# Products.CMFPlone 6.2).
WORKFLOWS = {
    "simple_publication_workflow": {
        "transitions": ["publish", "reject", "retract", "submit"],
        "paths": {"private": [], "published": ["publish"], "pending": ["submit"]},
    },
    "one_state_workflow": {"transitions": [], "paths": {"published": []}},
    "intranet_workflow": {
        "transitions": ["hide", "publish_externally", "publish_internally", "reject", "retract",
                        "show_internally", "submit"],
        "paths": {"internal": [], "private": ["hide"], "internally_published": ["publish_internally"],
                  "pending": ["submit"], "external": ["publish_internally", "publish_externally"]},
    },
    "intranet_folder_workflow": {
        "transitions": ["hide", "show_internally"],
        "paths": {"internal": [], "private": ["hide"]},
    },
    "folder_workflow": {
        "transitions": ["hide", "publish", "retract", "show"],
        "paths": {"visible": [], "private": ["hide"], "published": ["publish"]},
    },
    "plone_workflow": {
        "transitions": ["hide", "publish", "reject", "retract", "show", "submit"],
        "paths": {"visible": [], "private": ["hide"], "published": ["publish"], "pending": ["submit"]},
    },
}

ROLES = ["Anonymous", "Member", "Owner", "Reader", "Contributor", "Editor", "Reviewer",
         "Site Administrator", "Manager"]
LOCAL_ROLES = ["Reader", "Contributor", "Editor", "Reviewer"]
# Global roles per role. Every Plone user is a Member, like a registered member
GLOBAL_ROLES = {"plone": {"Site Administrator": ["Member", "Site Administrator"], "Manager": ["Member", "Manager"]},
                "nick": {"Site Administrator": ["Administrator"], "Manager": ["Administrator"]}}
DEFAULT_ROLES = {"plone": ["Member"], "nick": []}


# Short type ids: the titles of the types show up in type vocabularies,
# which other tests filter (for example on "fold")
SHORT = {"simple_publication_workflow": "simple", "one_state_workflow": "onestate",
         "intranet_workflow": "intranet", "intranet_folder_workflow": "intranetcontainer",
         "folder_workflow": "container", "plone_workflow": "community"}


def type_id(workflow: str) -> str:
    return f"wfmatrix_{SHORT[workflow]}"


class Site:
    """Minimal JSON client (JWT per user, no cookies)."""

    def __init__(self, base: str, kind: str, admin: str = "admin:admin"):
        self.base = base.rstrip("/")
        self.kind = kind
        self.admin = tuple(admin.split(":", 1))
        jar = CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))
        self.http = httpx.Client(timeout=120, cookies=jar, follow_redirects=False)
        self.tokens: dict[str, str] = {}

    def url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return self.base + ("/" + path.lstrip("/") if path.strip("/") else "")

    def headers(self, as_: str | None) -> dict:
        h = {"Accept": "application/json"}
        if as_ is not None:
            if as_ not in self.tokens:
                password = self.admin[1] if as_ == self.admin[0] else PASSWORD
                r = self.http.post(self.url("@login"), json={"login": as_, "password": password},
                                   headers={"Accept": "application/json"})
                assert r.status_code == 200, f"login {as_}: {r.status_code} {r.text[:300]}"
                self.tokens[as_] = r.json()["token"]
            h["Authorization"] = f"Bearer {self.tokens[as_]}"
        return h

    def req(self, method: str, path: str, as_: str | None = "admin", ok=(200, 201, 204), **kw):
        if as_ == "admin":
            as_ = self.admin[0]
        r = self.http.request(method, self.url(path), headers={**self.headers(as_), **kw.pop("headers", {})}, **kw)
        if ok and r.status_code not in ok:
            raise AssertionError(f"{method} {self.url(path)} as {as_} -> {r.status_code} {r.text[:500]}")
        return r


# -- Plone type setup -------------------------------------------------------


def plone_assign_workflow(site: Site, type_: str, workflow: str) -> None:
    """Set the chain of a type with the types control panel form."""
    auth = (site.admin[0], site.admin[1])
    page = site.http.get(site.url(f"@@content-controlpanel?type_id={type_}"), auth=auth)
    token = re.search(r'name="_authenticator" type="hidden" value="([^"]+)"', page.text).group(1)
    r = site.http.post(site.url("@@content-controlpanel"), auth=auth, data={
        "_authenticator": token, "type_id": type_, "old_type_id": type_, "new_workflow": workflow,
        "form.submitted:boolean": "True", "form.workflow.submitted:boolean": "True",
        "form.button.Save": "Save", "addable:boolean": "True", "searchable:boolean": "True"})
    assert r.status_code in (200, 302), f"assign {workflow} to {type_}: {r.status_code}"
    page = site.http.get(site.url(f"@@content-controlpanel?type_id={type_}"), auth=auth)
    assert f'value="{workflow}"' in page.text or workflow == "(Default)"


def setup_types(site: Site) -> None:
    if site.kind != "plone":
        have = {t["@id"].rsplit("/", 1)[-1] for t in site.req("GET", "@types").json()}
        missing = [type_id(w) for w in WORKFLOWS if type_id(w) not in have]
        if missing:
            pytest.skip(f"target lacks the restapi_suite_workflows fixture profile: {missing}")
        return
    have = {t["@id"].rsplit("/", 1)[-1] for t in site.req("GET", "@controlpanels/dexterity-types").json()["items"]}
    for workflow in WORKFLOWS:
        if type_id(workflow) not in have:
            r = site.req("POST", "@controlpanels/dexterity-types", json={"title": type_id(workflow), "description": ""})
            assert r.json()["@id"].endswith("/" + type_id(workflow)), r.text
        plone_assign_workflow(site, type_id(workflow), workflow)


def teardown_types(site: Site) -> None:
    if site.kind != "plone":
        return
    for workflow in WORKFLOWS:
        plone_assign_workflow(site, type_id(workflow), "(Default)")
        site.req("DELETE", f"@controlpanels/dexterity-types/{type_id(workflow)}", ok=(204, 404))


# -- the matrix -------------------------------------------------------------


def available(site: Site, path: str, as_: str | None) -> tuple[int, list[str]]:
    r = site.req("GET", f"{path}/@workflow", as_=as_, ok=None)
    if r.status_code != 200:
        return r.status_code, []
    return 200, sorted(t["@id"].rsplit("/", 1)[-1] for t in r.json().get("transitions", []))


def compute_matrix(site: Site) -> dict:
    """Create the users, a folder and one item per workflow state, and ask
    @workflow as every role. Everything created is removed again."""
    suffix = uuid.uuid4().hex[:6]
    users = {role: f"wfm{suffix}_{role.lower().replace(' ', '')}" for role in ROLES if role != "Anonymous"}
    folder = f"wfmatrix-{suffix}"
    created_users: list[str] = []
    setup_types(site)
    try:
        for role, user in users.items():
            roles = GLOBAL_ROLES[site.kind].get(role, DEFAULT_ROLES[site.kind])
            site.req("POST", "@users", json={"username": user, "email": f"{user}@example.com",
                                             "password": PASSWORD, "roles": roles})
            created_users.append(user)
        folder_type = "Folder"
        site.req("POST", "", json={"@type": folder_type, "id": folder, "title": folder})
        site.req("POST", f"{folder}/@workflow/publish", ok=(200, 400))
        entries = [{"id": users[r], "type": "user", "roles": {r: True}} for r in LOCAL_ROLES]
        # The owner needs Add to create the items; the role is taken away afterwards
        entries.append({"id": users["Owner"], "type": "user", "roles": {"Contributor": True}})
        site.req("POST", f"{folder}/@sharing", json={"entries": entries})

        items: dict[tuple[str, str], str] = {}
        for workflow, info in WORKFLOWS.items():
            for state, steps in info["paths"].items():
                id_ = f"{workflow[:12]}-{state}".replace("_", "-")
                site.req("POST", folder, as_=users["Owner"],
                         json={"@type": type_id(workflow), "id": id_, "title": id_})
                path = f"{folder}/{id_}"
                for step in steps:
                    site.req("POST", f"{path}/@workflow/{step}")
                got = site.req("GET", f"{path}/@workflow").json()["state"]["id"]
                assert got == state, f"{workflow}: {steps} lead to {got}, not {state}"
                items[(workflow, state)] = path
        site.req("POST", f"{folder}/@sharing",
                 json={"entries": [{"id": users["Owner"], "type": "user", "roles": {"Contributor": False}}]})

        matrix: dict = {}
        for (workflow, state), path in items.items():
            cells = matrix.setdefault(workflow, {"transitions": WORKFLOWS[workflow]["transitions"],
                                                 "states": {}})["states"].setdefault(state, {})
            for role in ROLES:
                status, transitions = available(site, path, None if role == "Anonymous" else users[role])
                cells[role] = {"status": status, "available": transitions}
        return matrix
    finally:
        site.req("DELETE", folder, ok=None)
        for user in created_users:
            site.req("DELETE", f"@users/{user}", ok=None)
        teardown_types(site)


def compare(reference: dict, actual: dict) -> tuple[int, list[str]]:
    """Compare two matrices cell by cell (workflow, state, transition, role).
    Returns the number of cells and the differences."""
    cells, diffs = 0, []
    for workflow, info in reference.items():
        for state, roles in info["states"].items():
            for transition in info["transitions"] or [None]:
                for role in ROLES:
                    cells += 1
                    want = transition is not None and transition in roles[role]["available"]
                    got_cell = actual.get(workflow, {}).get("states", {}).get(state, {}).get(role)
                    if got_cell is None:
                        diffs.append(f"{workflow}/{state}/{transition}/{role}: missing")
                        continue
                    got = transition is not None and transition in got_cell["available"]
                    if transition is None:
                        got = bool(got_cell["available"])
                    if want != got:
                        diffs.append(f"{workflow}/{state}/{transition}/{role}: plone {want}, target {got}")
    return cells, diffs


# -- DCWorkflow semantics (for workflows the reference site doesn't have) -----


def _roles_of(role: str) -> set[str]:
    """Roles of the user of a matrix role on the item, like Plone."""
    if role == "Anonymous":
        return {"Anonymous"}
    return {"Anonymous", "Authenticated", "Member"} | ({role} if role != "Member" else set())


def generate_matrix(definitions: dict[str, str], rolemap: dict[str, set[str]],
                    parent_workflow: str | None = None, parent_state: str | None = None) -> dict:
    """Compute the matrix from DCWorkflow semantics: definitions maps workflow
    ids to definition.xml text, rolemap the site's permission -> roles. The
    items are in a folder in `parent_state` of `parent_workflow` (acquired
    permissions come from there). A role has a permission when one of its
    roles is granted it (Anonymous means everybody); Manager gets every
    permission of the rolemap (acquired from the Zope root)."""
    import xml.dom.minidom as minidom

    def parse(xml: str):
        root = minidom.parseString(xml).documentElement
        managed = [p.firstChild.data.strip() for p in root.getElementsByTagName("permission")
                   if p.parentNode is root]
        states = {}
        for s_node in root.getElementsByTagName("state"):
            maps = {}
            for m in s_node.getElementsByTagName("permission-map"):
                maps[m.getAttribute("name")] = (
                    {r.firstChild.data.strip() for r in m.getElementsByTagName("permission-role")},
                    m.getAttribute("acquired") == "True")
            states[s_node.getAttribute("state_id")] = {
                "exits": [t.getAttribute("transition_id") for t in s_node.getElementsByTagName("exit-transition")],
                "maps": maps}
        transitions = {}
        for t in root.getElementsByTagName("transition"):
            action = t.getElementsByTagName("action")
            transitions[t.getAttribute("transition_id")] = {
                "trigger": t.getAttribute("trigger") or "USER",
                "listed": bool(action and action[0].firstChild and action[0].firstChild.data.strip()
                               and action[0].getAttribute("category") == "workflow"),
                "permissions": [x.firstChild.data.strip() for x in t.getElementsByTagName("guard-permission")],
                "roles": [x.firstChild.data.strip() for x in t.getElementsByTagName("guard-role")],
                "expr": [x for x in t.getElementsByTagName("guard-expression") if x.firstChild],
            }
        return {"managed": managed, "states": states, "transitions": transitions,
                "bypass": root.getAttribute("manager_bypass") == "True"}

    parsed = {wid: parse(xml) for wid, xml in definitions.items()}

    def site_roles(permission: str) -> set[str]:
        return rolemap.get(permission, set()) | {"Manager"}

    def granted(wf, state, permission, parent=None) -> set[str]:
        if permission in wf["managed"]:
            roles, acquired = wf["states"][state]["maps"].get(permission, (set(), True))
            if acquired:
                roles = roles | (parent() if parent else site_roles(permission))
            return roles
        return parent() if parent else site_roles(permission)

    parent_wf = parsed.get(parent_workflow) if parent_workflow else None

    def parent_granted(permission):
        if parent_wf is None:
            return lambda: site_roles(permission)
        return lambda: granted(parent_wf, parent_state, permission)

    def has(user_roles, roles):
        return "Anonymous" in roles or bool(user_roles & roles)

    matrix = {}
    for wid, wf in parsed.items():
        if wid == parent_workflow and wid not in WORKFLOWS and wid not in GENERATED:
            continue
        info = {"transitions": sorted(wf["transitions"]), "states": {}}
        for state, sdef in wf["states"].items():
            cells = info["states"][state] = {}
            for role in ROLES:
                user_roles = _roles_of(role)
                perm = lambda p: has(user_roles, granted(wf, state, p, parent_granted(p)))
                if not perm("View"):
                    cells[role] = {"status": 401, "available": []}
                    continue
                available = []
                for tid in sdef["exits"]:
                    t = wf["transitions"].get(tid)
                    if not t or t["trigger"] != "USER" or not t["listed"]:
                        continue
                    if wf["bypass"] and "Manager" in user_roles:
                        available.append(tid)
                        continue
                    if t["permissions"] and not any(perm(p) for p in t["permissions"]):
                        continue
                    if t["roles"] and not (user_roles & set(t["roles"])):
                        continue
                    if t["expr"]:
                        continue  # no expressions in the generated workflows
                    available.append(tid)
                cells[role] = {"status": 200, "available": sorted(available)}
        matrix[wid] = info
    return matrix


# Workflows of the fixture generated from DCWorkflow semantics: plone.app.discussion
# isn't installed on the reference site. Their items are comments of a
# published Page (simple_publication_workflow), which they acquire from.
GENERATED = ["comment_review_workflow", "comment_one_state_workflow"]


def read_rolemaps(*paths: str) -> dict[str, set[str]]:
    import xml.dom.minidom as minidom
    rolemap: dict[str, set[str]] = {}
    for path in paths:
        root = minidom.parse(path).documentElement
        for p in root.getElementsByTagName("permission"):
            rolemap.setdefault(p.getAttribute("name"), set()).update(
                r.getAttribute("name") for r in p.getElementsByTagName("role"))
    return rolemap


@pytest.fixture(scope="module")
def target_matrix():
    url = os.environ.get("WORKFLOW_MATRIX_URL")
    if not url:
        pytest.skip("WORKFLOW_MATRIX_URL not set")
    site = Site(url, os.environ.get("WORKFLOW_MATRIX_KIND", "nick"),
                os.environ.get("WORKFLOW_MATRIX_ADMIN", "admin:admin"))
    return compute_matrix(site)


@pytest.mark.parametrize("workflow", list(WORKFLOWS))
def test_workflow_matrix(target_matrix, workflow):
    reference = json.loads(FIXTURE.read_text())["workflows"]
    cells, diffs = compare({workflow: reference[workflow]}, target_matrix)
    assert not diffs, f"{len(diffs)} of {cells} cells differ:\n" + "\n".join(diffs)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "generate":
        # generate <workflows dir> <rolemap.xml>...: add the GENERATED workflows
        # to the fixture, after checking the generator against the captured ones
        workflows_dir, rolemaps = Path(sys.argv[2]), sys.argv[3:]
        definitions = {d.name: (d / "definition.xml").read_text() for d in workflows_dir.iterdir()
                       if (d / "definition.xml").exists()}
        generated = generate_matrix(definitions, read_rolemaps(*rolemaps),
                                    "simple_publication_workflow", "published")
        fixture = json.loads(FIXTURE.read_text())
        captured = {w: i for w, i in fixture["workflows"].items() if w in WORKFLOWS}
        cells, diffs = compare(captured, generated)
        print(f"generator vs captured Plone: {cells - len(diffs)}/{cells} cells match")
        print("\n".join(diffs))
        fixture["workflows"].update({w: generated[w] for w in GENERATED})
        fixture["generated"] = {"workflows": GENERATED, "how": (
            "DCWorkflow semantics from definition.xml and the rolemaps of Products.CMFPlone and "
            "plone.app.discussion; acquired permissions come from a published Page "
            "(simple_publication_workflow); not installed on the reference site")}
        FIXTURE.write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n")
        print("wrote", FIXTURE)
    elif len(sys.argv) >= 4 and sys.argv[1] in ("capture", "compare"):
        site = Site(sys.argv[2], sys.argv[3], *(sys.argv[4:5] or ["admin:admin"]))
        matrix = compute_matrix(site)
        if sys.argv[1] == "capture":
            FIXTURE.parent.mkdir(exist_ok=True)
            FIXTURE.write_text(json.dumps({
                "source": f"{sys.argv[2]} (Plone 6.2, GET @workflow per role)",
                "captured": time.strftime("%Y-%m-%d"),
                "roles": ROLES,
                "workflows": matrix,
            }, indent=1, sort_keys=True) + "\n")
            print("wrote", FIXTURE)
        else:
            reference = json.loads(FIXTURE.read_text())["workflows"]
            cells, diffs = compare({w: reference[w] for w in WORKFLOWS}, matrix)
            print(f"{cells - len(diffs)}/{cells} cells match")
            print("\n".join(diffs))
    else:
        print(__doc__)
