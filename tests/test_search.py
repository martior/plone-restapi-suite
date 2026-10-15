"""@search: indexes, sorting, batching, metadata, permission filtering and
index freshness.

Reference: docs/source/endpoints/searching.md, services/search/get.py,
search/handler.py and the http-examples search_*.

Fixture tree (``seed``), inside the published sandbox:

    <sandbox>            Folder, published
      alpha              Document, published, "Alpha zebra", Subject red+blue
      beta               Document, private,   "Beta æøå",    Subject blue
      sub                Folder,   published, "Sub folder"
        gamma            Document, private,   "Gamma"
        delta            Document, published, "Delta"

Unordered results are compared as sorted id lists (Plone's default order
without sort_on is catalog order and not a contract).
"""

from __future__ import annotations

import time

import pytest

from restapi_suite.api import uid
from restapi_suite.assertions import DENIED, item_ids, same_outcome
from restapi_suite.listing import get_each, ids, ordered_ids, phys

pytestmark = pytest.mark.area("search")

NICK = "nick"


def sb_id(sandbox: str) -> str:
    return sandbox.rsplit("/", 1)[-1]


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


def s(api, path, as_="admin", **params):
    return api.get(f"{path}/@search", as_=as_, params=params)


ALL = ["alpha", "beta", "delta", "gamma", "sub"]


# ------------------------------------------------------------ basics

def test_search_includes_context_and_descendants(api, sandbox):
    """A search on a container includes the container itself (ZCatalog path semantics)."""
    seed(api, sandbox)
    r = s(api, sandbox)
    assert ids(r) == sorted(ALL + [sb_id(sandbox)])
    assert r.json["items_total"] == 6
    assert r.json["@id"].endswith(f"{sandbox}/@search")


def test_search_on_document_context_returns_only_itself(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, f"{sandbox}/alpha")) == ["alpha"]


def test_search_summary_shape(api, sandbox):
    """Default summaries carry @id, @type, title, description, review_state."""
    seed(api, sandbox)
    r = s(api, sandbox, getId="alpha")
    [item] = r.json["items"]
    for k in ("@id", "@type", "title", "description", "review_state"):
        assert k in item, item
    assert item["@type"] == api.type("document")
    assert item["title"] == "Alpha zebra"
    assert item["description"] == "first item"
    assert item["review_state"] == "published"


def test_search_summary_has_no_uid_by_default(api, sandbox):
    """Plone's default summary is a fixed metadata set; UID needs metadata_fields."""
    seed(api, sandbox)
    [item] = s(api, sandbox, getId="alpha").json["items"]
    assert "UID" not in item, sorted(item)


def test_search_empty_result(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, SearchableText="nosuchwordanywhere")
    assert r.status == 200, r
    assert r.json["items"] == [] and r.json["items_total"] == 0
    assert "batching" not in r.json


def test_search_unknown_parameter_is_ignored(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, sandbox, foo="bar")) == sorted(ALL + [sb_id(sandbox)])


def test_search_unknown_portal_type_is_empty(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, sandbox, portal_type="NoSuchType")) == []


# ------------------------------------------------------------ field indexes

def test_search_portal_type(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, sandbox, portal_type="{type:document}")) == ["alpha", "beta", "delta", "gamma"]
    assert ids(s(api, sandbox, portal_type="{type:folder}")) == sorted([sb_id(sandbox), "sub"])


def test_search_portal_type_list_is_or(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, portal_type=["{type:document}", "{type:folder}"])
    assert ids(r) == sorted(ALL + [sb_id(sandbox)])


@pytest.mark.parametrize("state,expected", [
    ("published", ["alpha", "delta", "sub", "SB"]),
    ("private", ["beta", "gamma"]),
    ("pending", []),
])
def test_search_review_state(api, sandbox, state, expected):
    seed(api, sandbox)
    exp = sorted(sb_id(sandbox) if e == "SB" else e for e in expected)
    assert ids(s(api, sandbox, review_state=state)) == exp


def test_search_review_state_list(api, sandbox):
    seed(api, sandbox)
    api.transition(f"{sandbox}/beta", "submit")
    r = s(api, sandbox, review_state=["pending", "private"], portal_type="{type:document}")
    assert ids(r) == ["beta", "gamma"]


def test_search_get_id(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, sandbox, getId="gamma")) == ["gamma"]


def test_search_uid(api, sandbox):
    seed(api, sandbox)
    uid = api.get(f"{sandbox}/sub/gamma", compare=False).json["UID"]
    # UIDs differ per side: look them up on each side.
    r = get_each(api, f"{sandbox}/@search", "admin",
                 lambda side: {"UID": side.request("GET", f"{sandbox}/sub/gamma", side.admin).json["UID"]})
    assert ids(r) == ["gamma"], uid


def test_search_unknown_uid_is_empty(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, sandbox, UID="0123456789abcdef0123456789abcdef")) == []


def test_search_malformed_uid_is_empty(api, sandbox):
    """A UID that is not even UUID-shaped is just a non-matching value."""
    seed(api, sandbox)
    assert ids(s(api, sandbox, UID="nonexistent")) == []


# ------------------------------------------------------------ keyword index (Subject)

def test_search_subject_single(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, sandbox, Subject="blue")) == ["alpha", "beta"]
    assert ids(s(api, sandbox, Subject="red")) == ["alpha"]


