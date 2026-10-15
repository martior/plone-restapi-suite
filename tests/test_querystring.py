"""@querystring and @querystring-search (plone.app.querystring operations).

Reference: docs/source/endpoints/querystring.md, querystringsearch.md,
services/querystring/get.py, services/querystringsearch/get.py and
plone.app.querystring's queryparser (in the plone-backend:6.2 image).

Operations are sent with their full plone.app.querystring names, which is
what Volto's listing/search blocks send.

Fixture tree (``seed``), inside the published sandbox:

    <sandbox>            Folder, published
      alpha              Document, published, "Alpha zebra", Subject red+blue
      beta               Document, private,   "Beta æøå",    Subject blue
      sub                Folder,   published, "Sub folder"
        gamma            Document, private,   "Gamma"
        delta            Document, published, "Delta"

Note: @querystring-search excludes the context object itself from the
results (unlike @search).
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from restapi_suite.assertions import DENIED, same_outcome
from restapi_suite.listing import QS, ids, ordered_ids

pytestmark = pytest.mark.area("querystring")

NICK = "nick"
ALL = ["alpha", "beta", "delta", "gamma", "sub"]
HERE = {"i": "path", "o": QS + "string.relativePath", "v": "."}


def seed(api, sb):
    api.publish(sb)
    api.doc(sb, "alpha", title="Alpha zebra", description="first item", subjects=["red", "blue"])
    api.publish(f"{sb}/alpha")
    api.doc(sb, "beta", title="Beta æøå", subjects=["blue"])
    api.folder(sb, "sub", title="Sub folder")
    api.publish(f"{sb}/sub")
    api.doc(f"{sb}/sub", "gamma", title="Gamma")
    api.doc(f"{sb}/sub", "delta", title="Delta")
    api.publish(f"{sb}/sub/delta")


def qs(api, path, query, as_="admin", **kw):
    return api.post(f"{path}/@querystring-search", as_=as_, json={"query": query, **kw})


def row(i, o, v=""):
    return {"i": i, "o": QS + o, "v": v}


# ------------------------------------------------------------ @querystring

def test_querystring_shape(api):
    r = api.get("@querystring")
    assert r.status == 200, r
    assert isinstance(r.json["indexes"], dict) and isinstance(r.json["sortable_indexes"], dict)
    assert r.json["@id"].endswith("/@querystring")


def test_querystring_contextual_id(api, sandbox):
    r = api.get(f"{sandbox}/@querystring")
    assert r.status == 200 and r.json["@id"].endswith(f"{sandbox}/@querystring"), r


def test_querystring_anonymous_allowed(api):
    assert api.get("@querystring", as_="anonymous").status == 200


COMMON_INDEXES = ["Creator", "Description", "SearchableText", "Subject", "Title", "created", "effective",
                  "end", "expires", "getId", "modified", "path", "portal_type", "review_state", "start"]


@pytest.mark.parametrize("name", COMMON_INDEXES)
def test_querystring_index_present(api, name):
    idx = api.get("@querystring").json["indexes"]
    assert name in idx, sorted(idx)
    e = idx[name]
    for k in ("title", "description", "group", "enabled", "sortable", "operations", "operators", "values",
              "vocabulary"):
        assert k in e, (name, sorted(e))
    assert set(e["operations"]) == set(e["operators"]), (name, e["operations"], list(e["operators"]))


@pytest.mark.parametrize("name", ["isFolderish", "sortable_title", "getObjPositionInParent"])
def test_querystring_volto_indexes_present(api, name):
    """Indexes Volto's listing block offers (folderish filter, title/position sorting)."""
    assert name in api.get("@querystring").json["indexes"]


@pytest.mark.parametrize("name", ["sortable_title", "getObjPositionInParent", "created", "modified",
                                  "effective", "getId"])
def test_querystring_sortable_indexes(api, name):
    """Volto's sort-on dropdown is built from sortable_indexes."""
    assert name in api.get("@querystring").json["sortable_indexes"]


def test_querystring_operation_names_are_plone_app_querystring(api):
    """Operation ids are the full registry names Volto sends back in queries."""
    idx = api.get("@querystring").json["indexes"]
    for name in ("review_state", "portal_type", "path", "created", "Subject"):
        for op in idx[name]["operations"]:
            assert op.startswith(QS), (name, op)


