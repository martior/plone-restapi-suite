"""Content CRUD: GET/POST/PATCH/DELETE on content, ids/titles, error cases,
paths, expand/fullobjects/batching, ordering, @copy/@move, blocks.

Expectations follow plone.restapi docs (docs/source/endpoints/content.md,
docs/source/usage/serialization.md, .../endpoints/expansion.md, batching.md)
and the services in src/plone/restapi/services/content/*.py; where unclear the
Plone 6.2 reference decides (see comments).
"""

from __future__ import annotations

import pytest

from restapi_suite.api import uid
from restapi_suite.assertions import err_message, item_ids

pytestmark = pytest.mark.area("content")


# --------------------------------------------------------------------- GET

def test_get_document_shape(api, sandbox):
    api.doc(sandbox, "d1", title="Doc one", description="Desc one")
    r = api.get(f"{sandbox}/d1")
    assert r.status == 200, r
    j = r.json
    for key in ("@id", "@type", "id", "title", "description", "UID", "review_state",
                "parent", "created", "modified", "is_folderish", "@components", "layout"):
        assert key in j, f"missing {key}"
    assert j["@id"] == f"{api.t.base}/{sandbox}/d1"
    assert j["@type"] == api.type("document")
    assert (j["id"], j["title"], j["description"]) == ("d1", "Doc one", "Desc one")
    assert j["review_state"] == "private"
    assert j["parent"]["@id"] == f"{api.t.base}/{sandbox}"
    assert r.headers["content-type"].startswith("application/json")


def test_get_folder_items_shape_and_order(api, sandbox):
    for i in (3, 1, 2):
        api.doc(sandbox, f"c{i}", title=f"Child {i}")
    r = api.get(sandbox)
    assert r.status == 200, r
    j = r.json
    assert j["is_folderish"] is True
    assert j["items_total"] == 3
    assert item_ids(j) == ["c3", "c1", "c2"], "items keep insertion order"
    item = j["items"][0]
    for key in ("@id", "@type", "title", "description", "review_state"):
        assert key in item, f"summary lacks {key}"


def test_get_nonexistent_is_404(api, sandbox):
    r = api.get(f"{sandbox}/does-not-exist")
    assert r.status == 404, r


def test_404_error_body_shape(api, sandbox):
    """plone.restapi errors are {"type": ..., "message": ...} (docs/source/usage/exceptions.md)."""
    r = api.get(f"{sandbox}/does-not-exist")
    assert r.status == 404
    assert r.is_json, r
    assert r.json.get("type") == "NotFound", r


def test_get_below_nonexistent_parent_is_404(api, sandbox):
    assert api.get(f"{sandbox}/nope/deeper/still").status == 404


def test_get_trailing_slash(api, sandbox):
    api.doc(sandbox, "d1")
    r = api.get(f"{sandbox}/d1/")
    assert r.status == 200, r
    assert r.json["@id"] == f"{api.t.base}/{sandbox}/d1"


def test_get_double_slash_in_path(api, sandbox):
    api.doc(sandbox, "d1")
    r = api.get(f"{sandbox}//d1")
    # Plone 6.2: Zope collapses empty path segments.
    assert r.status == 200, r


def test_get_plusplus_api_without_accept(api, sandbox):
    """++api++ traversal returns JSON without an Accept header (Volto's seamless mode)."""
    api.doc(sandbox, "d1")
    r = api.get(f"++api++/{sandbox}/d1", accept=None)
    assert r.status == 200, r
    assert r.is_json and r.json["id"] == "d1"


@pytest.mark.known_divergence("nick", kind="deliberate", reason="Nick is headless and always answers JSON")
def test_get_without_accept_header_is_not_json(api, sandbox):
    """Plone serves its HTML view when the client does not ask for JSON."""
    api.doc(sandbox, "d1")
    r = api.get(f"{sandbox}/d1", accept=None, compare=False)
    assert r.status == 200
    assert not r.is_json, r


def test_get_percent_encoded_path(api, sandbox):
    api.doc(sandbox, "a-b")
    r = api.get(f"{sandbox}/a%2Db")
    assert r.status == 200, r
    assert r.json["id"] == "a-b"