def test_search_subject_unknown_is_empty(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, sandbox, Subject="green")) == []


def test_search_subject_list_is_or(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, sandbox, Subject=["red", "blue"])) == ["alpha", "beta"]


def test_search_subject_operator_and(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, **{"Subject.query": ["red", "blue"], "Subject.operator": "and"})
    assert ids(r) == ["alpha"]


def test_search_subject_metadata(api, sandbox):
    seed(api, sandbox)
    [item] = s(api, sandbox, getId="alpha", metadata_fields="Subject").json["items"]
    assert sorted(item["Subject"]) == ["blue", "red"]


def test_search_subject_reindexed_after_edit(api, sandbox):
    seed(api, sandbox)
    api.patch(f"{sandbox}/beta", json={"subjects": ["green"]}, compare=False)
    assert ids(s(api, sandbox, Subject="green")) == ["beta"]
    assert ids(s(api, sandbox, Subject="blue")) == ["alpha"]


# ------------------------------------------------------------ path

@pytest.mark.parametrize("depth,expected", [
    (0, ["SB"]),
    (1, ["alpha", "beta", "sub"]),
    (2, ["SB", "alpha", "beta", "delta", "gamma", "sub"]),
    (-1, ["SB", "alpha", "beta", "delta", "gamma", "sub"]),
])
@pytest.mark.plone_bug("ExtendedPathIndex: depth=1 excludes the context itself but depth>=2 includes it again")
@pytest.mark.known_divergence(NICK, kind="plone-bug", ids=["2-*"],
                              reason="Nick excludes the context for every depth >= 1 (consistent)")
def test_search_path_depth(api, sandbox, depth, expected):
    """path.depth without path.query applies to the context path."""
    seed(api, sandbox)
    exp = sorted(sb_id(sandbox) if e == "SB" else e for e in expected)
    assert ids(s(api, sandbox, **{"path.depth": depth})) == exp


def test_search_path_query_absolute(api, sandbox):
    """path.query takes the physical path; the search context does not matter then."""
    seed(api, sandbox)
    r = get_each(api, f"{sandbox}/@search", "admin", lambda side: {"path.query": phys(side, f"{sandbox}/sub")})
    assert ids(r) == ["delta", "gamma", "sub"]


def test_search_path_string_param(api, sandbox):
    """`path=<physical path>` (no dotted option) is accepted as the path query."""
    seed(api, sandbox)
    r = get_each(api, f"{sandbox}/@search", "admin", lambda side: {"path": phys(side, f"{sandbox}/sub")})
    assert ids(r) == ["delta", "gamma", "sub"]


def test_search_path_query_with_depth(api, sandbox):
    seed(api, sandbox)
    r = get_each(api, f"{sandbox}/@search", "admin",
                 lambda side: {"path.query": phys(side, f"{sandbox}/sub"), "path.depth": 1})
    assert ids(r) == ["delta", "gamma"]


def test_search_path_query_multiple(api, sandbox):
    seed(api, sandbox)
    r = get_each(api, f"{sandbox}/@search", "admin",
                 lambda side: {"path.query": [phys(side, f"{sandbox}/sub"), phys(side, f"{sandbox}/alpha")]})
    assert ids(r) == ["alpha", "delta", "gamma", "sub"]


def test_search_path_query_nonexistent_is_empty(api, sandbox):
    seed(api, sandbox)
    r = get_each(api, f"{sandbox}/@search", "admin",
                 lambda side: {"path.query": phys(side, f"{sandbox}/nonexistent")})
    assert ids(r) == []


def test_search_path_query_from_site_root(api, sandbox):
    """A path query on the site root finds the same subtree."""
    seed(api, sandbox)
    r = get_each(api, "@search", "admin", lambda side: {"path.query": phys(side, f"{sandbox}/sub"), "path.depth": 1})
    assert ids(r) == ["delta", "gamma"]


def test_search_path_prefix_is_not_a_sibling_match(api, sandbox):
    """A path query on .../sub must not match a sibling .../sub2 (path prefix vs segment)."""
    seed(api, sandbox)
    api.folder(sandbox, "sub2", title="Sub two")
    api.doc(f"{sandbox}/sub2", "eps", title="Eps")
    r = get_each(api, f"{sandbox}/@search", "admin", lambda side: {"path.query": phys(side, f"{sandbox}/sub")})
    assert ids(r) == ["delta", "gamma", "sub"]
    assert ids(s(api, f"{sandbox}/sub")) == ["delta", "gamma", "sub"]


def test_search_deep_tree(api, sandbox):
    """Ten levels deep: depth limits and contextual search keep working."""
    api.publish(sandbox)
    path = sandbox
    for n in range(10):
        path = api.folder(path, f"l{n}", title=f"Level {n}")
    api.doc(path, "leaf", title="Deep leaf xylophone")
    assert ids(s(api, sandbox, SearchableText="xylophone")) == ["leaf"]
    assert ids(s(api, f"{sandbox}/l0/l1/l2", **{"path.depth": 1})) == ["l3"]
    assert ids(s(api, f"{sandbox}/l0/l1/l2/l3/l4/l5/l6/l7/l8/l9")) == ["l9", "leaf"]
    assert len(s(api, sandbox, portal_type="{type:folder}").json["items"]) == 11


