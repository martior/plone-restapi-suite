"""@types: list, single-type JSON schema, field lookup, addability per persona.

Sources: docs/source/endpoints/types.md, services/types/get.py
(check_security: anonymous may only see the schema of the context's own
type; addable = type in allowedContentTypes() and context folderish).
Schema mutation (POST/PATCH/PUT @types/<type>) changes global state and is
only probed for refusal here.
"""

from __future__ import annotations

import pytest

from restapi_suite.plugin import by_persona

pytestmark = pytest.mark.area("types")


def by_id(items):
    return {i["@id"].rsplit("/", 1)[-1]: i for i in items}


def test_types_list_shape(api, sandbox):
    r = api.get(f"{sandbox}/@types")
    assert r.status == 200, r
    assert isinstance(r.json, list) and r.json, r
    for item in r.json:
        for key in ("@id", "title", "addable", "immediately_addable"):
            assert key in item, f"{key} missing in {item}"
        assert item["@id"].startswith(f"{api.t.base}/@types/"), item["@id"]
    ids = by_id(r.json)
    assert api.type("document") in ids and api.type("folder") in ids


def test_types_list_item_has_id(api, sandbox):
    """plone.restapi 10 adds "id" to each item (services/types/get.py)."""
    r = api.get(f"{sandbox}/@types")
    assert all("id" in i for i in r.json), r.json[0]


def test_types_list_admin_can_add_document_and_folder(api, sandbox):
    ids = by_id(api.get(f"{sandbox}/@types").json)
    assert ids[api.type("document")]["addable"] is True
    assert ids[api.type("folder")]["addable"] is True


def test_types_list_site_root_type_not_addable(api, sandbox):
    ids = by_id(api.get(f"{sandbox}/@types").json)
    site = api.type("site")
    assert site not in ids or ids[site]["addable"] is False


def test_types_list_anonymous_is_401(api, sandbox):
    api.publish(sandbox)
    assert api.get(f"{sandbox}/@types", as_="anonymous").status == 401


@by_persona(member=False, local_editor=False, site_admin=True, manager=True)
def test_types_addable_by_persona(api, sandbox, persona, expected):
    """local Editor cannot add in Plone (Add portal content is Owner/Contributor/
    Manager/Site Administrator); a plain Member neither. Sandbox published so
    every persona can traverse to it."""
    api.publish(sandbox)
    r = api.get(f"{sandbox}/@types", as_=persona)
    assert r.status == 200, r
    ids = by_id(r.json)
    assert ids[api.type("document")]["addable"] is expected
    assert ids[api.type("folder")]["addable"] is expected


def test_types_addable_for_local_contributor(api, sandbox):
    u = api.create_user()
    api.share(sandbox, u, ["Contributor"])
    ids = by_id(api.get(f"{sandbox}/@types", as_=u).json)
    assert ids[api.type("document")]["addable"] is True


# Minimal values for required fields beyond title (from @types/<type> "required").
REQUIRED_EXTRAS = {
    "start": "2030-01-01T10:00:00+00:00",
    "end": "2030-01-01T12:00:00+00:00",
    "remoteUrl": "https://example.org/",
}
SKIP_ADD_PROBE = {"file", "image", "video"}  # need blobs; covered by a file/image area


def test_types_addable_matches_actual_add(api, sandbox):
    """addable must predict whether POST succeeds, for every listed type."""
    u = api.create_user()
    api.share(sandbox, u, ["Contributor"])
    skip = {api.type(t) for t in SKIP_ADD_PROBE} | {"Collection"}  # Collection: see test below
    bad = []
    for item in api.get(f"{sandbox}/@types", as_=u, compare=False).json:
        tname = item["@id"].rsplit("/", 1)[-1]
        if not item["addable"] or tname in skip:
            continue
        req = api.get(f"{sandbox}/@types/{tname}", compare=False).json.get("required", [])
        body = {"@type": tname, "title": f"t {tname}", **{k: v for k, v in REQUIRED_EXTRAS.items() if k in req}}
        r = api.post(sandbox, as_=u, json=body, compare=False)
        if r.status != 201:
            bad.append((tname, r.status, r.text[:200]))
    assert not bad, bad


@pytest.mark.plone_bug("a Collection posted with only its required title fails: 'Object is of wrong type' for field query")
def test_types_collection_minimal_add_fails(api, sandbox):
    if "Collection" not in by_id(api.get(f"{sandbox}/@types", compare=False).json):
        pytest.skip("target has no Collection type")
    r = api.post(sandbox, json={"@type": "Collection", "title": "c"})
    assert r.status == 400, r


@pytest.mark.known_divergence("nick", kind="deliberate", reason="Nick's Page is folderish (Volto-style)")
def test_types_nothing_addable_in_non_folderish(api, sandbox):
    """Plone 6.2 classic Document is not folderish, so nothing is addable inside it."""
    api.doc(sandbox, "d")
    r = api.get(f"{sandbox}/d/@types")
    assert r.status == 200, r
    assert not any(i["addable"] for i in r.json), [i["@id"] for i in r.json if i["addable"]]


