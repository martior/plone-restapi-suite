"""@navigation, @contextnavigation, @breadcrumbs, @navroot and their
expansion via ?expand=...

Reference: docs/source/endpoints/navigation.md, contextnavigation.md,
breadcrumbs.md, navroot.md, usage/expansion.md and the services of the same
names.

The sandbox sits at level 2 (site / worker_root / sandbox), so its children
are at navigation level 3. @navigation always starts at the navigation root
(the site), so the tests locate the sandbox node in the returned tree.

Fixture tree (``seed``):

    <sandbox>  published
      p3       Document, published
      p1       Document, published
      pf       Folder,   private
        pubin  Document, published
      fold     Folder,   published
        c1     Document, published
        deep   Folder,   published
          c2   Document, published
      hid      Document, published, exclude_from_nav
      hf       Folder,   published, exclude_from_nav
        hfc    Document, published
"""

from __future__ import annotations

import pytest

from restapi_suite.assertions import same_outcome
from restapi_suite.listing import child_ids, find_node, walk

pytestmark = pytest.mark.area("navigation")

NICK = "nick"
NAV = ["p3", "p1", "pf", "fold"]  # creation order = position in parent


def seed(api, sb):
    api.publish(sb)
    for d in ("p3", "p1"):
        api.doc(sb, d, title=d.upper())
        api.publish(f"{sb}/{d}")
    api.folder(sb, "pf", title="Private folder")
    api.doc(f"{sb}/pf", "pubin", title="Published inside private")
    api.publish(f"{sb}/pf/pubin")
    api.folder(sb, "fold", title="Fold")
    api.publish(f"{sb}/fold")
    api.doc(f"{sb}/fold", "c1", title="C1")
    api.publish(f"{sb}/fold/c1")
    api.folder(f"{sb}/fold", "deep", title="Deep")
    api.publish(f"{sb}/fold/deep")
    api.doc(f"{sb}/fold/deep", "c2", title="C2")
    api.publish(f"{sb}/fold/deep/c2")
    api.doc(sb, "hid", title="Hidden", exclude_from_nav=True)
    api.publish(f"{sb}/hid")
    api.folder(sb, "hf", title="Hidden folder", exclude_from_nav=True)
    api.publish(f"{sb}/hf")
    api.doc(f"{sb}/hf", "hfc", title="Child of hidden")
    api.publish(f"{sb}/hf/hfc")


def nav(api, path, depth=None, as_="admin"):
    params = {"expand.navigation.depth": depth} if depth is not None else None
    r = api.get(f"{path}/@navigation", as_=as_, params=params)
    assert r.status == 200, r
    return r


def sb_node(api, r, sandbox, items=None):
    node = find_node(items if items is not None else r.json["items"], api.t.base, sandbox)
    assert node is not None, f"sandbox {sandbox} not in navigation"
    return node


def ordered_children(node):
    return [i["@id"].rstrip("/").rsplit("/", 1)[-1] for i in node.get("items") or []]


# ------------------------------------------------------------ @navigation

def test_navigation_top_level(api, sandbox, worker_root):
    """Without depth, @navigation lists the navigation root's direct children only."""
    r = nav(api, sandbox)
    assert r.json["@id"].endswith(f"{sandbox}/@navigation")
    node = find_node(r.json["items"], api.t.base, worker_root)
    assert node is not None, [i["@id"] for i in r.json["items"]]
    assert not node.get("items"), node


def test_navigation_item_shape(api, sandbox):
    seed(api, sandbox)
    node = sb_node(api, nav(api, sandbox, 4), sandbox)
    for k in ("@id", "title", "description", "review_state", "items"):
        assert k in node, sorted(node)
    p3 = node["items"][0]
    assert p3["title"] == "P3" and p3["review_state"] == "published"


def test_navigation_depth_tree(api, sandbox):
    seed(api, sandbox)
    node = sb_node(api, nav(api, sandbox, 4), sandbox)
    assert ordered_children(node) == NAV
    fold = find_node(node["items"], api.t.base, f"{sandbox}/fold")
    assert child_ids(fold) == ["c1", "deep"]
    deep = find_node(node["items"], api.t.base, f"{sandbox}/fold/deep")
    assert not deep.get("items"), deep  # level 5 is beyond depth 4


def test_navigation_deeper_depth(api, sandbox):
    seed(api, sandbox)
    node = sb_node(api, nav(api, sandbox, 5), sandbox)
    deep = find_node(node["items"], api.t.base, f"{sandbox}/fold/deep")
    assert child_ids(deep) == ["c2"]