# ------------------------------------------------------------ SearchableText

@pytest.mark.parametrize("term,expected", [
    ("zebra", ["alpha"]),
    ("Zebra", ["alpha"]),
    ("ZEBRA", ["alpha"]),
    ("first", ["alpha"]),          # description is part of SearchableText
    ("zeb*", ["alpha"]),           # trailing wildcard
    ("Beta", ["beta"]),
    ("alpha zebra", ["alpha"]),    # implicit AND
    ("alpha beta", []),
    ("zebra AND alpha", ["alpha"]),
    ("zebra OR beta", ["alpha", "beta"]),
    ('"alpha zebra"', ["alpha"]),  # phrase
    ("Gamma", ["gamma"]),
])
def test_search_searchable_text(api, sandbox, term, expected):
    seed(api, sandbox)
    assert ids(s(api, sandbox, SearchableText=term, portal_type="{type:document}")) == expected


UNICODE_TITLE = "Crème brûlée 日本語 Ünïcode"


@pytest.mark.parametrize("term", ["brûlée", "Crème", "CRÈME", "日本語", "ünïcode", "Ünïcode", "café", "naïve"])
def test_search_unicode_terms(api, sandbox, term):
    api.doc(sandbox, "creme", title=UNICODE_TITLE, description="naïve café")
    assert ids(s(api, sandbox, SearchableText=term, portal_type="{type:document}")) == ["creme"]


@pytest.mark.parametrize("term", ["creme", "brulee", "naive", "cafe"])
def test_search_unicode_accent_folding(api, sandbox, term):
    """Plone's text index folds accents: 'creme' finds 'Crème'."""
    api.doc(sandbox, "creme", title=UNICODE_TITLE, description="naïve café")
    assert ids(s(api, sandbox, SearchableText=term, portal_type="{type:document}")) == ["creme"]


def test_search_unicode_title_roundtrip(api, sandbox):
    api.doc(sandbox, "creme", title=UNICODE_TITLE)
    [item] = s(api, sandbox, getId="creme").json["items"]
    assert item["title"] == UNICODE_TITLE


@pytest.mark.parametrize("term,expected", [
    ("<script>", []),
    ("_", []),
    ("%00", []),
    ("a", []),
    ("*zebra", ["alpha"]),
    ("ze?ra", ["alpha"]),
    ("z*", ["alpha"]),
    ("zebra; DROP TABLE x", []),
    ("zebra' OR '1'='1", ["alpha"]),  # parsed as: zebra OR 1=1
])
def test_search_searchable_text_special_but_valid(api, sandbox, term, expected):
    """Terms with special characters that Plone's query parser accepts."""
    seed(api, sandbox)
    assert ids(s(api, sandbox, SearchableText=term, portal_type="{type:document}")) == expected


@pytest.mark.parametrize("term", ["", "  ", "(", ")", "*"])
@pytest.mark.plone_bug("an empty/operator-only SearchableText answers 200 with a bare JSON list [] "
                       "instead of a search result object")
@pytest.mark.known_divergence(NICK, kind="plone-bug", reason="Nick answers a normal empty result object "
                              "(or 500 for parentheses, see test_search_searchable_text_never_500)")
def test_search_searchable_text_empty_query_bare_list(api, sandbox, term):
    seed(api, sandbox)
    r = s(api, sandbox, SearchableText=term)
    assert r.status == 200 and r.json == [], r


@pytest.mark.parametrize("term", ['"', "?", "\\", "%", "'", "AND", "OR", "NOT", "-", "@", "zebra NOT alpha", "\u200b"])
@pytest.mark.plone_bug("ZCTextIndex query parse errors ('Query contains only common words', 'Token <ATOM> "
                       "required') are not mapped to 400: @search answers 500 with a traceback")
@pytest.mark.known_divergence(NICK, kind="plone-bug", reason="Nick parses every term safely and answers 200 "
                              "(plone.restapi documents no parse errors)")
def test_search_searchable_text_parse_error_is_500(api, sandbox, term):
    seed(api, sandbox)
    r = s(api, sandbox, SearchableText=term)
    assert r.status == 500, r


@pytest.mark.parametrize("term", ["(", ")", "<script>", "zebra AND alpha", '"alpha zebra"', "zebra OR beta"])
def test_search_searchable_text_never_500_for_parseable_terms(api, sandbox, term):
    """Terms Plone parses (status 200) must not crash the backend."""
    seed(api, sandbox)
    r = s(api, sandbox, SearchableText=term)
    assert r.status == 200, r


def test_search_searchable_text_reindexed_after_title_change(api, sandbox):
    seed(api, sandbox)
    api.patch(f"{sandbox}/beta", json={"title": "Beta quokka"}, compare=False)
    assert ids(s(api, sandbox, SearchableText="quokka")) == ["beta"]
    assert ids(s(api, sandbox, SearchableText="æøå")) == []


# ------------------------------------------------------------ sorting

def test_search_sort_on_title(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, sort_on="sortable_title", portal_type="{type:document}")
    assert ordered_ids(r) == ["alpha", "beta", "delta", "gamma"]