def test_get_encoded_slash_is_not_a_separator(api, sandbox):
    api.folder(sandbox, "f")
    api.doc(f"{sandbox}/f", "d")
    r = api.get(f"{sandbox}/f%2Fd")
    # Plone 6.2 (waitress/Zope) decodes %2F before traversal, so this finds f/d.
    assert r.status == 200, r


def test_get_dotdot_segment(api, sandbox):
    api.folder(sandbox, "f")
    api.doc(sandbox, "d")
    r = api.get(f"{sandbox}/f/%2E%2E/d")
    assert r.status in (200, 404), r  # recorded; see report for the exact value per target


def test_get_private_as_anonymous_is_401(api, sandbox):
    api.doc(sandbox, "d1")
    assert api.get(f"{sandbox}/d1", as_="anonymous").status == 401


def test_get_expand_breadcrumbs(api, sandbox):
    api.folder(sandbox, "f", title="Eff")
    api.doc(f"{sandbox}/f", "d", title="Dee")
    r = api.get(f"{sandbox}/f/d", params={"expand": "breadcrumbs"})
    assert r.status == 200, r
    bc = r.json["@components"]["breadcrumbs"]
    assert [i["title"] for i in bc["items"]][-2:] == ["Eff", "Dee"], bc


def test_get_expand_multiple(api, sandbox):
    api.doc(sandbox, "d")
    r = api.get(f"{sandbox}/d", params={"expand": "breadcrumbs,workflow,types"})
    assert r.status == 200, r
    comp = r.json["@components"]
    assert "items" in comp["breadcrumbs"]
    assert "transitions" in comp["workflow"]
    assert isinstance(comp["types"], list)


def test_get_expand_unknown_component_is_ignored(api, sandbox):
    api.doc(sandbox, "d")
    r = api.get(f"{sandbox}/d", params={"expand": "no-such-component"})
    assert r.status == 200, r


def test_get_fullobjects(api, sandbox):
    api.doc(sandbox, "d", description="full")
    r = api.get(sandbox, params={"fullobjects": "1"})
    assert r.status == 200, r
    item = r.json["items"][0]
    for key in ("UID", "created", "modified", "parent", "description"):
        assert key in item, f"fullobjects item lacks {key}"


def test_get_metadata_fields(api, sandbox):
    api.doc(sandbox, "d")
    r = api.get(sandbox, params={"metadata_fields": "UID"})
    assert r.status == 200, r
    assert "UID" in r.json["items"][0], r.json["items"][0]


def test_get_batching(api, sandbox):
    for i in range(5):
        api.doc(sandbox, f"c{i}")
    r = api.get(sandbox, params={"b_size": "2"})
    assert r.status == 200, r
    j = r.json
    assert j["items_total"] == 5
    assert len(j["items"]) == 2
    assert "batching" in j and "next" in j["batching"], j.get("batching")
    r2 = api.get(sandbox, params={"b_size": "2", "b_start": "4"})
    assert item_ids(r2.json) == ["c4"]


# --------------------------------------------------------------------- POST

def test_post_creates_201_with_location(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "new", "title": "New"})
    assert r.status == 201, r
    assert r.json["@id"] == f"{api.t.base}/{sandbox}/new"
    assert r.headers.get("location") == r.json["@id"]
    assert r.json["title"] == "New" and r.json["@type"] == api.type("document")
    assert api.get(f"{sandbox}/new").status == 200


def test_post_id_from_title(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "title": "Hello World Again"})
    assert r.status == 201, r
    assert r.json["id"] == "hello-world-again"


def test_post_id_from_unicode_title(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "title": "Ærlig Øl på Åsen – ünïcødé"})
    assert r.status == 201, r
    # Plone 6.2: plone.i18n normalizer transliterates; the en dash is dropped.
    assert r.json["id"] == "aerlig-ol-pa-asen-unicode", r.json["id"]


def test_post_same_title_twice_gets_unique_id(api, sandbox):
    a = api.post(sandbox, json={"@type": "{type:document}", "title": "Twin"})
    b = api.post(sandbox, json={"@type": "{type:document}", "title": "Twin"})
    assert (a.status, b.status) == (201, 201)
    assert a.json["id"] == "twin"
    assert b.json["id"] == "twin-1", b.json["id"]


def test_post_duplicate_explicit_id_is_rejected(api, sandbox):
    api.doc(sandbox, "dup")
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "dup", "title": "Again"})
    # Plone 6.2: explicit ids are not renamed; the add fails with 400.
    assert r.status == 400, r