@pytest.mark.parametrize("name,ops", [
    ("path", ["string.absolutePath", "string.relativePath", "string.path"]),
    ("review_state", ["selection.any", "selection.none"]),
    ("portal_type", ["selection.any", "selection.none"]),
    ("Subject", ["selection.any", "selection.all", "selection.none"]),
    ("created", ["date.today", "date.between", "date.beforeToday", "date.afterToday", "date.lessThan",
                 "date.largerThan", "date.lessThanRelativeDate", "date.largerThanRelativeDate",
                 "date.beforeRelativeDate", "date.afterRelativeDate"]),
    ("Creator", ["string.currentUser", "selection.any"]),
    ("SearchableText", ["string.contains"]),
], ids=lambda x: x if isinstance(x, str) else None)
def test_querystring_index_operations(api, name, ops):
    have = api.get("@querystring").json["indexes"][name]["operations"]
    short = {o[len(QS):] if o.startswith(QS) else o for o in have}
    assert set(ops) <= short, (name, sorted(short))


def test_querystring_operator_description(api):
    e = api.get("@querystring").json["indexes"]["review_state"]
    op = e["operators"][QS + "selection.any"]
    assert op["title"] and op["widget"] == "MultipleSelectionWidget"


def test_querystring_review_state_values(api):
    """Selection indexes ship their vocabulary values inline (fetch_vocabulary)."""
    e = api.get("@querystring").json["indexes"]["review_state"]
    assert {"private", "published"} <= set(e["values"]), e["values"]
    assert e["values"]["published"]["title"]


def test_querystring_portal_type_values(api):
    e = api.get("@querystring").json["indexes"]["portal_type"]
    assert {api.type("document"), api.type("folder")} <= set(e["values"]), sorted(e["values"])


# ------------------------------------------------------------ @querystring-search: path

def test_qs_relative_path_excludes_context(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE])
    assert ids(r) == ALL
    assert r.json["items_total"] == 5


@pytest.mark.parametrize("ctx,v,expected", [
    ("", "./sub", ["delta", "gamma", "sub"]),
    ("", ".::1", ["alpha", "beta", "sub"]),
    ("sub", "..", ["SB", "alpha", "beta", "delta", "gamma"]),
    ("sub", "..::1", ["alpha", "beta"]),
    ("sub", ".", ["delta", "gamma"]),
], ids=["child", "depth1", "parent", "parent-depth1", "subfolder"])
def test_qs_relative_path(api, sandbox, ctx, v, expected):
    seed(api, sandbox)
    path = f"{sandbox}/{ctx}" if ctx else sandbox
    exp = sorted(sandbox.rsplit("/", 1)[-1] if e == "SB" else e for e in expected)
    assert ids(qs(api, path, [row("path", "string.relativePath", v)])) == exp


def test_qs_absolute_path(api, sandbox):
    """absolutePath takes a site-relative path (no portal id)."""
    seed(api, sandbox)
    r = qs(api, sandbox, [row("path", "string.absolutePath", f"/{sandbox}/sub")])
    assert ids(r) == ["delta", "gamma", "sub"]


def test_qs_absolute_path_with_depth(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [row("path", "string.absolutePath", f"/{sandbox}::1")])
    assert ids(r) == ["alpha", "beta", "sub"]


def test_qs_absolute_path_from_other_context(api, sandbox):
    """An absolute path query ignores the context it is posted to."""
    seed(api, sandbox)
    r = qs(api, f"{sandbox}/alpha", [row("path", "string.absolutePath", f"/{sandbox}/sub::1")])
    assert ids(r) == ["delta", "gamma"]


def test_qs_two_paths_are_or(api, sandbox):
    seed(api, sandbox)
    api.folder(sandbox, "other")
    api.doc(f"{sandbox}/other", "o1")
    r = qs(api, sandbox, [row("path", "string.absolutePath", f"/{sandbox}/sub::1"),
                          row("path", "string.absolutePath", f"/{sandbox}/other::1")])
    assert ids(r) == ["delta", "gamma", "o1"]


def test_qs_path_nonexistent_is_empty(api, sandbox):
    seed(api, sandbox)
    assert ids(qs(api, sandbox, [row("path", "string.absolutePath", f"/{sandbox}/nonexistent")])) == []


# ------------------------------------------------------------ selection / string operations