@pytest.mark.parametrize("order", ["descending", "reverse"])
def test_search_sort_order_descending(api, sandbox, order):
    seed(api, sandbox)
    r = s(api, sandbox, sort_on="sortable_title", sort_order=order, portal_type="{type:document}")
    assert ordered_ids(r) == ["gamma", "delta", "beta", "alpha"]


def test_search_sort_on_unknown_index_is_ignored(api, sandbox):
    """Plone ignores an unknown sort_on (200, same result set)."""
    seed(api, sandbox)
    r = s(api, sandbox, sort_on="no_such_index")
    assert ids(r) == sorted(ALL + [sb_id(sandbox)])


def test_search_sort_order_invalid_is_ascending(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, sort_on="sortable_title", sort_order="sideways", portal_type="{type:document}")
    assert ordered_ids(r) == ["alpha", "beta", "delta", "gamma"]


def test_search_sort_on_get_id(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, sort_on="getId", portal_type="{type:document}")
    assert ordered_ids(r) == ["alpha", "beta", "delta", "gamma"]


def test_search_modified_metadata_follows_edits(api, sandbox):
    """The catalog's modified metadata is refreshed on edit and equals the object's value.
    (Sorting on modified within one test is not reliable: DateIndex has minute resolution.)"""
    api.doc(sandbox, "m1", title="m1")
    time.sleep(1.1)
    api.patch(f"{sandbox}/m1", json={"description": "touched"}, compare=False)
    obj = api.get(f"{sandbox}/m1", compare=False).json
    [item] = s(api, sandbox, getId="m1", metadata_fields="modified").json["items"]
    assert item["modified"] == obj["modified"], (item["modified"], obj["modified"])
    assert obj["modified"] > obj["created"]


def test_search_sort_on_created_and_modified(api, sandbox):
    """sort_on on the date indexes is accepted and returns the full set."""
    for n in ("c1", "c2", "c3"):
        api.doc(sandbox, n, title=f"z {n}")
    for idx in ("created", "modified", "effective"):
        for order in ("ascending", "descending"):
            r = s(api, sandbox, sort_on=idx, sort_order=order, portal_type="{type:document}")
            assert ids(r) == ["c1", "c2", "c3"], (idx, order)


def test_search_sort_title_is_case_insensitive(api, sandbox):
    """sortable_title is normalised: 'apple' < 'Banana' < 'cherry'."""
    api.doc(sandbox, "c", title="cherry")
    api.doc(sandbox, "b", title="Banana")
    api.doc(sandbox, "a", title="apple")
    r = s(api, sandbox, sort_on="sortable_title", portal_type="{type:document}")
    assert ordered_ids(r) == ["a", "b", "c"]


def test_search_sort_title_natural_numbers(api, sandbox):
    """sortable_title zero-pads numbers: 'Item 2' < 'Item 10'."""
    api.doc(sandbox, "i10", title="Item 10")
    api.doc(sandbox, "i2", title="Item 2")
    r = s(api, sandbox, sort_on="sortable_title", portal_type="{type:document}")
    assert ordered_ids(r) == ["i2", "i10"]


# ------------------------------------------------------------ date ranges

@pytest.mark.parametrize("params,expected", [
    ({"created.query": "2000-01-01", "created.range": "min"}, "all"),
    ({"created.query": "2000-01-01", "created.range": "max"}, "none"),
    ({"created.query": "2100-01-01", "created.range": "max"}, "all"),
    ({"created.query": "2100-01-01", "created.range": "min"}, "none"),
    ({"created.query": ["2000-01-01", "2100-01-01"], "created.range": "min:max"}, "all"),
    ({"created.query": ["2000-01-01", "2001-01-01"], "created.range": "min:max"}, "none"),
    ({"modified.query": "2000-01-01", "modified.range": "min"}, "all"),
    ({"modified.query": "2100-01-01", "modified.range": "min"}, "none"),
], ids=["created-min-past", "created-max-past", "created-max-future", "created-min-future",
        "created-between", "created-between-past", "modified-min-past", "modified-min-future"])
def test_search_date_ranges(api, sandbox, params, expected):
    seed(api, sandbox)
    every = sorted(ALL + [sb_id(sandbox)])
    assert ids(s(api, sandbox, **params)) == (every if expected == "all" else [])


def test_search_effective_range_only_published(api, sandbox):
    """effective is set on publication; private items keep the 'never' floor date."""
    seed(api, sandbox)
    r = s(api, sandbox, **{"effective.query": "2001-01-01", "effective.range": "min"})
    assert ids(r) == sorted(["alpha", "delta", "sub", sb_id(sandbox)])


@pytest.mark.plone_bug("an unparseable date in a range query is a 500 (DateIndex type error), not a 400")
@pytest.mark.known_divergence(NICK, kind="plone-bug", reason="Nick ignores range queries altogether: 200 "
                              "(the missing range support itself is test_search_date_ranges)")
def test_search_invalid_date_is_500(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, **{"created.query": "not-a-date", "created.range": "min"})
    assert r.status == 500, r


def test_search_unknown_range_is_ignored(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, **{"created.query": "2000-01-01", "created.range": "bogus"})
    assert ids(r) == sorted(ALL + [sb_id(sandbox)])


# ------------------------------------------------------------ batching

def _rel(api, url: str) -> str:
    base = api.t.base
    assert url.startswith(base), url
    return url[len(base):].lstrip("/")