def test_post_missing_type_is_400(api, sandbox):
    r = api.post(sandbox, json={"title": "No type"})
    assert r.status == 400, r
    assert "@type" in err_message(r), r


def test_post_unknown_type_is_403(api, sandbox):
    """Plone 6.2 answers 403 Forbidden "Invalid '@type' parameter" (services/content/utils.py create())."""
    r = api.post(sandbox, json={"@type": "NoSuchType", "title": "x"})
    assert r.status == 403, r
    assert "NoSuchType" in err_message(r), r


@pytest.mark.plone_bug("malformed JSON body on POST is a 500 (json_body raises DeserializationError outside the try)")
@pytest.mark.known_divergence("nick", kind="plone-bug", reason="Nick answers 400 (but with an HTML body, see next test)")
def test_post_invalid_json_is_500(api, sandbox):
    r = api.post(sandbox, content="{not json", headers={"Content-Type": "application/json"})
    assert r.status == 500, r


def test_post_invalid_json_error_is_json(api, sandbox):
    """Whatever the status, a JSON API must answer errors with a JSON body."""
    r = api.post(sandbox, content="{not json", headers={"Content-Type": "application/json"})
    assert r.status >= 400 and r.is_json and r.json is not None, r


@pytest.mark.plone_bug("a JSON array body on POST is a 500 (AttributeError: list has no .get)")
@pytest.mark.known_divergence("nick", kind="plone-bug", reason="Nick answers 400 (no @type)")
def test_post_json_array_body_is_500(api, sandbox):
    r = api.post(sandbox, json=[{"@type": "{type:document}", "title": "x"}])
    assert r.status == 500, r


def test_post_unknown_field_is_ignored(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "u", "title": "U", "no_such_field": 42})
    assert r.status == 201, r
    assert "no_such_field" not in api.get(f"{sandbox}/u").json


def test_post_title_wrong_type_is_400(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "w", "title": 12345})
    assert r.status == 400, r


def test_post_description_wrong_type_is_400(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "w", "title": "W", "description": ["a", "b"]})
    assert r.status == 400, r


def test_post_missing_title_is_400(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "nt"})
    assert r.status == 400, r


def test_post_title_with_newline_is_400(api, sandbox):
    """title is a TextLine: newlines violate its constraint."""
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "nl", "title": "line1\nline2"})
    assert r.status == 400, r


TITLE_MAX = 1024  # Plone 6.2 IBasic title max_length (measured: 1024 ok, 1025 rejected)


def test_post_title_at_max_length(api, sandbox):
    title = ("Lorem ipsum dolor sit amet " * 40)[:TITLE_MAX]
    r = api.post(sandbox, json={"@type": "{type:document}", "title": title})
    assert r.status == 201, r
    assert api.get(r.path).json["title"] == title
    # Plone 6.2 caps generated ids well below the title length (measured 254 chars from a 270-char title).
    assert len(r.json["id"]) <= 255, len(r.json["id"])


def test_post_title_over_max_length_is_400(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "long", "title": "x" * (TITLE_MAX + 1)})
    assert r.status == 400, r
    assert "too long" in err_message(r).lower(), r


def test_post_title_300_chars(api, sandbox):
    """Between Nick's apparent 255 limit and Plone's 1024."""
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "t300", "title": "y" * 300})
    assert r.status == 201, r


import unicodedata  # noqa: E402

UNICODE_TITLES = [
    "😀 emoji 🇳🇴 flags",
    "مرحبا بالعالم",  # RTL
    "é combining acute",
    "日本語のタイトル",
    "<script>alert(1)</script> & \"quotes\" 'single'",
]


@pytest.mark.parametrize("title", UNICODE_TITLES, ids=["emoji", "rtl", "combining", "cjk", "html"])
def test_post_unicode_title_roundtrip(api, sandbox, title):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": f"u{uid(4)}", "title": title})
    assert r.status == 201, r
    # Plone 6.2 NFC-normalises text ("e" + U+0301 comes back as U+00E9).
    assert api.get(r.path).json["title"] == unicodedata.normalize("NFC", title)


def test_post_into_nonexistent_parent_is_404(api, sandbox):
    r = api.post(f"{sandbox}/nope", json={"@type": "{type:document}", "title": "x"})
    assert r.status == 404, r