def test_types_constrained_by_container(api, sandbox):
    """Nothing is addable in a context you cannot add to (member, published folder)."""
    api.publish(sandbox)
    r = api.get(f"{sandbox}/@types", as_="member")
    assert r.status == 200
    assert not any(i["addable"] for i in r.json)


def test_types_private_context_member(api, sandbox):
    """@types on a context the member cannot view."""
    api.doc(sandbox, "d")
    r = api.get(f"{sandbox}/d/@types", as_="member")
    assert r.status in (401, 403), r


# ------------------------------------------------------------- single type

def test_type_schema_document(api, sandbox):
    r = api.get(f"{sandbox}/@types/{{type:document}}")
    assert r.status == 200, r
    assert r.headers["content-type"].split(";")[0] == "application/json+schema", r.headers["content-type"]
    j = r.json
    assert j["type"] == "object"
    for key in ("title", "properties", "required", "fieldsets", "layouts"):
        assert key in j, f"schema lacks {key}"
    assert "title" in j["properties"]
    assert "title" in j["required"]
    t = j["properties"]["title"]
    assert t["type"] == "string" and t["title"] == "Title"
    assert isinstance(j["fieldsets"], list) and j["fieldsets"][0]["id"] == "default"
    all_fields = [f for fs in j["fieldsets"] for f in fs["fields"]]
    assert "title" in all_fields


def test_type_schema_folder(api, sandbox):
    r = api.get(f"{sandbox}/@types/{{type:folder}}")
    assert r.status == 200, r
    assert "title" in r.json["properties"]


def test_type_schema_property_types(api, sandbox):
    props = api.get(f"{sandbox}/@types/{{type:document}}").json["properties"]
    assert props["description"]["type"] == "string"
    assert props["description"].get("widget") == "textarea", props["description"]
    for k, v in props.items():
        assert "title" in v, f"property {k} lacks a title"
        assert "type" in v or "widget" in v or "factory" in v, f"property {k}: {v}"


def test_type_unknown_is_404(api, sandbox):
    r = api.get(f"{sandbox}/@types/NoSuchType")
    assert r.status == 404, r
    assert r.json.get("type") == "NotFound", r


def test_type_schema_anonymous_is_401(api, sandbox):
    api.publish(sandbox)
    assert api.get(f"{sandbox}/@types/{{type:document}}", as_="anonymous").status == 401


@pytest.mark.plone_bug("check_security lets anonymous through for the context's own type (issue 2054), "
                       "but the dexterity-types traversal then raises Unauthorized: still 401")
def test_type_schema_anonymous_own_type(api, sandbox):
    api.publish(sandbox)
    assert api.get(f"{sandbox}/@types/{{type:folder}}", as_="anonymous").status == 401


@by_persona(member=200, local_editor=200, site_admin=200, manager=200)
def test_type_schema_authenticated(api, sandbox, persona, expected):
    api.publish(sandbox)
    assert api.get(f"{sandbox}/@types/{{type:document}}", as_=persona).status == expected


@pytest.mark.plone_bug("field lookup only searches the FTI model schema: behaviour fields such as "
                       "description come back as 200 {} instead of their JSON schema")
def test_type_field_lookup_behaviour_field(api):
    """GET @types/<type>/<field> on the site root (docs/source/endpoints/types.md)."""
    r = api.get("@types/{type:document}/description")
    assert r.status == 200, r
    assert r.json == {}, r


@pytest.mark.plone_bug("@types/<type>/title is 403 'not publishable': the field name collides with the title attribute")
def test_type_field_lookup_title(api):
    assert api.get("@types/{type:document}/title").status == 403


@pytest.mark.plone_bug("fieldset lookup @types/<type>/default is 200 {} for behaviour-only types")
def test_type_fieldset_lookup(api):
    r = api.get("@types/{type:document}/default")
    assert r.status == 200, r
    assert r.json == {}, r


@pytest.mark.plone_bug("field/fieldset lookup on a non-root context is a 500")
def test_type_field_lookup_on_folder_context(api, sandbox):
    assert api.get(f"{sandbox}/@types/{{type:document}}/description").status == 500


@by_persona(anonymous=401, member=401, local_editor=401, site_admin=400)
def test_type_schema_mutation_refused(api, sandbox, persona, expected):
    """POST @types/<type> (add a field) is refused for non-managers: Plone 6.2
    answers 401 even for authenticated users (Zope Unauthorized). Site
    Administrators pass the permission check and only fail on the
    deliberately invalid factory (400), so no schema is changed."""
    r = api.post(f"@types/{{type:document}}", as_=persona,
                 json={"factory": "label", "title": "rs probe", "fieldset_id": 0})
    assert r.status == expected, r


def test_types_expand_component(api, sandbox):
    r = api.get(sandbox, params={"expand": "types"})
    types = r.json["@components"]["types"]
    assert isinstance(types, list) and {"@id", "addable"} <= set(types[0])