def test_search_batching_first_page(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, b_size=2, sort_on="sortable_title")
    j = r.json
    assert len(j["items"]) == 2 and j["items_total"] == 6
    b = j["batching"]
    assert set(b) == {"@id", "first", "last", "next"}, b
    assert "b_start=2" in b["next"] and "b_start=0" in b["first"] and "b_start=4" in b["last"]


def test_search_batching_middle_and_last_page(api, sandbox):
    seed(api, sandbox)
    mid = s(api, sandbox, b_size=2, b_start=2, sort_on="sortable_title").json
    assert set(mid["batching"]) == {"@id", "first", "last", "next", "prev"}
    assert "b_start=0" in mid["batching"]["prev"] and "b_start=4" in mid["batching"]["next"]
    last = s(api, sandbox, b_size=2, b_start=4, sort_on="sortable_title").json
    assert set(last["batching"]) == {"@id", "first", "last", "prev"}
    assert len(last["items"]) == 2 and last["items_total"] == 6


def test_search_batching_links_are_followable(api, sandbox):
    """Following `next` from the first page visits every item exactly once."""
    seed(api, sandbox)
    j = s(api, sandbox, b_size=4, sort_on="sortable_title").json
    seen = item_ids(j)
    nxt = j["batching"]["next"]
    page = api.get(_rel(api, nxt), compare=False).json
    seen += item_ids(page)
    assert "next" not in page["batching"]
    assert sorted(seen) == sorted(ALL + [sb_id(sandbox)])


def test_search_batching_keeps_query_params(api, sandbox):
    seed(api, sandbox)
    b = s(api, sandbox, b_size=1, portal_type="{type:document}", sort_on="sortable_title").json["batching"]
    for link in ("first", "last", "next"):
        assert "portal_type=" in b[link] and "sort_on=sortable_title" in b[link], b


def test_search_b_start_past_end(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, b_size=2, b_start=100)
    assert r.status == 200, r
    assert r.json["items"] == [] and r.json["items_total"] == 6


def test_search_huge_b_size_returns_all_unbatched(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, b_size=100000)
    assert ids(r) == sorted(ALL + [sb_id(sandbox)])
    assert "batching" not in r.json


def test_search_b_size_zero_returns_no_items(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, b_size=0)
    assert r.status == 200 and r.json["items"] == [] and r.json["items_total"] == 6, r


def test_search_negative_b_start_is_start(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, b_start=-1)
    assert ids(r) == sorted(ALL + [sb_id(sandbox)])


@pytest.mark.parametrize("params", [{"b_size": "abc"}, {"b_start": "x"}, {"b_size": -1}],
                         ids=["b_size-abc", "b_start-x", "b_size-negative"])
@pytest.mark.plone_bug("non-integer or negative batch parameters are a 500 (ValueError), not a 400")
@pytest.mark.known_divergence(NICK, kind="plone-bug", reason="Nick is lenient (parseInt fallback): 200")
def test_search_invalid_batch_params_500(api, sandbox, params):
    seed(api, sandbox)
    assert s(api, sandbox, **params).status == 500


@pytest.mark.plone_bug("a non-integer path.depth is a 500 (ExtendedPathIndex), not a 400")
@pytest.mark.known_divergence(NICK, kind="plone-bug", reason="Nick ignores a non-integer depth: 200")
def test_search_invalid_path_depth_500(api, sandbox):
    seed(api, sandbox)
    assert s(api, sandbox, **{"path.depth": "x"}).status == 500


def test_search_default_batch_size_is_25(api, sandbox):
    api.publish(sandbox)
    for n in range(27):
        api.doc(sandbox, f"d{n:02d}", title=f"Doc {n:02d}")
    r = s(api, sandbox, portal_type="{type:document}")
    assert len(r.json["items"]) == 25 and r.json["items_total"] == 27
    assert "next" in r.json["batching"]


@pytest.mark.plone_bug("paging a sort with ties is not stable: with 9 equal titles, b_size=4 pages "
                       "repeat t0 and drop another item (ZCatalog limit sort is not stable across b_start)")
@pytest.mark.known_divergence("plone*", kind="plone-bug", reason="Plone itself repeats/drops items when paging ties")
def test_search_pagination_stable_with_ties(api, sandbox):
    """Paging through a sort with ties (equal titles) yields each item exactly once."""
    for n in range(9):
        api.doc(sandbox, f"t{n}", title="Same title")
    seen: list[str] = []
    for start in range(0, 9, 4):
        seen += item_ids(s(api, sandbox, sort_on="sortable_title", b_size=4, b_start=start,
                           portal_type="{type:document}").json)
    assert sorted(seen) == sorted(f"t{n}" for n in range(9)), seen


def test_search_pagination_unique_sort_key(api, sandbox):
    """Paging through a sort on a unique key (getId) yields each item exactly once, in order."""
    for n in range(9):
        api.doc(sandbox, f"t{n}", title="Same title")
    seen: list[str] = []
    for start in range(0, 9, 4):
        seen += item_ids(s(api, sandbox, sort_on="getId", b_size=4, b_start=start,
                           portal_type="{type:document}").json)
    assert seen == [f"t{n}" for n in range(9)]