@pytest.mark.known_divergence("nick", kind="deliberate", reason="Nick's Page is folderish (Volto-style)")
def test_post_into_document(api, sandbox):
    """Plone 6.2 classic Documents are not folderish."""
    api.doc(sandbox, "d")
    r = api.post(f"{sandbox}/d", json={"@type": "{type:document}", "id": "child", "title": "x"})
    assert r.status in (400, 403, 404, 405), r


@pytest.mark.parametrize("bad_id", ["slash/inside", "_underscore", "..", "aq_parent"],
                         ids=["slash", "underscore", "dotdot", "aq-name"])
def test_post_invalid_explicit_id_is_400(api, sandbox, bad_id):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": bad_id, "title": "x"})
    assert r.status == 400, r


@pytest.mark.parametrize("odd_id", ["has space", "a" * 300, "UPPER.case~ok", "index_html"],
                         ids=["space", "300chars", "dots-tilde", "index-html"])
def test_post_unusual_explicit_id_is_kept(api, sandbox, odd_id):
    """Plone 6.2 keeps explicit ids verbatim if Zope's checkValidId allows them."""
    r = api.post(sandbox, json={"@type": "{type:document}", "id": odd_id, "title": "x"})
    assert r.status == 201, r
    assert r.json["id"] == odd_id


@pytest.mark.plone_bug("ids starting with '@' are accepted and the child then shadows the @-service of the same name")
@pytest.mark.known_divergence("nick", kind="plone-bug", reason="the @-service wins over a child with the same id")
def test_post_child_named_like_service_shadows_it(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "@search", "title": "Shadow"})
    assert r.status == 201, r
    s = api.get(f"{sandbox}/@search")
    assert s.status == 200 and s.json.get("title") == "Shadow", s


def test_post_uid_as_admin_is_honoured(api, sandbox):
    want = uid(32).ljust(32, "0")
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "withuid", "title": "x", "UID": want})
    assert r.status == 201, r
    assert r.json["UID"].replace("-", "") == want  # Nick formats uuids with dashes


def test_post_uid_requires_manage_portal(api, sandbox):
    u = api.create_user()
    api.share(sandbox, u, ["Contributor"])
    r = api.post(sandbox, as_=u, json={"@type": "{type:document}", "id": "withuid", "title": "x",
                                       "UID": "0123456789abcdef0123456789abcdef"})
    assert r.status == 403, r


def test_post_initial_review_state_is_private(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "p", "title": "x"})
    assert r.json["review_state"] == "private"


@pytest.mark.known_divergence("nick", kind="deliberate", reason="Nick's Page has blocks, no rich text field")
def test_post_with_text_field(api, sandbox):
    text = {"data": "<p>Hello <b>world</b></p>", "content-type": "text/html", "encoding": "utf-8"}
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "t", "title": "x", "text": text})
    assert r.status == 201, r
    got = api.get(f"{sandbox}/t").json.get("text")
    assert got and "Hello" in got["data"] and got["content-type"] == "text/html", got


# --------------------------------------------------------------------- PATCH

def test_patch_returns_204_and_persists(api, sandbox):
    api.doc(sandbox, "d", title="Before")
    r = api.patch(f"{sandbox}/d", json={"title": "After", "description": "D"})
    assert r.status == 204, r
    assert r.content == b""
    j = api.get(f"{sandbox}/d").json
    assert (j["title"], j["description"]) == ("After", "D")


def test_patch_prefer_return_representation(api, sandbox):
    api.doc(sandbox, "d", title="Before")
    r = api.patch(f"{sandbox}/d", json={"title": "After"}, headers={"Prefer": "return=representation"})
    assert r.status == 200, r
    assert r.json["title"] == "After"


def test_patch_unknown_field_is_ignored(api, sandbox):
    api.doc(sandbox, "d")
    r = api.patch(f"{sandbox}/d", json={"no_such_field": "x"})
    assert r.status == 204, r
    assert "no_such_field" not in api.get(f"{sandbox}/d").json


def test_patch_wrong_type_is_400(api, sandbox):
    api.doc(sandbox, "d", title="Keep")
    r = api.patch(f"{sandbox}/d", json={"title": {"not": "a string"}})
    assert r.status == 400, r
    assert api.get(f"{sandbox}/d").json["title"] == "Keep"