@pytest.mark.parametrize("query,expected", [
    ([row("portal_type", "selection.any", ["{type:document}"])], ["alpha", "beta", "delta", "gamma"]),
    ([row("portal_type", "selection.any", ["{type:folder}"])], ["sub"]),
    ([row("portal_type", "selection.any", ["{type:document}", "{type:folder}"])], ALL),
    ([row("portal_type", "selection.none", ["{type:document}"])], ["sub"]),
    ([row("review_state", "selection.any", ["published"])], ["alpha", "delta", "sub"]),
    ([row("review_state", "selection.any", ["private"])], ["beta", "gamma"]),
    ([row("review_state", "selection.none", ["published"])], ["beta", "gamma"]),
    ([row("Subject", "selection.any", ["red", "blue"])], ["alpha", "beta"]),
    ([row("Subject", "selection.all", ["red", "blue"])], ["alpha"]),
    ([row("Subject", "selection.none", ["red"])], ["beta", "delta", "gamma", "sub"]),
    ([row("Subject", "selection.any", ["green"])], []),
    ([row("Title", "string.contains", "zebra")], ["alpha"]),
    ([row("SearchableText", "string.contains", "zebra")], ["alpha"]),
    ([row("SearchableText", "string.contains", "first")], ["alpha"]),
    ([row("SearchableText", "string.contains", "æøå")], ["beta"]),
    ([row("SearchableText", "string.contains", "zeb*")], ["alpha"]),
    ([row("getId", "string.is", "beta")], ["beta"]),
    ([row("portal_type", "selection.any", ["{type:document}"]), row("review_state", "selection.any", ["published"])],
     ["alpha", "delta"]),
], ids=["type-doc", "type-folder", "type-both", "type-none", "state-published", "state-private", "state-none",
        "subject-any", "subject-all", "subject-none", "subject-unknown", "title-contains", "text-contains",
        "text-description", "text-unicode", "text-wildcard", "id-is", "type-and-state"])
def test_qs_operations(api, sandbox, query, expected):
    seed(api, sandbox)
    assert ids(qs(api, sandbox, [HERE] + query)) == expected


def test_qs_current_user(api, sandbox):
    """Creator / string.currentUser matches the requesting user."""
    seed(api, sandbox)
    u = api.create_user()
    api.share(sandbox, u, ["Contributor"])
    api.create(sandbox, "document", id="mine", title="Mine", as_=u)
    r = qs(api, sandbox, [HERE, row("Creator", "string.currentUser")], as_=u)
    assert ids(r) == ["mine"]
    r = qs(api, sandbox, [HERE, row("Creator", "string.currentUser")])
    assert "mine" not in ids(r) and "alpha" in ids(r)


def test_qs_creator_selection(api, sandbox):
    seed(api, sandbox)
    u = api.create_user()
    api.share(sandbox, u, ["Contributor"])
    api.create(sandbox, "document", id="mine", title="Mine", as_=u)
    assert ids(qs(api, sandbox, [HERE, row("Creator", "selection.any", [u.id])])) == ["mine"]


# ------------------------------------------------------------ date operations

def _date_seed(api, sb, with_today=False):
    today = dt.datetime.now(dt.timezone.utc)
    soon = (today + dt.timedelta(days=3)).strftime("%Y-%m-%dT12:00:00")
    api.doc(sb, "past", title="Past", effective="2001-06-01T12:00:00")
    api.doc(sb, "soon", title="Soon", effective=soon)
    api.doc(sb, "future", title="Future", effective="2099-06-01T12:00:00")
    if with_today:
        api.doc(sb, "today", title="Today", effective=today.strftime("%Y-%m-%dT%H:%M:00"))


DOC = row("portal_type", "selection.any", ["{type:document}"])


@pytest.mark.parametrize("op,v,expected", [
    ("date.beforeToday", "", ["past"]),
    ("date.afterToday", "", ["future", "soon"]),
    ("date.between", ["2000-01-01", "2010-01-01"], ["past"]),
    ("date.between", ["2010-01-01", "2000-01-01"], ["past"]),   # values are sorted
    ("date.between", ["2000-01-01", "2100-01-01"], ["future", "past", "soon"]),
    ("date.lessThan", "2050-01-01", ["past", "soon"]),
    ("date.largerThan", "2050-01-01", ["future"]),
    ("date.lessThanRelativeDate", 7, ["soon"]),              # between today and N days from now
    ("date.afterRelativeDate", 30, ["future"]),              # after today + N days
    ("date.beforeRelativeDate", 30, ["past"]),               # before today - N days
], ids=["beforeToday", "afterToday", "between", "between-reversed", "between-all", "lessThan", "largerThan",
        "lessThanRelativeDate", "afterRelativeDate", "beforeRelativeDate"])
def test_qs_date_operations(api, sandbox, op, v, expected):
    _date_seed(api, sandbox)
    assert ids(qs(api, sandbox, [HERE, DOC, row("effective", op, v)])) == expected