def test_search_repeated_query_same_order(api, sandbox):
    for n in range(6):
        api.doc(sandbox, f"r{n}", title="Same title")
    a = ordered_ids(s(api, sandbox, sort_on="sortable_title", portal_type="{type:document}"))
    b = ordered_ids(s(api, sandbox, sort_on="sortable_title", portal_type="{type:document}"))
    assert a == b


# ------------------------------------------------------------ metadata / fullobjects

def test_search_metadata_fields_selected(api, sandbox):
    seed(api, sandbox)
    [item] = s(api, sandbox, getId="alpha", metadata_fields=["UID", "getId", "portal_type"]).json["items"]
    assert item["getId"] == "alpha" and item["portal_type"] == api.type("document")
    assert isinstance(item["UID"], str) and item["UID"]


def test_search_metadata_fields_all(api, sandbox):
    seed(api, sandbox)
    [item] = s(api, sandbox, getId="alpha", metadata_fields="_all").json["items"]
    for k in ("UID", "Title", "Description", "Subject", "getId", "portal_type", "review_state", "created",
              "modified", "effective", "is_folderish", "exclude_from_nav", "Creator"):
        assert k in item, sorted(item)
    assert item["Title"] == "Alpha zebra"


def test_search_metadata_fields_unknown_is_ignored(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, getId="alpha", metadata_fields="no_such_column")
    [item] = r.json["items"]
    assert "no_such_column" not in item


def test_search_fullobjects(api, sandbox):
    seed(api, sandbox)
    [item] = s(api, sandbox, getId="alpha", fullobjects=1).json["items"]
    for k in ("@id", "@type", "UID", "title", "subjects", "parent", "review_state", "created", "modified"):
        assert k in item, sorted(item)
    assert sorted(item["subjects"]) == ["blue", "red"]
    assert item["parent"]["@id"].endswith(sandbox)


def test_search_fullobjects_batched(api, sandbox):
    seed(api, sandbox)
    r = s(api, sandbox, fullobjects=1, b_size=2, sort_on="sortable_title")
    assert len(r.json["items"]) == 2 and r.json["items_total"] == 6
    assert "next" in r.json["batching"]
    assert all("parent" in i for i in r.json["items"])


def test_search_use_site_search_settings_keeps_searchable_types(api, sandbox):
    """use_site_search_settings filters by the searchable types; Documents and Folders stay."""
    seed(api, sandbox)
    r = get_each(api, f"{sandbox}/@search", "admin",
                 lambda side: {"use_site_search_settings": 1, "path.query": phys(side, sandbox)})
    assert ids(r) == sorted(ALL + [sb_id(sandbox)])


def test_search_use_site_search_settings_searches_from_navroot(api, sandbox):
    """Without an explicit path, use_site_search_settings searches from the navigation
    root, not from the context (search/handler.py)."""
    word = f"wombat{uid()}"
    api.doc(sandbox, "far", title=f"Far {word}")
    api.folder(sandbox, "elsewhere")
    assert ids(s(api, f"{sandbox}/elsewhere", SearchableText=word)) == []
    assert ids(s(api, f"{sandbox}/elsewhere", SearchableText=word, use_site_search_settings=1)) == ["far"]


# ------------------------------------------------------------ exclude_from_nav

def test_search_finds_items_excluded_from_nav(api, sandbox):
    """exclude_from_nav only affects navigation, not search."""
    seed(api, sandbox)
    api.patch(f"{sandbox}/beta", json={"exclude_from_nav": True}, compare=False)
    assert ids(s(api, sandbox, getId="beta")) == ["beta"]
    [item] = s(api, sandbox, getId="beta", metadata_fields="exclude_from_nav").json["items"]
    assert item["exclude_from_nav"] is True


# ------------------------------------------------------------ permission filtering

def _perm_tree(api, sb):
    """
    sb (published)          local_editor: Editor (sandbox fixture)
      pub    published
      priv   private
      pend   pending
      shared private folder   subreader + group member: local Reader
        spriv  private
        spub   published
      blk    private folder, inheritance blocked
        bpriv  private
    """
    api.publish(sb)
    api.doc(sb, "pub")
    api.publish(f"{sb}/pub")
    api.doc(sb, "priv")
    api.doc(sb, "pend")
    api.transition(f"{sb}/pend", "submit")
    api.folder(sb, "shared")
    api.doc(f"{sb}/shared", "spriv")
    api.doc(f"{sb}/shared", "spub")
    api.publish(f"{sb}/shared/spub")
    api.folder(sb, "blk")
    api.doc(f"{sb}/blk", "bpriv")
    api.block_inheritance(f"{sb}/blk")


VIS = {
    "anonymous": ["SB", "pub", "spub"],
    "member": ["SB", "pub", "spub"],
    "local_editor": ["SB", "pub", "priv", "pend", "shared", "spriv", "spub"],
    "subreader": ["SB", "pub", "shared", "spriv", "spub"],
    "groupreader": ["SB", "pub", "shared", "spriv", "spub"],
    "site_admin": ["SB", "pub", "priv", "pend", "shared", "spriv", "spub", "blk", "bpriv"],
    "manager": ["SB", "pub", "priv", "pend", "shared", "spriv", "spub", "blk", "bpriv"],
}


@pytest.mark.parametrize("persona", list(VIS))
@pytest.mark.known_divergence(NICK, reason="search ACL omits local roles other than Reader (local Editor)",
                              ref="A2#2", ids=["local_editor"])