def test_patch_title_null_is_400(api, sandbox):
    api.doc(sandbox, "d", title="Keep")
    r = api.patch(f"{sandbox}/d", json={"title": None})
    assert r.status == 400, r
    assert api.get(f"{sandbox}/d").json["title"] == "Keep"


def test_patch_invalid_json_is_400(api, sandbox):
    api.doc(sandbox, "d")
    r = api.patch(f"{sandbox}/d", content="{broken", headers={"Content-Type": "application/json"})
    assert r.status == 400, r


def test_patch_empty_object_is_204(api, sandbox):
    api.doc(sandbox, "d")
    assert api.patch(f"{sandbox}/d", json={}).status == 204


def test_patch_nonexistent_is_404(api, sandbox):
    assert api.patch(f"{sandbox}/nope", json={"title": "x"}).status == 404


def test_patch_id_renames_and_redirects(api, sandbox):
    """Plone 6.2: PATCH {"id": ...} renames; the old URL redirects (plone.app.redirector)."""
    a = api.create(sandbox, "document", id="d")
    r = api.patch(f"{sandbox}/d", json={"id": "renamed"})
    assert r.status == 204, r
    new = api.get(f"{sandbox}/renamed")
    assert new.status == 200 and new.json["UID"] == a.json["UID"]
    old = api.get(f"{sandbox}/d")
    assert old.status in (301, 302), old
    assert old.headers["location"].startswith(f"{api.t.base}/{sandbox}/renamed"), old.headers.get("location")


def test_patch_id_to_existing_sibling_gets_unique_id(api, sandbox):
    """Plone 6.2 (and Nick) rename b -> a-1 instead of rejecting the clash."""
    api.doc(sandbox, "a")
    api.doc(sandbox, "b", title="B")
    r = api.patch(f"{sandbox}/b", json={"id": "a"})
    assert r.status == 204, r
    assert api.get(f"{sandbox}/a").json["title"] == "a"
    assert api.get(f"{sandbox}/a-1").json["title"] == "B"


def test_patch_updates_modified(api, sandbox):
    api.doc(sandbox, "d")
    before = api.get(f"{sandbox}/d").json["modified"]
    import time
    time.sleep(1.1)
    api.patch(f"{sandbox}/d", json={"title": "changed"})
    after = api.get(f"{sandbox}/d").json["modified"]
    assert after > before, (before, after)


def test_patch_if_match_mismatch(api, sandbox):
    """plone.restapi has no optimistic concurrency: If-Match is ignored by Plone 6.2."""
    api.doc(sandbox, "d")
    r = api.patch(f"{sandbox}/d", json={"title": "x"}, headers={"If-Match": '"definitely-stale"'})
    assert r.status == 204, r


@pytest.mark.known_divergence("nick", kind="deliberate", reason="Express sends a weak ETag on every JSON response")
def test_get_etag_header(api, sandbox):
    """Recorded only: Plone 6.2 without plone.app.caching sends no ETag."""
    api.doc(sandbox, "d")
    r = api.get(f"{sandbox}/d")
    assert r.status == 200
    assert "etag" not in r.headers, r.headers.get("etag")


# --------------------------------------------------------------------- DELETE

def test_delete_204_then_404(api, sandbox):
    api.doc(sandbox, "d")
    r = api.delete(f"{sandbox}/d")
    assert r.status == 204, r
    assert api.get(f"{sandbox}/d").status == 404


def test_delete_folder_removes_subtree(api, sandbox):
    api.folder(sandbox, "f")
    api.doc(f"{sandbox}/f", "d")
    assert api.delete(f"{sandbox}/f").status == 204
    assert api.get(f"{sandbox}/f/d").status == 404


def test_delete_nonexistent_is_404(api, sandbox):
    assert api.delete(f"{sandbox}/nope").status == 404


def test_delete_twice(api, sandbox):
    api.doc(sandbox, "d")
    api.delete(f"{sandbox}/d")
    assert api.delete(f"{sandbox}/d").status == 404


def test_delete_updates_parent_items(api, sandbox):
    for i in range(3):
        api.doc(sandbox, f"c{i}")
    api.delete(f"{sandbox}/c1")
    j = api.get(sandbox).json
    assert item_ids(j) == ["c0", "c2"]
    assert j["items_total"] == 2