def test_qs_date_today(api, sandbox):
    _date_seed(api, sandbox, with_today=True)
    assert ids(qs(api, sandbox, [HERE, DOC, row("effective", "date.today")])) == ["today"]


def test_qs_two_date_rows_merge_into_range(api, sandbox):
    """largerThan + lessThan on one index are merged into a min:max range."""
    _date_seed(api, sandbox)
    r = qs(api, sandbox, [HERE, DOC, row("effective", "date.largerThan", "2000-01-01"),
                          row("effective", "date.lessThan", "2050-01-01")])
    assert ids(r) == ["past", "soon"]


# ------------------------------------------------------------ sorting, batching, limit

def test_qs_sort_on_title(api, sandbox):
    seed(api, sandbox)
    assert ordered_ids(qs(api, sandbox, [HERE, DOC], sort_on="sortable_title")) == ["alpha", "beta", "delta", "gamma"]


def test_qs_sort_order_descending(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE, DOC], sort_on="sortable_title", sort_order="descending")
    assert ordered_ids(r) == ["gamma", "delta", "beta", "alpha"]


@pytest.mark.plone_bug("querystringsearch.md documents sort_order 'reverse' as an alias of 'descending', "
                       "but Plone sorts ascending (only @search honours 'reverse')")
@pytest.mark.known_divergence(NICK, kind="plone-bug", reason="Nick treats every value other than "
                              "'ascending' as descending, which matches the documentation")
def test_qs_sort_order_reverse_is_ignored(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE, DOC], sort_on="sortable_title", sort_order="reverse")
    assert ordered_ids(r) == ["alpha", "beta", "delta", "gamma"]


def test_qs_sort_on_position_in_parent(api, sandbox):
    """getObjPositionInParent is Volto's default listing order (folder order)."""
    for n in ("p3", "p1", "p2"):
        api.doc(sandbox, n, title=n)
    r = qs(api, sandbox, [HERE, DOC], sort_on="getObjPositionInParent")
    assert ordered_ids(r) == ["p3", "p1", "p2"]


def test_qs_sort_on_unknown_index_is_ignored(api, sandbox):
    seed(api, sandbox)
    assert ids(qs(api, sandbox, [HERE], sort_on="no_such_index")) == ALL


def test_qs_default_batch_size_is_25(api, sandbox):
    api.publish(sandbox)
    for n in range(27):
        api.doc(sandbox, f"d{n:02d}", title=f"Doc {n:02d}")
    r = qs(api, sandbox, [HERE])
    assert len(r.json["items"]) == 25 and r.json["items_total"] == 27
    assert "next" in r.json["batching"]


def test_qs_b_size_and_batching(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE], sort_on="sortable_title", b_size=2)
    assert ordered_ids(r) == ["alpha", "beta"] and r.json["items_total"] == 5
    assert set(r.json["batching"]) == {"@id", "first", "last", "next"}, r.json["batching"]


def test_qs_b_start(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE], sort_on="sortable_title", b_size=2, b_start=2)
    assert ordered_ids(r) == ["delta", "gamma"] and r.json["items_total"] == 5
    assert {"prev", "next"} <= set(r.json["batching"])


def test_qs_b_start_past_end(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE], b_size=2, b_start=100)
    assert r.status == 200 and r.json["items"] == [] and r.json["items_total"] == 5, r


def test_qs_huge_b_size(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE], b_size=100000)
    assert ids(r) == ALL and "batching" not in r.json


def test_qs_limit(api, sandbox):
    """limit caps the result set (and items_total)."""
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE], sort_on="sortable_title", limit=3)
    assert ordered_ids(r) == ["alpha", "beta", "delta"]
    assert r.json["items_total"] == 3


def test_qs_limit_with_batching(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE], sort_on="sortable_title", limit=3, b_size=2, b_start=2)
    assert ordered_ids(r) == ["delta"] and r.json["items_total"] == 3


def test_qs_fullobjects(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE, row("getId", "string.is", "alpha")], fullobjects=True)
    [item] = r.json["items"]
    for k in ("parent", "subjects", "UID", "review_state", "@components"):
        assert k in item, sorted(item)


def test_qs_metadata_fields(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE, row("getId", "string.is", "alpha")], metadata_fields=["UID", "Subject"])
    [item] = r.json["items"]
    assert item["UID"] and sorted(item["Subject"]) == ["blue", "red"]