@pytest.mark.known_divergence(NICK, reason="the local Reader is granted after the content exists and "
                              "@sharing does not reindex the ACL", ref="A2#1", ids=["subreader", "groupreader"])
def test_search_permission_filtered(api, sandbox, persona):
    """Each persona finds exactly what it may View: workflow state, local roles on a
    subfolder (user and group), and blocked inheritance all apply."""
    _perm_tree(api, sandbox)
    who = persona
    if persona == "subreader":
        who = api.create_user()
        api.share(f"{sandbox}/shared", who, ["Reader"])
    elif persona == "groupreader":
        who = api.create_user()
        g = api.create_group(users=[who.id])
        api.share(f"{sandbox}/shared", g, ["Reader"], type_="group")
    exp = sorted(sb_id(sandbox) if e == "SB" else e for e in VIS[persona])
    assert ids(s(api, sandbox, as_=who)) == exp


@pytest.mark.parametrize("persona", list(VIS))
@pytest.mark.known_divergence(NICK, reason="search ACL omits local roles other than Reader (local Editor)",
                              ref="A2#2", ids=["local_editor"])
@pytest.mark.known_divergence(NICK, reason="the local Reader is granted after the content exists and "
                              "@sharing does not reindex the ACL", ref="A2#1", ids=["subreader", "groupreader"])
def test_search_permission_matches_traversal(api, sandbox, persona):
    """Whatever search returns can be opened, and whatever can be opened is found."""
    _perm_tree(api, sandbox)
    who = persona
    if persona in ("subreader", "groupreader"):
        who = api.create_user()
        if persona == "subreader":
            api.share(f"{sandbox}/shared", who, ["Reader"])
        else:
            g = api.create_group(users=[who.id])
            api.share(f"{sandbox}/shared", g, ["Reader"], type_="group")
    found = set(ids(s(api, sandbox, as_=who)))
    for rel in ("pub", "priv", "pend", "shared", "shared/spriv", "shared/spub", "blk", "blk/bpriv"):
        st = api.get(f"{sandbox}/{rel}", as_=who, compare=False).status
        assert (st == 200) == (rel.rsplit("/", 1)[-1] in found), (rel, st, sorted(found))


@pytest.mark.parametrize("persona", ["anonymous", "member", "site_admin", "manager"])
def test_search_items_total_respects_permissions(api, sandbox, persona):
    """items_total counts only visible items (no leak of hidden counts)."""
    _perm_tree(api, sandbox)
    r = s(api, sandbox, as_=persona, b_size=1)
    assert r.json["items_total"] == len(VIS[persona]), r.json


@pytest.mark.parametrize("persona,expected", [("anonymous", 401), ("member", 401), ("local_editor", 200)])
def test_search_on_private_context_requires_view(api, sandbox, persona, expected):
    """@search needs View on the context itself (the sandbox is private here)."""
    api.doc(sandbox, "d")
    r = s(api, sandbox, as_=persona)
    assert same_outcome(expected, r.status), r


def test_search_site_root_as_anonymous_hides_private(api, sandbox):
    """A site-wide search never returns this sandbox's private content to anonymous."""
    _perm_tree(api, sandbox)
    r = get_each(api, "@search", "anonymous", lambda side: {"path.query": phys(side, sandbox)})
    assert ids(r) == sorted([sb_id(sandbox), "pub", "spub"])


def test_search_global_reader_sees_private(api, sandbox):
    """A user with the global Reader role views (and finds) private content."""
    _perm_tree(api, sandbox)
    u = api.create_user(roles=("member", "Reader"))
    exp = sorted([sb_id(sandbox)] + ["pub", "priv", "pend", "shared", "spriv", "spub", "blk", "bpriv"])
    assert ids(s(api, sandbox, as_=u)) == exp  # blocking inheritance does not affect global roles


# ------------------------------------------------------------ index freshness

def test_search_after_rename(api, sandbox):
    seed(api, sandbox)
    r = api.patch(f"{sandbox}/beta", json={"id": "beta2"}, compare=False)
    assert r.status in (200, 204), r
    assert ids(s(api, sandbox, getId="beta2")) == ["beta2"]
    assert ids(s(api, sandbox, getId="beta")) == []
    [item] = s(api, sandbox, SearchableText="æøå").json["items"]
    assert item["@id"].endswith(f"{sandbox}/beta2")


def test_search_after_move(api, sandbox):
    seed(api, sandbox)
    r = api.post(f"{sandbox}/sub/@move", json={"source": f"/{sandbox}/beta"}, compare=False)
    assert r.status == 200, r
    assert ids(s(api, f"{sandbox}/sub", **{"path.depth": 1})) == ["beta", "delta", "gamma"]
    assert ids(s(api, sandbox, **{"path.depth": 1})) == ["alpha", "sub"]
    [item] = s(api, sandbox, getId="beta").json["items"]
    assert item["@id"].endswith(f"{sandbox}/sub/beta")