# --------------------------------------------------------------------- ordering

def _ids(api, path):
    return item_ids(api.get(path, compare=False).json)


@pytest.fixture
def abcd(api, sandbox):
    for i in "abcd":
        api.doc(sandbox, i)
    return sandbox


def test_order_top(api, abcd):
    r = api.patch(abcd, json={"ordering": {"obj_id": "c", "delta": "top"}})
    assert r.status == 204, r
    assert _ids(api, abcd) == ["c", "a", "b", "d"]


def test_order_bottom(api, abcd):
    assert api.patch(abcd, json={"ordering": {"obj_id": "a", "delta": "bottom"}}).status == 204
    assert _ids(api, abcd) == ["b", "c", "d", "a"]


def test_order_positive_delta(api, abcd):
    assert api.patch(abcd, json={"ordering": {"obj_id": "a", "delta": 2}}).status == 204
    assert _ids(api, abcd) == ["b", "c", "a", "d"]


def test_order_negative_delta(api, abcd):
    assert api.patch(abcd, json={"ordering": {"obj_id": "d", "delta": -1}}).status == 204
    assert _ids(api, abcd) == ["a", "b", "d", "c"]


def test_order_delta_beyond_end_clamps(api, abcd):
    assert api.patch(abcd, json={"ordering": {"obj_id": "b", "delta": 100}}).status == 204
    assert _ids(api, abcd) == ["a", "c", "d", "b"]


def test_order_subset_ids(api, abcd):
    r = api.patch(abcd, json={"ordering": {"obj_id": "d", "delta": "top", "subset_ids": ["c", "d"]}})
    assert r.status == 204, r
    assert _ids(api, abcd) == ["a", "b", "d", "c"]


def test_order_subset_ids_mismatch_is_400(api, abcd):
    r = api.patch(abcd, json={"ordering": {"obj_id": "a", "delta": 1, "subset_ids": ["d", "a"]}})
    assert r.status == 400, r
    assert _ids(api, abcd) == list("abcd")


@pytest.mark.plone_bug("an unknown ordering obj_id is silently ignored (204, nothing moves)")
def test_order_unknown_obj_id_is_ignored(api, abcd):
    r = api.patch(abcd, json={"ordering": {"obj_id": "zzz", "delta": 1}})
    assert r.status == 204, r
    assert _ids(api, abcd) == list("abcd")


@pytest.mark.plone_bug("a non-integer ordering delta is a 500 (int('sideways') ValueError)")
@pytest.mark.known_divergence("nick", kind="plone-bug", reason="Nick silently ignores the invalid delta (204); "
                              "400 would be right for both")
def test_order_invalid_delta_is_500(api, abcd):
    r = api.patch(abcd, json={"ordering": {"obj_id": "a", "delta": "sideways"}})
    assert r.status == 500, r
    assert _ids(api, abcd) == list("abcd")


def test_order_with_other_fields(api, abcd):
    """Plone applies ordering and the field update in one PATCH."""
    r = api.patch(abcd, json={"ordering": {"obj_id": "d", "delta": "top"}, "title": "Renamed folder"})
    assert r.status == 204, r
    j = api.get(abcd).json
    assert item_ids(j)[0] == "d"
    assert j["title"] == "Renamed folder"


def test_order_persists_on_new_child(api, abcd):
    api.patch(abcd, json={"ordering": {"obj_id": "d", "delta": "top"}})
    api.doc(abcd, "e")
    assert _ids(api, abcd) == ["d", "a", "b", "c", "e"]


def test_resort_by_title_descending(api, sandbox):
    """http-examples/content_resort: PATCH {"sort": {"on": "sortable_title", "order": "descending"}}."""
    for i, t in zip("abc", ["Beta", "Alpha", "Gamma"]):
        api.doc(sandbox, i, title=t)
    r = api.patch(sandbox, json={"sort": {"on": "sortable_title", "order": "descending"}})
    assert r.status == 204, r
    assert _ids(api, sandbox) == ["c", "a", "b"]


# --------------------------------------------------------------------- @copy / @move