def test_navigation_depth_beyond_tree_is_fine(api, sandbox):
    seed(api, sandbox)
    node = sb_node(api, nav(api, sandbox, 50), sandbox)
    assert ordered_children(node) == NAV


@pytest.mark.parametrize("depth", [0, 1])
def test_navigation_depth_zero_or_one_is_top_level(api, sandbox, worker_root, depth):
    r = nav(api, sandbox, depth)
    node = find_node(r.json["items"], api.t.base, worker_root)
    assert node is not None and not node.get("items"), node


def test_navigation_invalid_depth_is_400(api, sandbox):
    r = api.get(f"{sandbox}/@navigation", params={"expand.navigation.depth": "x"})
    assert r.status == 400, r


def test_navigation_same_tree_from_any_context(api, sandbox):
    """@navigation is rooted at the navigation root, not at the context."""
    seed(api, sandbox)
    a = sb_node(api, nav(api, sandbox, 4), sandbox)
    b = sb_node(api, nav(api, f"{sandbox}/fold/deep/c2", 4), sandbox)
    assert ordered_children(a) == ordered_children(b) == NAV


def test_navigation_excludes_exclude_from_nav(api, sandbox):
    seed(api, sandbox)
    node = sb_node(api, nav(api, sandbox, 5), sandbox)
    all_ids = {i["@id"].rsplit("/", 1)[-1] for _, i in walk(node["items"])}
    assert not {"hid", "hf", "hfc"} & all_ids, all_ids


def test_navigation_exclude_from_nav_toggle(api, sandbox):
    seed(api, sandbox)
    api.patch(f"{sandbox}/p1", json={"exclude_from_nav": True}, compare=False)
    assert "p1" not in ordered_children(sb_node(api, nav(api, sandbox, 3), sandbox))
    api.patch(f"{sandbox}/p1", json={"exclude_from_nav": False}, compare=False)
    assert "p1" in ordered_children(sb_node(api, nav(api, sandbox, 3), sandbox))


def test_navigation_follows_ordering(api, sandbox):
    seed(api, sandbox)
    r = api.patch(sandbox, json={"ordering": {"obj_id": "fold", "delta": "top"}}, compare=False)
    assert r.status == 204, r
    assert ordered_children(sb_node(api, nav(api, sandbox, 3), sandbox)) == ["fold", "p3", "p1", "pf"]


def test_navigation_after_title_change(api, sandbox):
    seed(api, sandbox)
    api.patch(f"{sandbox}/p1", json={"title": "Renamed title"}, compare=False)
    node = sb_node(api, nav(api, sandbox, 3), sandbox)
    p1 = find_node(node["items"], api.t.base, f"{sandbox}/p1")
    assert p1["title"] == "Renamed title"


def test_navigation_after_rename(api, sandbox):
    seed(api, sandbox)
    r = api.patch(f"{sandbox}/p1", json={"id": "p1new"}, compare=False)
    assert r.status in (200, 204), r
    assert ordered_children(sb_node(api, nav(api, sandbox, 3), sandbox)) == ["p3", "p1new", "pf", "fold"]


def test_navigation_after_move(api, sandbox):
    seed(api, sandbox)
    r = api.post(f"{sandbox}/fold/@move", json={"source": f"/{sandbox}/p1"}, compare=False)
    assert r.status == 200, r
    node = sb_node(api, nav(api, sandbox, 4), sandbox)
    assert ordered_children(node) == ["p3", "pf", "fold"]
    assert child_ids(find_node(node["items"], api.t.base, f"{sandbox}/fold")) == ["c1", "deep", "p1"]


def test_navigation_after_delete(api, sandbox):
    seed(api, sandbox)
    assert api.delete(f"{sandbox}/fold", compare=False).status == 204
    assert ordered_children(sb_node(api, nav(api, sandbox, 4), sandbox)) == ["p3", "p1", "pf"]


NAV_VIS = {
    "anonymous": (["p3", "p1", "fold"], None),
    "member": (["p3", "p1", "fold"], None),
    "local_editor": (NAV, ["pubin"]),
    "site_admin": (NAV, ["pubin"]),
    "manager": (NAV, ["pubin"]),
}