def test_qs_summary_shape(api, sandbox):
    seed(api, sandbox)
    [item] = qs(api, sandbox, [HERE, row("getId", "string.is", "alpha")]).json["items"]
    for k in ("@id", "@type", "title", "description", "review_state"):
        assert k in item, sorted(item)
    assert "UID" not in item


def test_qs_get_with_json_query(api, sandbox):
    """GET @querystring-search?query=<json> is equivalent to POST."""
    seed(api, sandbox)
    q = json.dumps({"query": [HERE, row("review_state", "selection.any", ["published"])],
                    "sort_on": "sortable_title"})
    r = api.get(f"{sandbox}/@querystring-search", params={"query": q})
    assert ordered_ids(r) == ["alpha", "delta", "sub"]


# ------------------------------------------------------------ invalid input

def test_qs_empty_query_is_400(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [])
    assert r.status == 400, r


def test_qs_missing_query_is_400(api, sandbox):
    seed(api, sandbox)
    r = api.post(f"{sandbox}/@querystring-search", json={})
    assert r.status == 400, r


def test_qs_unknown_operation_is_400(api, sandbox):
    seed(api, sandbox)
    r = qs(api, sandbox, [HERE, row("Title", "nope", "x")])
    assert r.status == 400, r


@pytest.mark.known_divergence(NICK, kind="deliberate", reason="Nick's native operation ids are the "
                              "prefix-less names it advertises in @querystring; it accepts them")
def test_qs_short_operation_name_is_400(api, sandbox):
    """Only registered (full) operation names are valid in Plone."""
    seed(api, sandbox)
    r = api.post(f"{sandbox}/@querystring-search",
                 json={"query": [HERE, {"i": "portal_type", "o": "selection.any", "v": ["{type:document}"]}]})
    assert r.status == 400, r


# ------------------------------------------------------------ permissions and freshness

VIS = {
    "anonymous": ["alpha", "delta", "sub"],
    "member": ["alpha", "delta", "sub"],
    "local_editor": ALL,
    "site_admin": ALL,
    "manager": ALL,
}


@pytest.mark.parametrize("persona", list(VIS))
@pytest.mark.known_divergence(NICK, reason="search ACL omits local roles other than Reader (local Editor)",
                              ref="A2#2", ids=["local_editor"])
def test_qs_permission_filtered(api, sandbox, persona):
    seed(api, sandbox)
    assert ids(qs(api, sandbox, [HERE], as_=persona)) == VIS[persona]


@pytest.mark.known_divergence(NICK, reason="the local Reader is granted after the content exists and "
                              "@sharing does not reindex the ACL", ref="A2#1")
def test_qs_local_reader_on_subfolder(api, sandbox):
    seed(api, sandbox)
    u = api.create_user()
    api.share(f"{sandbox}/sub", u, ["Reader"])
    assert ids(qs(api, sandbox, [HERE], as_=u)) == ["alpha", "delta", "gamma", "sub"]


def test_qs_blocked_inheritance_hides_from_local_editor(api, sandbox):
    seed(api, sandbox)
    api.block_inheritance(f"{sandbox}/sub")
    got = ids(qs(api, sandbox, [HERE], as_="local_editor"))
    assert "gamma" not in got and "delta" in got, got


@pytest.mark.parametrize("persona,expected", [("anonymous", 401), ("member", 401), ("local_editor", 200)])
def test_qs_private_context_requires_view(api, sandbox, persona, expected):
    api.doc(sandbox, "d")
    assert same_outcome(expected, qs(api, sandbox, [HERE], as_=persona).status)


def test_qs_after_move(api, sandbox):
    seed(api, sandbox)
    r = api.post(f"{sandbox}/sub/@move", json={"source": f"/{sandbox}/beta"}, compare=False)
    assert r.status == 200, r
    assert ids(qs(api, f"{sandbox}/sub", [HERE])) == ["beta", "delta", "gamma"]


def test_qs_after_title_change(api, sandbox):
    seed(api, sandbox)
    api.patch(f"{sandbox}/beta", json={"title": "Beta quokka"}, compare=False)
    assert ids(qs(api, sandbox, [HERE, row("SearchableText", "string.contains", "quokka")])) == ["beta"]


@pytest.mark.known_divergence(NICK, reason="@sharing POST does not reindex the catalog ACL", ref="A2#1")
def test_qs_after_sharing_grant(api, sandbox):
    seed(api, sandbox)
    u = api.create_user()
    assert "gamma" not in ids(qs(api, sandbox, [HERE], as_=u))
    api.share(f"{sandbox}/sub", u, ["Reader"])
    assert "gamma" in ids(qs(api, sandbox, [HERE], as_=u))