def test_copy_into_other_folder(api, sandbox):
    api.doc(sandbox, "src", title="Source")
    api.folder(sandbox, "dest")
    r = api.post(f"{sandbox}/dest/@copy", json={"source": f"{{base}}/{sandbox}/src"})
    assert r.status == 200, r
    assert isinstance(r.json, list) and r.json[0]["target"] == f"{api.t.base}/{sandbox}/dest/src", r
    assert api.get(f"{sandbox}/dest/src").json["title"] == "Source"
    assert api.get(f"{sandbox}/src").status == 200


def test_copy_into_same_folder_renames(api, sandbox):
    api.doc(sandbox, "src")
    r = api.post(f"{sandbox}/@copy", json={"source": f"{{base}}/{sandbox}/src"})
    assert r.status == 200, r
    assert r.json[0]["target"] == f"{api.t.base}/{sandbox}/copy_of_src", r


def test_copy_new_uid(api, sandbox):
    a = api.create(sandbox, "document", id="src")
    api.folder(sandbox, "dest")
    api.post(f"{sandbox}/dest/@copy", json={"source": f"{{base}}/{sandbox}/src"})
    assert api.get(f"{sandbox}/dest/src").json["UID"] != a.json["UID"]


def test_move_into_other_folder(api, sandbox):
    a = api.create(sandbox, "document", id="src")
    api.folder(sandbox, "dest")
    r = api.post(f"{sandbox}/dest/@move", json={"source": f"{{base}}/{sandbox}/src"})
    assert r.status == 200, r
    old = api.get(f"{sandbox}/src")
    # Plone 6.2: the old URL redirects to the new location (plone.app.redirector).
    assert old.status in (301, 302) and old.headers["location"].startswith(f"{api.t.base}/{sandbox}/dest/src"), old
    moved = api.get(f"{sandbox}/dest/src")
    assert moved.status == 200 and moved.json["UID"] == a.json["UID"]


def test_move_multiple_sources(api, sandbox):
    api.doc(sandbox, "a")
    api.doc(sandbox, "b")
    api.folder(sandbox, "dest")
    r = api.post(f"{sandbox}/dest/@move", json={"source": [f"{{base}}/{sandbox}/a", f"{{base}}/{sandbox}/b"]})
    assert r.status == 200, r
    assert sorted(item_ids(api.get(f"{sandbox}/dest").json)) == ["a", "b"]


def test_move_by_uid(api, sandbox):
    """plone.restapi resolves sources given as URL, physical path or UID (services/copymove/copymove.py)."""
    a = api.create(sandbox, "document", id="src")
    api.folder(sandbox, "dest")
    for side in api.sides:  # each side has its own UID for "src"
        uid_ = side.request("GET", f"{sandbox}/src", side.admin).json["UID"]
        r = side.request("POST", f"{sandbox}/dest/@move", side.admin, json={"source": uid_})
        if side is api.t:
            assert r.status == 200, r
    assert api.get(f"{sandbox}/dest/src").json["UID"] == a.json["UID"]


def test_move_by_site_relative_path(api, sandbox):
    """A path starting with "/" is resolved against the site root (copymove.py get_object)."""
    api.doc(sandbox, "src")
    api.folder(sandbox, "dest")
    r = api.post(f"{sandbox}/dest/@move", json={"source": f"/{sandbox}/src"})
    assert r.status == 200, r
    assert api.get(f"{sandbox}/dest/src", compare=False).status == 200


def test_move_nonexistent_source(api, sandbox):
    api.folder(sandbox, "dest")
    r = api.post(f"{sandbox}/dest/@move", json={"source": f"{{base}}/{sandbox}/nope"})
    # Plone 6.2 skips unresolvable sources and returns an empty list.
    assert r.status == 200 and r.json == [], r


def test_move_folder_into_itself_is_rejected(api, sandbox):
    api.folder(sandbox, "f")
    r = api.post(f"{sandbox}/f/@move", json={"source": f"{{base}}/{sandbox}/f"})
    assert r.status in (400, 403, 500), r
    assert api.get(f"{sandbox}/f").status == 200


# --------------------------------------------------------------------- blocks

BLOCKS = {
    "8a1f0b2e-0000-4000-8000-000000000001": {"@type": "title"},
    "8a1f0b2e-0000-4000-8000-000000000002": {
        "@type": "slate",
        "value": [{"type": "p", "children": [{"text": "Hello "}, {"type": "strong", "children": [{"text": "blocks"}]}]}],
        "plaintext": "Hello blocks",
    },
    "8a1f0b2e-0000-4000-8000-000000000003": {"@type": "custom", "nested": {"list": [1, 2.5, True, None, "x"]}},
}
LAYOUT = {"items": list(BLOCKS)}