@pytest.mark.parametrize("persona", list(NAV_VIS))
def test_navigation_permission_filtered(api, sandbox, persona):
    """Private items are hidden, and so are published items below a private folder."""
    seed(api, sandbox)
    children, pf_children = NAV_VIS[persona]
    node = sb_node(api, nav(api, sandbox, 4, as_=persona), sandbox)
    assert ordered_children(node) == children
    pf = find_node(node["items"], api.t.base, f"{sandbox}/pf")
    if pf_children is None:
        assert pf is None
        assert "pubin" not in {i["@id"].rsplit("/", 1)[-1] for _, i in walk(node["items"])}
    else:
        assert child_ids(pf) == pf_children


@pytest.mark.known_divergence(NICK, reason="the local Reader is granted after the content exists and "
                              "@sharing does not reindex the ACL", ref="A2#1")
def test_navigation_local_reader_on_subfolder(api, sandbox):
    seed(api, sandbox)
    u = api.create_user()
    api.share(f"{sandbox}/pf", u, ["Reader"])
    node = sb_node(api, nav(api, sandbox, 4, as_=u), sandbox)
    assert ordered_children(node) == NAV
    assert child_ids(find_node(node["items"], api.t.base, f"{sandbox}/pf")) == ["pubin"]


def test_navigation_private_sandbox_hidden_from_anonymous(api, sandbox, worker_root):
    api.doc(sandbox, "d")
    r = nav(api, worker_root, 3, as_="anonymous")
    assert find_node(r.json["items"], api.t.base, sandbox) is None


def test_navigation_on_private_context_requires_view(api, sandbox):
    r = api.get(f"{sandbox}/@navigation", as_="anonymous")
    assert same_outcome(401, r.status), r


@pytest.mark.known_divergence(NICK, reason="@sharing POST does not reindex the catalog ACL", ref="A2#1")
def test_navigation_after_sharing_grant(api, sandbox):
    seed(api, sandbox)
    u = api.create_user()
    assert "pf" not in ordered_children(sb_node(api, nav(api, sandbox, 3, as_=u), sandbox))
    api.share(f"{sandbox}/pf", u, ["Reader"])
    assert "pf" in ordered_children(sb_node(api, nav(api, sandbox, 3, as_=u), sandbox))


def test_navigation_after_publish(api, sandbox):
    seed(api, sandbox)
    api.publish(f"{sandbox}/pf")
    node = sb_node(api, nav(api, sandbox, 4, as_="anonymous"), sandbox)
    assert ordered_children(node) == NAV
    assert child_ids(find_node(node["items"], api.t.base, f"{sandbox}/pf")) == ["pubin"]


# ------------------------------------------------------------ expansion

def test_components_are_links_without_expand(api, sandbox):
    seed(api, sandbox)
    c = api.get(f"{sandbox}/p1").json["@components"]
    for name in ("navigation", "breadcrumbs", "navroot", "contextnavigation"):
        assert set(c[name]) == {"@id"}, (name, c[name])
        assert c[name]["@id"].endswith(f"{sandbox}/p1/@{name}")


def test_expand_breadcrumbs_and_navigation(api, sandbox):
    seed(api, sandbox)
    r = api.get(f"{sandbox}/fold/c1", params={"expand": "breadcrumbs,navigation"})
    c = r.json["@components"]
    direct = api.get(f"{sandbox}/fold/c1/@breadcrumbs").json
    assert c["breadcrumbs"]["items"] == direct["items"]
    assert "items" in c["navigation"]
    assert "items" not in c["navroot"] and "navroot" not in c["navroot"]  # not expanded


def test_expand_navigation_with_depth(api, sandbox):
    seed(api, sandbox)
    r = api.get(f"{sandbox}/p1", params={"expand": "navigation", "expand.navigation.depth": 4})
    node = sb_node(api, r, sandbox, r.json["@components"]["navigation"]["items"])
    assert ordered_children(node) == NAV


def test_expand_navroot(api, sandbox):
    seed(api, sandbox)
    c = api.get(f"{sandbox}/p1", params={"expand": "navroot"}).json["@components"]
    assert c["navroot"]["navroot"]["@id"].rstrip("/") == api.t.base


def test_expand_contextnavigation(api, sandbox):
    seed(api, sandbox)
    r = api.get(sandbox, params={"expand": "contextnavigation",
                                 "expand.contextnavigation.currentFolderOnly": "true"})
    cn = r.json["@components"]["contextnavigation"]
    assert ordered_children(cn) == NAV


def test_expand_as_anonymous_filters(api, sandbox):
    seed(api, sandbox)
    r = api.get(f"{sandbox}/p1", as_="anonymous", params={"expand": "navigation", "expand.navigation.depth": 4})
    assert r.status == 200, r
    node = sb_node(api, r, sandbox, r.json["@components"]["navigation"]["items"])
    assert ordered_children(node) == ["p3", "p1", "fold"]