def test_search_after_folder_move_reindexes_descendants(api, sandbox):
    """Moving a folder updates the paths of everything inside it."""
    seed(api, sandbox)
    api.folder(sandbox, "target")
    r = api.post(f"{sandbox}/target/@move", json={"source": f"/{sandbox}/sub"}, compare=False)
    assert r.status == 200, r
    assert ids(s(api, f"{sandbox}/target")) == ["delta", "gamma", "sub", "target"]
    [item] = s(api, sandbox, getId="gamma").json["items"]
    assert item["@id"].endswith(f"{sandbox}/target/sub/gamma")
    r = get_each(api, f"{sandbox}/@search", "admin",
                 lambda side: {"path.query": phys(side, f"{sandbox}/target/sub"), "path.depth": 1})
    assert ids(r) == ["delta", "gamma"]


def test_search_after_folder_rename_reindexes_descendants(api, sandbox):
    seed(api, sandbox)
    r = api.patch(f"{sandbox}/sub", json={"id": "renamed"}, compare=False)
    assert r.status in (200, 204), r
    assert api.get(f"{sandbox}/renamed/gamma", compare=False).status == 200  # traversal follows the rename
    [item] = s(api, sandbox, getId="gamma").json["items"]
    assert item["@id"].endswith(f"{sandbox}/renamed/gamma")
    assert ids(s(api, f"{sandbox}/renamed", **{"path.depth": 1})) == ["delta", "gamma"]


def test_search_after_copy(api, sandbox):
    seed(api, sandbox)
    r = api.post(f"{sandbox}/sub/@copy", json={"source": f"/{sandbox}/alpha"}, compare=False)
    assert r.status == 200, r
    assert ids(s(api, sandbox, SearchableText="zebra")) == ["alpha", "alpha"]


def test_search_after_delete(api, sandbox):
    seed(api, sandbox)
    assert api.delete(f"{sandbox}/beta", compare=False).status == 204
    assert ids(s(api, sandbox, SearchableText="æøå")) == []
    assert ids(s(api, sandbox, getId="beta")) == []


def test_search_after_workflow_transition(api, sandbox):
    seed(api, sandbox)
    assert ids(s(api, sandbox, as_="anonymous", portal_type="{type:document}")) == ["alpha", "delta"]
    api.publish(f"{sandbox}/beta")
    assert ids(s(api, sandbox, review_state="published", portal_type="{type:document}")) == ["alpha", "beta", "delta"]
    assert ids(s(api, sandbox, as_="anonymous", portal_type="{type:document}")) == ["alpha", "beta", "delta"]
    api.transition(f"{sandbox}/alpha", "retract")
    assert ids(s(api, sandbox, as_="anonymous", portal_type="{type:document}")) == ["beta", "delta"]


@pytest.mark.known_divergence(NICK, reason="@sharing POST does not reindex the ACL of descendants", ref="A2#1")
def test_search_after_sharing_grant_on_folder_reaches_descendants(api, sandbox):
    """Granting Reader on a folder makes its existing private children findable."""
    seed(api, sandbox)
    u = api.create_user()
    assert ids(s(api, f"{sandbox}/sub", as_=u)) == ["delta", "sub"]
    api.share(f"{sandbox}/sub", u, ["Reader"])
    assert api.get(f"{sandbox}/sub/gamma", as_=u, compare=False).status == 200
    assert ids(s(api, f"{sandbox}/sub", as_=u)) == ["delta", "gamma", "sub"]


@pytest.mark.known_divergence(NICK, reason="@sharing POST does not reindex the ACL (fails open)", ref="A2#1")
def test_search_after_sharing_revoke_on_folder(api, sandbox):
    seed(api, sandbox)
    u = api.create_user()
    api.share(f"{sandbox}/sub", u, ["Reader"])
    assert ids(s(api, f"{sandbox}/sub", as_=u)) == ["delta", "gamma", "sub"]
    api.share(f"{sandbox}/sub", u, {"Reader": False})
    assert api.get(f"{sandbox}/sub/gamma", as_=u, compare=False).status in DENIED
    assert ids(s(api, f"{sandbox}/sub", as_=u)) == ["delta", "sub"]


def test_search_after_blocking_inheritance_hides_from_local_editor(api, sandbox):
    """local_editor's Editor role comes from the sandbox; blocking it on sub hides sub's private items."""
    seed(api, sandbox)
    before = ids(s(api, f"{sandbox}/sub", as_="local_editor"))
    api.block_inheritance(f"{sandbox}/sub")
    assert api.get(f"{sandbox}/sub/gamma", as_="local_editor", compare=False).status in DENIED
    after = ids(s(api, f"{sandbox}/sub", as_="local_editor"))
    assert "gamma" not in after, (before, after)
    assert "delta" in after and "sub" in after


def test_search_after_group_membership_change(api, sandbox):
    """Group membership is resolved at query time: adding a user to a group with a
    local role makes the content findable without any reindex."""
    api.publish(sandbox)
    api.folder(sandbox, "sub")
    api.publish(f"{sandbox}/sub")
    u = api.create_user()
    g = api.create_group()
    api.share(f"{sandbox}/sub", g, ["Reader"], type_="group")
    api.doc(f"{sandbox}/sub", "late")  # indexed after the grant: no stale-ACL question
    assert ids(s(api, f"{sandbox}/sub", as_=u)) == ["sub"]
    api.add_to_group(g, u.id)
    assert api.get(f"{sandbox}/sub/late", as_=u, compare=False).status == 200
    assert ids(s(api, f"{sandbox}/sub", as_=u)) == ["late", "sub"]