@pytest.mark.requires("blocks")
def test_blocks_roundtrip(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "b", "title": "B", "blocks": BLOCKS,
                                "blocks_layout": LAYOUT})
    assert r.status == 201, r
    j = api.get(f"{sandbox}/b").json
    assert j["blocks_layout"] == LAYOUT
    assert j["blocks"] == BLOCKS


@pytest.mark.requires("blocks")
def test_blocks_patch_replaces(api, sandbox):
    api.post(sandbox, json={"@type": "{type:document}", "id": "b", "title": "B", "blocks": BLOCKS,
                            "blocks_layout": LAYOUT})
    one = {"k1": {"@type": "title"}}
    assert api.patch(f"{sandbox}/b", json={"blocks": one, "blocks_layout": {"items": ["k1"]}}).status == 204
    j = api.get(f"{sandbox}/b").json
    assert j["blocks"] == one and j["blocks_layout"] == {"items": ["k1"]}


@pytest.mark.requires("blocks")
def test_blocks_wrong_type_is_400(api, sandbox):
    r = api.post(sandbox, json={"@type": "{type:document}", "id": "b", "title": "B", "blocks": "not-a-dict"})
    assert r.status == 400, r


# --------------------------------------------------------------------- concurrency

def test_concurrent_creates_in_one_folder(api, sandbox):
    """8 threads x 3 POSTs into one folder: all must succeed with distinct ids.
    (Plone retries ZODB conflicts; the suite client retries the remaining
    ConflictErrors, see client.py. Nothing is retried for other backends.)"""
    from concurrent.futures import ThreadPoolExecutor
    from restapi_suite.client import Side

    token = api.t.token(api.t.admin)

    def worker(n):
        side = Side(api.t.target)  # own connection per thread, shared token
        side._tokens[side.admin.id] = token
        out = []
        for i in range(3):
            r = side.request("POST", sandbox, side.admin,
                             json={"@type": "{type:document}", "title": f"Concurrent {n}-{i}"})
            out.append((r.status, r.get("id"), r.text[:120] if r.status != 201 else ""))
        side.close()
        return out

    with ThreadPoolExecutor(8) as ex:
        results = [x for xs in ex.map(worker, range(8)) for x in xs]
    bad = [r for r in results if r[0] != 201]
    assert not bad, bad
    ids = [r[1] for r in results]
    assert len(set(ids)) == 24, ids
    assert len(item_ids(api.get(sandbox, compare=False, params={"b_size": "100"}).json)) == 24


def test_concurrent_patch_last_writer_wins(api, sandbox):
    from concurrent.futures import ThreadPoolExecutor
    from restapi_suite.client import Side

    api.doc(sandbox, "d")
    token = api.t.token(api.t.admin)

    def worker(n):
        side = Side(api.t.target)
        side._tokens[side.admin.id] = token
        r = side.request("PATCH", f"{sandbox}/d", side.admin, json={"title": f"writer {n}"})
        side.close()
        return r.status

    with ThreadPoolExecutor(6) as ex:
        statuses = list(ex.map(worker, range(6)))
    assert statuses == [204] * 6, statuses
    assert api.get(f"{sandbox}/d", compare=False).json["title"].startswith("writer ")


def test_concurrent_deletes_in_one_folder(api, sandbox):
    """8 siblings deleted in parallel: all 204, folder empty afterwards.
    (Nick re-numbers position_in_parent of all siblings on every delete.)"""
    from concurrent.futures import ThreadPoolExecutor
    from restapi_suite.client import Side

    for i in range(8):
        api.doc(sandbox, f"c{i}")
    token = api.t.token(api.t.admin)

    def worker(n):
        side = Side(api.t.target)
        side._tokens[side.admin.id] = token
        r = side.request("DELETE", f"{sandbox}/c{n}", side.admin)
        side.close()
        return r.status

    with ThreadPoolExecutor(8) as ex:
        statuses = list(ex.map(worker, range(8)))
    assert statuses == [204] * 8, statuses
    assert item_ids(api.get(sandbox, compare=False).json) == []