# ------------------------------------------------------------ @breadcrumbs

def test_breadcrumbs_nested(api, sandbox, worker_root):
    seed(api, sandbox)
    r = api.get(f"{sandbox}/fold/deep/c2/@breadcrumbs")
    assert r.status == 200, r
    base = api.t.base
    assert [i["@id"] for i in r.json["items"]] == [
        f"{base}/{worker_root}", f"{base}/{sandbox}", f"{base}/{sandbox}/fold", f"{base}/{sandbox}/fold/deep",
        f"{base}/{sandbox}/fold/deep/c2"]
    assert [i["title"] for i in r.json["items"]][2:] == ["Fold", "Deep", "C2"]
    assert r.json["@id"].endswith(f"{sandbox}/fold/deep/c2/@breadcrumbs")


def test_breadcrumbs_item_shape(api, sandbox):
    seed(api, sandbox)
    for item in api.get(f"{sandbox}/fold/@breadcrumbs").json["items"]:
        assert {"@id", "title"} <= set(item), item


def test_breadcrumbs_root_is_navroot_url(api, sandbox):
    seed(api, sandbox)
    r = api.get(f"{sandbox}/fold/@breadcrumbs")
    assert r.json["root"].rstrip("/") == api.t.base, r.json["root"]


def test_breadcrumbs_on_site_root_is_empty(api):
    r = api.get("@breadcrumbs")
    assert r.status == 200 and r.json["items"] == [], r


def test_breadcrumbs_after_title_change(api, sandbox):
    seed(api, sandbox)
    api.patch(f"{sandbox}/fold", json={"title": "Fold renamed"}, compare=False)
    items = api.get(f"{sandbox}/fold/c1/@breadcrumbs").json["items"]
    assert items[-2]["title"] == "Fold renamed"


def test_breadcrumbs_after_move(api, sandbox):
    seed(api, sandbox)
    r = api.post(f"{sandbox}/fold/deep/@move", json={"source": f"/{sandbox}/p1"}, compare=False)
    assert r.status == 200, r
    items = api.get(f"{sandbox}/fold/deep/p1/@breadcrumbs").json["items"]
    assert [i["@id"].rsplit("/", 1)[-1] for i in items][-3:] == ["fold", "deep", "p1"]


def test_breadcrumbs_unicode_titles(api, sandbox):
    api.folder(sandbox, "u", title="Ærlig talt – 日本語")
    items = api.get(f"{sandbox}/u/@breadcrumbs").json["items"]
    assert items[-1]["title"] == "Ærlig talt – 日本語"


@pytest.mark.plone_bug("breadcrumbs of a published item below a private folder show the private folder's "
                       "title to anonymous (ancestors are not permission-filtered)")
def test_breadcrumbs_show_private_ancestor_to_anonymous(api, sandbox):
    seed(api, sandbox)
    r = api.get(f"{sandbox}/pf/pubin/@breadcrumbs", as_="anonymous")
    assert r.status == 200, r
    assert [i["title"] for i in r.json["items"]][-2:] == ["Private folder", "Published inside private"]


def test_breadcrumbs_private_context_requires_view(api, sandbox):
    seed(api, sandbox)
    assert same_outcome(401, api.get(f"{sandbox}/pf/@breadcrumbs", as_="anonymous").status)


# ------------------------------------------------------------ @navroot

def test_navroot_of_nested_content_is_site(api, sandbox):
    seed(api, sandbox)
    r = api.get(f"{sandbox}/fold/deep/@navroot")
    assert r.status == 200, r
    assert r.json["@id"].endswith(f"{sandbox}/fold/deep/@navroot")
    nr = r.json["navroot"]
    assert nr["@id"].rstrip("/") == api.t.base
    assert nr["@type"] == api.type("site")
    assert nr["title"]


def test_navroot_same_for_site_and_content(api, sandbox):
    seed(api, sandbox)
    a = api.get("@navroot").json["navroot"]
    b = api.get(f"{sandbox}/p1/@navroot").json["navroot"]
    assert a["@id"] == b["@id"] and a["title"] == b["title"]


def test_navroot_anonymous(api, sandbox):
    seed(api, sandbox)
    r = api.get(f"{sandbox}/p1/@navroot", as_="anonymous")
    assert r.status == 200 and r.json["navroot"]["@id"].rstrip("/") == api.t.base, r


def test_navroot_private_context_requires_view(api, sandbox):
    assert same_outcome(401, api.get(f"{sandbox}/@navroot", as_="anonymous").status)


# ------------------------------------------------------------ @contextnavigation

def cnav(api, path, as_="admin", **opts):
    params = {f"expand.contextnavigation.{k}": v for k, v in opts.items()}
    r = api.get(f"{path}/@contextnavigation", as_=as_, params=params or None)
    assert r.status == 200, r
    return r


def test_contextnavigation_current_folder_only(api, sandbox):
    seed(api, sandbox)
    r = cnav(api, sandbox, currentFolderOnly="true")
    assert ordered_children(r.json) == NAV
    assert r.json["@id"].endswith(f"{sandbox}/@contextnavigation")


def test_contextnavigation_envelope(api, sandbox):
    seed(api, sandbox)
    j = cnav(api, sandbox, currentFolderOnly="true").json
    assert j["available"] is True and j["has_custom_name"] is False
    assert j["title"] == "Navigation"
    assert j["url"].rstrip("/") == f"{api.t.base}/{sandbox}"


def test_contextnavigation_item_shape(api, sandbox):
    seed(api, sandbox)
    item = cnav(api, sandbox, currentFolderOnly="true").json["items"][0]
    for k in ("@id", "title", "description", "review_state", "href", "is_current", "is_in_path",
              "is_folderish", "normalized_id", "type", "items"):
        assert k in item, sorted(item)
    assert item["normalized_id"] == "p3" and item["type"] == "document" and item["is_folderish"] is False


def test_contextnavigation_custom_name(api, sandbox):
    seed(api, sandbox)
    j = cnav(api, sandbox, currentFolderOnly="true", name="Custom").json
    assert j["title"] == "Custom" and j["has_custom_name"] is True


def test_contextnavigation_include_top(api, sandbox):
    seed(api, sandbox)
    j = cnav(api, sandbox, currentFolderOnly="true", includeTop="true").json
    assert ordered_children(j) == [sandbox.rsplit("/", 1)[-1]] + NAV


def test_contextnavigation_nested_children(api, sandbox):
    seed(api, sandbox)
    j = cnav(api, sandbox, currentFolderOnly="true").json
    assert child_ids(find_node(j["items"], api.t.base, f"{sandbox}/fold")) == ["c1", "deep"]


def test_contextnavigation_excludes_exclude_from_nav(api, sandbox):
    seed(api, sandbox)
    j = cnav(api, sandbox, currentFolderOnly="true").json
    got = {i["@id"].rsplit("/", 1)[-1] for _, i in walk(j["items"])}
    assert not {"hid", "hf", "hfc"} & got, got


@pytest.mark.parametrize("persona,expected", [
    ("anonymous", ["p3", "p1", "fold"]), ("member", ["p3", "p1", "fold"]), ("local_editor", NAV),
    ("manager", NAV)])
@pytest.mark.known_divergence(NICK, reason="contextnavigation filters by the catalog ACL, which omits local "
                              "Editors", ref="A2#2", ids=["local_editor"])
def test_contextnavigation_permission_filtered(api, sandbox, persona, expected):
    seed(api, sandbox)
    assert ordered_children(cnav(api, sandbox, as_=persona, currentFolderOnly="true").json) == expected


def test_contextnavigation_default_marks_current_path(api, sandbox):
    """By default the tree starts at the first level (here: the worker root) and the
    path to the context is marked."""
    seed(api, sandbox)
    j = cnav(api, f"{sandbox}/fold").json
    sb = find_node(j["items"], api.t.base, sandbox)
    assert sb is not None and sb["is_in_path"] is True and sb["is_current"] is False
    fold = find_node(j["items"], api.t.base, f"{sandbox}/fold")
    assert fold["is_current"] is True
    assert child_ids(fold) == ["c1", "deep"]


@pytest.mark.plone_bug("on a non-folderish context, currentFolderOnly lists only the context itself; its "
                       "siblings in the parent folder are missing (on the folder they are all listed)")
def test_contextnavigation_on_document(api, sandbox):
    seed(api, sandbox)
    j = cnav(api, f"{sandbox}/fold/c1", currentFolderOnly="true").json
    assert ordered_children(j) == ["c1"]
    c1 = find_node(j["items"], api.t.base, f"{sandbox}/fold/c1")
    assert c1["is_current"] is True


def test_contextnavigation_private_context_requires_view(api, sandbox):
    assert same_outcome(401, api.get(f"{sandbox}/@contextnavigation", as_="anonymous").status)
