"""Plone 6.2 -> Nick migration round trip.

Opt-in (marker ``migration``): it needs a Plone source seeded with
``seed_plone`` and a fresh Nick that imported the profile written by Nick's
``pnpm export`` from that source. Both URLs come from the environment:

    MIGRATION_SOURCE_URL       Plone site, e.g. http://127.0.0.1:18700/plone
    MIGRATION_TARGET_URL       Nick, e.g. http://127.0.0.1:18701
    MIGRATION_SOURCE_ADMIN     user:password on Plone (default admin:admin)
    MIGRATION_TARGET_ADMIN     user:password on Nick (default admin:admin)

Workflow:

    python tests/test_migration_roundtrip.py seed $MIGRATION_SOURCE_URL
    (cd nick && pnpm export $MIGRATION_SOURCE_URL admin admin migrated)
    # fresh Nick with profiles core + migrated, then:
    pytest -m migration tests/test_migration_roundtrip.py

The seed also adds a Dexterity type ``guarded_item`` with a custom workflow
chain (tests/fixtures/roundtrip_workflows, imported as a GenericSetup
tarball): guards with roles, a group and expressions, a variable, a worklist
and a second workflow. ``test_custom_workflow_guards`` checks that every
persona is offered the same transitions on its items; it skips on sources
seeded before it was added.

The test compares, item by item: the content tree (paths, types, UUIDs,
titles), review states, local roles and the inherit flag, workflow history,
versions, blobs (byte equality of every file and image), relations,
translations, redirects and comments. Then it compares @search for
anonymous and for seeded users with different roles. Users get new random
passwords on import (password hashes are not readable over REST), so the
test sets a known password on both sides for the users it logs in as.
"""

from __future__ import annotations

import base64
from pathlib import Path
import os
import struct
import sys
import zlib
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx
import pytest

pytestmark = [pytest.mark.area("migration"), pytest.mark.migration]

PASSWORD = "Roundtrip-secret-1"

# Users seeded on Plone: id -> (fullname, global roles)
USERS = {
    "member1": ("Mia Member", ["Member"]),
    "reader1": ("Rolf Reader", ["Member"]),
    "editor1": ("Erika Editor", ["Member"]),
    "contributor1": ("Carl Contributor", ["Member"]),
    "reviewer1": ("Rita Reviewer", ["Member"]),
}
GROUPS = {
    "staff": ("Staff", [], ["editor1", "reader1"]),
    "reviewers-team": ("Reviewers team", ["Reviewer"], ["reviewer1"]),
}
ARCHIVE_SIZE = 130  # more than one export batch


# -- HTTP ---------------------------------------------------------------


class Site:
    """Minimal JSON client for one backend (no cookies, JWT per user)."""

    def __init__(self, base: str, admin: str = "admin:admin"):
        self.base = base.rstrip("/")
        self.admin = tuple(admin.split(":", 1))
        jar = CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))
        self.http = httpx.Client(timeout=120, cookies=jar, follow_redirects=False)
        self.tokens: dict[str, str] = {}

    def url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return self.base + ("/" + path.lstrip("/") if path.strip("/") else "")

    def login(self, user: str, password: str) -> str:
        r = self.http.post(self.url("@login"), json={"login": user, "password": password},
                           headers={"Accept": "application/json"})
        assert r.status_code == 200, f"login {user} on {self.base}: {r.status_code} {r.text[:300]}"
        self.tokens[user] = r.json()["token"]
        return self.tokens[user]

    def headers(self, as_: str | None) -> dict:
        h = {"Accept": "application/json"}
        if as_ is not None:
            if as_ not in self.tokens:
                if as_ == self.admin[0]:
                    self.login(*self.admin)
                else:
                    self.login(as_, PASSWORD)
            h["Authorization"] = f"Bearer {self.tokens[as_]}"
        return h

    def req(self, method: str, path: str, as_: str | None = "admin", ok=(200, 201, 204), **kw):
        if as_ == "admin":
            as_ = self.admin[0]
        r = self.http.request(method, self.url(path), headers={**self.headers(as_), **kw.pop("headers", {})}, **kw)
        if ok and r.status_code not in ok:
            raise AssertionError(f"{method} {self.url(path)} as {as_} -> {r.status_code} {r.text[:500]}")
        return r

    def get(self, path, as_="admin", **kw):
        return self.req("GET", path, as_, **kw).json()

    def post(self, path, json=None, as_="admin", **kw):
        r = self.req("POST", path, as_, json=json, **kw)
        return r.json() if r.content.strip() else None

    def patch(self, path, json=None, as_="admin", **kw):
        return self.req("PATCH", path, as_, json=json, **kw)


# -- fixtures for blobs ---------------------------------------------------


def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
CSV = "id;name;amount\n1;Ærøskøbing;12.5\n2;Zürich;7\n".encode()
BLOBS = {
    "en/files/report.pdf": ("file", "report.pdf", "application/pdf", PDF),
    "en/files/data.csv": ("file", "data.csv", "text/csv", CSV),
    "en/files/logo.png": ("image", "logo.png", "image/png", png(64, 48, (200, 30, 90))),
}


def b64(data: bytes, filename: str, ctype: str) -> dict:
    return {"data": base64.b64encode(data).decode(), "encoding": "base64", "filename": filename,
            "content-type": ctype}


def slate(text: str, link: str | None = None) -> dict:
    children = [{"text": text}]
    if link:
        children += [{"type": "link", "data": {"url": link}, "children": [{"text": "link"}]}, {"text": ""}]
    return {"@type": "slate", "value": [{"type": "p", "children": children}], "plaintext": text}


def blocks(*items: dict) -> dict:
    ids = [f"b{i:02d}0000-0000-4000-8000-000000000000" for i in range(len(items) + 1)]
    data = {ids[0]: {"@type": "title"}}
    data.update({bid: item for bid, item in zip(ids[1:], items)})
    return {"blocks": data, "blocks_layout": {"items": ids}}


# -- seeding Plone --------------------------------------------------------


def seed_plone(base: str, admin: str = "admin", password: str = "admin") -> None:
    """Seed a fresh Plone 6.2 site (volto distribution) with content that
    covers every migration gap. Run once on an empty site."""
    p = Site(base, f"{admin}:{password}")
    zmi = {"Authorization": p.headers("admin")["Authorization"]}  # no Accept: JSON would hit plone.restapi
    auth = p.http.get(p.url("@@authenticator/token"), headers=zmi).text.strip()

    # Add-ons: discussion and multilingual (en + de)
    p.patch("@registry", {"plone.available_languages": ["en", "de"], "plone.default_language": "en"})
    for addon in ("plone.app.discussion", "plone.app.multilingual"):
        p.post(f"@addons/{addon}/install")
    p.patch("@registry", {"plone.app.discussion.interfaces.IDiscussionSettings.globally_enabled": True})

    # Custom workflow binding: events follow the intranet workflow
    r = p.http.post(p.url("portal_workflow/manage_changeWorkflows"), headers=zmi, data={
        "_authenticator": auth, "default_chain": "simple_publication_workflow", "chain_File": "",
        "chain_Image": "", "chain_Plone Site": "", "chain_Discussion Item": "comment_one_state_workflow",
        "chain_Event": "intranet_workflow"})
    assert r.status_code == 200, r.text[:300]

    # Users and groups
    for uid, (fullname, roles) in USERS.items():
        p.post("@users", {"username": uid, "email": f"{uid}@example.org", "password": PASSWORD,
                          "fullname": fullname, "roles": roles, "description": f"Bio of {fullname}",
                          "location": "Oslo"})
    for gid, (title, roles, members) in GROUPS.items():
        p.post("@groups", {"groupname": gid, "title": title, "description": f"{title} group",
                           "email": f"{gid}@example.org", "roles": roles, "users": members})

    def create(parent, type_, id_, title, as_="admin", **fields):
        return p.post(parent, {"@type": type_, "id": id_, "title": title, **fields}, as_=as_)

    def tr(path, transition, as_="admin", comment=""):
        p.post(f"{path}/@workflow/{transition}", {"comment": comment} if comment else {}, as_=as_)

    def share(path, entries, inherit=True):
        p.post(f"{path}/@sharing", {"entries": entries, "inherit": inherit})

    # /en/files: blobs (File and Image have no workflow in Plone)
    create("en", "Document", "files", "Files", description="Downloads", **blocks(slate("All files.")))
    tr("en/files", "publish")
    for path, (field, filename, ctype, data) in BLOBS.items():
        parent, id_ = path.rsplit("/", 1)
        create(parent, "Image" if field == "image" else "File", id_, filename.split(".")[0].title(),
               **{field: b64(data, filename, ctype)})

    # /en/about: versioned edits, relations, translation, redirects
    create("en", "Document", "about", "About us", description="Who we are",
                  **blocks(slate("About the company.")))
    tr("en/about", "publish", comment="Ready for the world")
    create("en/about", "Document", "old-team", "Team", **blocks(slate("First version.")))
    for i, text in enumerate(["Second version.", "Third version, with a link."], start=2):
        p.patch("en/about/old-team", {"title": f"Team v{i}", "changeNote": f"Edit number {i}",
                                      **blocks(slate(text, link=f"{p.base}/en/files/report.pdf"))})
    p.patch("en/about/old-team", {"id": "team"})  # rename: automatic redirect
    p.post("en/about/@aliases", {"items": [{"path": "/en/about-us"}, {"path": "/en/company"}]})
    report_uid = p.get("en/files/report.pdf")["UID"]
    history_doc = create("en/about", "Document", "history", "Our history", **blocks(slate("Since 1999.")))
    p.patch("en/about/team", {"relatedItems": [history_doc["UID"], report_uid]})
    tr("en/about/team", "publish")  # history stays private

    de = create("de", "Document", "ueber-uns", "Über uns", **blocks(slate("Über die Firma.")))
    tr("de/ueber-uns", "publish")
    p.post("en/about/@translations", {"id": de["@id"]})

    # /en/news: owner contributor1, submit/publish/reject history, comments, lead image
    create("en", "Document", "news", "News", **blocks(slate("Latest news."), {
        "@type": "listing", "querystring": {"query": [
            {"i": "portal_type", "o": "plone.app.querystring.operation.selection.any", "v": ["News Item"]}],
            "sort_on": "effective", "sort_order": "descending"}}))
    tr("en/news", "publish")
    share("en/news", [{"id": "contributor1", "type": "user", "roles": {"Contributor": True}}])
    logo_uid = p.get("en/files/logo.png")["UID"]
    create("en/news", "News Item", "launch", "Product launch", as_="contributor1", allow_discussion=True,
           preview_image_link=logo_uid, preview_caption_link="Launch photo", **blocks(slate("We launched.")))
    p.patch("en/news/launch", {"title": "Product launch 2026", "changeNote": "Year added"}, as_="contributor1")
    tr("en/news/launch", "submit", as_="contributor1", comment="Please review")
    tr("en/news/launch", "reject", as_="reviewer1", comment="Needs a photo credit")
    p.patch("en/news/launch", {"preview_caption_link": "Launch photo (c) ACME", "changeNote": "Credit"},
            as_="contributor1")
    tr("en/news/launch", "submit", as_="contributor1")
    tr("en/news/launch", "publish", as_="reviewer1", comment="Approved")
    p.post("en/news/launch/@comments", {"text": "Congratulations!"}, as_="member1")
    comments = p.get("en/news/launch/@comments")["items"]
    p.post(f"en/news/launch/@comments/{comments[0]['comment_id']}", {"text": "Thanks!"},
           as_="contributor1")
    create("en/news", "News Item", "upcoming", "Upcoming release", as_="contributor1",
           **blocks(slate("Soon.")))
    tr("en/news/upcoming", "submit", as_="contributor1", comment="For next week")
    create("en/news", "News Item", "draft", "Draft idea", as_="contributor1", **blocks(slate("Idea.")))

    # Teaser and image blocks pointing to other documents (a new version of /en/about)
    p.patch("en/about", {"changeNote": "Teaser", **blocks(
        slate("About the company."),
        {"@type": "teaser", "href": [{"@id": f"{p.base}/en/news/launch"}], "title": "Launch",
         "description": "Our launch"},
        {"@type": "image", "url": f"{p.base}/en/files/logo.png", "alt": "Logo"})})

    # /en/events: intranet_workflow states
    create("en", "Document", "events", "Events", **blocks(slate("What is on.")))
    tr("en/events", "publish")
    ev = {"start": "2026-11-01T10:00:00+00:00", "end": "2026-11-01T12:00:00+00:00",
          "location": "Oslo", "whole_day": False, "open_end": False}
    create("en/events", "Event", "summit", "Summit", **ev)
    tr("en/events/summit", "publish_internally")
    tr("en/events/summit", "publish_externally", comment="Open to the public")
    create("en/events", "Event", "staff-party", "Staff party", **ev)
    tr("en/events/staff-party", "publish_internally")
    create("en/events", "Event", "planning", "Planning meeting", **ev)  # internal

    # /en/intranet: private, group local roles; /secret blocks inheritance
    create("en", "Document", "intranet", "Intranet", **blocks(slate("Staff only.")))
    share("en/intranet", [{"id": "staff", "type": "group", "roles": {"Reader": True}},
                          {"id": "editor1", "type": "user", "roles": {"Editor": True}}])
    create("en/intranet", "Document", "handbook", "Handbook", **blocks(slate("Rules.")))
    create("en/intranet", "Document", "secret", "Secret", **blocks(slate("Board only.")))
    share("en/intranet/secret", [{"id": "reviewer1", "type": "user", "roles": {"Reader": True}}],
          inherit=False)
    create("en/intranet/secret", "Document", "minutes", "Board minutes", **blocks(slate("Minutes.")))
    create("en/intranet", "Image", "chart.png", "Chart",
           image=b64(png(20, 10, (0, 0, 0)), "chart.png", "image/png"))

    # /en/guarded: a type with a custom workflow chain (guards with roles,
    # groups and expressions, a variable, a worklist, a second workflow)
    seed_guarded_workflow(p, zmi, create, tr, share)

    # /en/archive: enough items to need several export batches
    create("en", "Document", "archive", "Archive", **blocks(slate("Old pages.")))
    tr("en/archive", "publish")
    for i in range(ARCHIVE_SIZE):
        create("en/archive", "Document", f"page-{i:03d}", f"Archived page {i}",
               description=f"Archive entry number {i}", subjects=["archive", f"year-{2000 + i % 20}"])
        if i % 3:
            tr(f"en/archive/page-{i:03d}", "publish")


GUARDED_TYPE = "guarded_item"
GUARDED_WORKFLOWS = Path(__file__).parent / "fixtures" / "roundtrip_workflows"


def seed_guarded_workflow(p, zmi, create, tr, share) -> None:
    """Add the type Guarded Item with the chain roundtrip_guarded_workflow +
    roundtrip_flag_workflow (tests/fixtures/roundtrip_workflows, imported as
    a GenericSetup tarball) and items in its states."""
    import io
    import tarfile

    p.post("@controlpanels/dexterity-types", {"title": "Guarded Item", "description": "Custom workflow"})
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for path in sorted(GUARDED_WORKFLOWS.rglob("*.xml")):
            tar.add(path, arcname=str(path.relative_to(GUARDED_WORKFLOWS)))
    auth = p.http.get(p.url("@@authenticator/token"), headers=zmi).text.strip()
    r = p.http.post(p.url("portal_setup/manage_importTarball"), headers=zmi,
                    data={"_authenticator": auth, "submitted": "1"},
                    files={"tarball": ("workflows.tar.gz", archive.getvalue(), "application/x-gzip")})
    assert r.status_code == 200, r.text[:300]
    assert p.get(f"@types/{GUARDED_TYPE}")  # the type exists

    create("en", "Document", "guarded", "Guarded", **blocks(slate("Custom workflow.")))
    tr("en/guarded", "publish")
    share("en/guarded", [{"id": "editor1", "type": "user", "roles": {"Editor": True}},
                         {"id": "contributor1", "type": "user", "roles": {"Contributor": True}}])
    create("en/guarded", GUARDED_TYPE, "draft-item", "Draft item")
    create("en/guarded", GUARDED_TYPE, "locked-item", "Locked plan")
    create("en/guarded", GUARDED_TYPE, "own-item", "Own item", as_="contributor1")
    create("en/guarded", GUARDED_TYPE, "review-item", "Review item")
    tr("en/guarded/review-item", "send", comment="Please check")
    create("en/guarded", GUARDED_TYPE, "done-item", "Done item")
    tr("en/guarded/done-item", "send")
    tr("en/guarded/done-item", "approve", as_="reviewer1", comment="Fine")


# -- verification -----------------------------------------------------------

SOURCE_URL = os.environ.get("MIGRATION_SOURCE_URL")
TARGET_URL = os.environ.get("MIGRATION_TARGET_URL")
TARGET_USERS = os.environ.get("MIGRATION_TARGET_USERS")  # users.json of the exported profile
needs_sites = pytest.mark.skipif(not (SOURCE_URL and TARGET_URL),
                                 reason="set MIGRATION_SOURCE_URL and MIGRATION_TARGET_URL")
# Personas for the permission filtered search: anonymous plus seeded users
SEARCH_PERSONAS = [None, "member1", "reader1", "editor1", "contributor1", "reviewer1"]
NO_WORKFLOW = ("File", "Image", "Plone Site")  # empty chains in the seeded site


def norm_uid(uid: str | None) -> str | None:
    return uid.replace("-", "").lower() if uid else uid


def norm_time(value: str | None) -> int | None:
    """ISO datetime -> epoch seconds (Plone: +00:00, Nick: .000Z)."""
    if not value:
        return None
    from datetime import datetime
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def rel(site: Site, url: str) -> str:
    """Path of a url relative to the site root ('' for the root)."""
    path = url[len(site.base):] if url.startswith(site.base) else url
    return path.rstrip("/")


def search_all(site: Site, as_: str | None = "admin", **params) -> list[dict]:
    """All @search results, batch by batch (comments are not documents)."""
    items, start, size = [], 0, 100
    while True:
        batch = site.req("GET", "@search", as_, params={**params, "b_size": size, "b_start": start}).json()["items"]
        items += [i for i in batch if i.get("@type") != "Discussion Item"]
        start += len(batch)
        if len(batch) < size:
            return items


class Side:
    """Everything the test compares, fetched once per backend."""

    def __init__(self, site: Site):
        from concurrent.futures import ThreadPoolExecutor
        self.site = site
        self.search = {rel(site, i["@id"]): i for i in search_all(site, sort_on="path")}
        with ThreadPoolExecutor(8) as pool:
            self.docs = dict(zip(self.search, pool.map(self._fetch, list(self.search))))

    def _fetch(self, path: str) -> dict:
        s = self.site
        doc = s.get(f"{path}?b_size=1000&expand=translations")
        out = {"json": doc, "history": s.get(f"{path}/@history"), "workflow": s.get(f"{path}/@workflow"),
               "comments": s.get(f"{path}/@comments")["items"] if doc.get("allow_discussion") else []}
        out["versions"] = {e["version"]: s.get(f"{path}/@history/{e['version']}")
                           for e in out["history"] if e["type"] == "versioning"}
        out["blobs"] = {}
        for field, value in doc.items():
            if isinstance(value, dict) and "download" in value and "filename" in value:
                r = s.req("GET", f"{path}/@@download/{field}", headers={"Accept": "*/*"})
                out["blobs"][field] = (value["filename"], value["content-type"], r.content)
        return out


@pytest.fixture(scope="module")
def plone():
    return Site(SOURCE_URL, os.environ.get("MIGRATION_SOURCE_ADMIN", "admin:admin"))


@pytest.fixture(scope="module")
def nick():
    return Site(TARGET_URL, os.environ.get("MIGRATION_TARGET_ADMIN", "admin:admin"))


@pytest.fixture(scope="module")
def sides(plone, nick):
    return Side(plone), Side(nick)


@pytest.fixture(scope="module")
def passwords():
    """Passwords of the migrated users (Nick), from the exported users.json."""
    if not TARGET_USERS:
        pytest.skip("set MIGRATION_TARGET_USERS to the users.json of the exported profile")
    import json
    with open(TARGET_USERS) as fh:
        return {u["id"]: u["password"] for u in json.load(fh)["users"]}


def compare(pairs):
    """Collect mismatches as (key, plone value, nick value)."""
    return [(k, a, b) for k, a, b in pairs if a != b]


def slate_text(blocks: dict) -> list:
    out = []
    for bid, block in sorted((blocks or {}).items()):
        if block.get("@type") == "slate":
            out.append((bid, block.get("plaintext")))
        elif block.get("@type") == "teaser":
            out.append((bid, [h["@id"] for h in block.get("href", [])], block.get("title")))
        elif block.get("@type") == "image":
            out.append((bid, block.get("url")))
        elif block.get("@type") == "listing":
            out.append((bid, [(q["i"], q["o"].replace("plone.app.querystring.operation.", ""), q["v"])
                              for q in block["querystring"]["query"]]))
        else:
            out.append((bid, block.get("@type")))
    return out


def link_paths(site: Site, value):
    """Make urls of a site comparable: strip the base url."""
    import json
    return json.loads(json.dumps(value).replace(site.base, ""))


# -- tests --------------------------------------------------------------------

@needs_sites
def test_content_tree(sides):
    p, n = sides
    assert sorted(p.search) == sorted(n.search)
    mismatches = []
    for path, src in p.docs.items():
        a, b = src["json"], n.docs[path]["json"]
        mismatches += compare([
            (f"{path} @type", a["@type"], b["@type"]),
            (f"{path} UID", norm_uid(a["UID"]), norm_uid(b["UID"])),
            (f"{path} id", a.get("id") if path else "root", b.get("id") if path else "root"),
            (f"{path} title", a.get("title"), b.get("title")),
            (f"{path} description", a.get("description"), b.get("description")),
        ])
    assert not mismatches, mismatches


@needs_sites
def test_review_states(sides):
    p, n = sides
    mismatches = []
    for path, src in p.docs.items():
        a, b = src["json"], n.docs[path]["json"]
        expected = a.get("review_state")
        if a["@type"] in NO_WORKFLOW:
            # no workflow in Plone: the state of the parent, the root is published
            parent = path.rsplit("/", 1)[0] if path else None
            expected = p.docs[parent]["json"].get("review_state") if parent is not None else "published"
            expected = expected or "published"
        mismatches += compare([(path, expected, b.get("review_state"))])
    assert not mismatches, mismatches


@needs_sites
def test_field_values(sides):
    p, n = sides
    fields = ["subjects", "exclude_from_nav", "allow_discussion", "preview_caption_link", "location",
              "whole_day", "open_end", "nav_title", "rights"]
    dates = ["effective", "expires", "start", "end", "created", "modified"]
    mismatches = []
    for path, src in p.docs.items():
        a, b = src["json"], n.docs[path]["json"]
        mismatches += compare([(f"{path} {f}", a.get(f), b.get(f)) for f in fields if f in a])
        mismatches += compare([(f"{path} {f}", norm_time(a.get(f)), norm_time(b.get(f)))
                               for f in dates if f in a and path])
        mismatches += compare([
            (f"{path} language", (a.get("language") or {}).get("token"), (b.get("language") or {}).get("token")
             if isinstance(b.get("language"), dict) else b.get("language")),
            (f"{path} blocks", link_paths(p.site, slate_text(a.get("blocks"))),
             link_paths(n.site, slate_text(b.get("blocks")))),
            (f"{path} blocks_layout", a.get("blocks_layout"), b.get("blocks_layout")),
        ])
    assert not mismatches, mismatches


@needs_sites
def test_folder_order(sides):
    p, n = sides
    mismatches = []
    for path, src in p.docs.items():
        a = [rel(p.site, i["@id"]) for i in src["json"].get("items", [])]
        b = [rel(n.site, i["@id"]) for i in n.docs[path]["json"].get("items", [])]
        mismatches += compare([(path, a, b)])
    assert not mismatches, mismatches


@needs_sites
def test_relations(sides):
    p, n = sides
    mismatches = []
    for path, src in p.docs.items():
        a, b = src["json"], n.docs[path]["json"]
        mismatches += compare([
            (f"{path} relatedItems", [norm_uid(i["UID"]) for i in a.get("relatedItems") or []],
             [norm_uid(i["UID"]) for i in b.get("relatedItems") or []]),
        ])
        if a.get("preview_image_link"):
            link = b.get("preview_image_link")
            link = link[0] if isinstance(link, list) and link else link
            mismatches += compare([(f"{path} preview_image_link", norm_uid(a["preview_image_link"]["UID"]),
                                    norm_uid((link or {}).get("UID")))])
    assert not mismatches, mismatches


def localized(path: str) -> bool:
    """Plone translates action and transition titles for content in German."""
    return path == "/de" or path.startswith("/de/")


def plone_workflow_history(side, path):
    """Plone: the raw history of @workflow (@history translates action ids)."""
    # Keyed by the action too: a creation and a transition to the same state
    # can happen in the same second
    titles = {(e["time"], e["review_state"], e.get("action")): e.get("transition_title")
              for e in side.docs[path]["history"] if e["type"] == "workflow"}
    return [(e["action"], e["actor"], e["review_state"], e.get("comments") or "", norm_time(e["time"]),
             None if localized(path) else titles.get((e["time"], e["review_state"], e["action"] or "Create")))
            for e in side.docs[path]["workflow"].get("history", [])]


def nick_workflow_history(side, path):
    """Nick's @history shows the creation entry as "Create", like Plone's @history."""
    return [(None if e.get("action") == "Create" else e.get("action"), e["actor"]["id"], e["review_state"],
             e.get("comments") or "", norm_time(e["time"]),
             None if localized(path) else e.get("transition_title"))
            for e in side.docs[path]["history"] if e["type"] == "workflow"]


@needs_sites
def test_workflow_history(sides):
    p, n = sides
    entries = 0
    mismatches = []
    for path in p.docs:
        a = sorted(plone_workflow_history(p, path), key=str)
        b = sorted(nick_workflow_history(n, path), key=str)
        entries += len(a)
        mismatches += compare([(path, a, b)])
    assert entries > 0
    assert not mismatches, mismatches


@needs_sites
def test_versions(sides):
    p, n = sides
    count = 0
    mismatches = []
    for path, src in p.docs.items():
        def versions(side):
            return sorted((e["version"], e["actor"]["id"], e.get("comments") or "", norm_time(e["time"]))
                          for e in side.docs[path]["history"] if e["type"] == "versioning")
        a = versions(p)
        count += len(a)
        if not a:
            continue  # unversioned in Plone; Nick keeps one version of the current content
        mismatches += compare([(f"{path} versions", a, versions(n))])
        for number, version in src["versions"].items():
            other = n.docs[path]["versions"].get(number, {})
            mismatches += compare([
                (f"{path}@{number} title", version.get("title"), other.get("title")),
                (f"{path}@{number} blocks", link_paths(p.site, slate_text(version.get("blocks"))),
                 link_paths(n.site, slate_text(other.get("blocks")))),
            ])
    assert count > 0
    assert not mismatches, mismatches


PRINCIPALS = {**{u: f for u, (f, _r) in USERS.items()}, **{g: t for g, (t, _r, _m) in GROUPS.items()}}


@needs_sites
def test_local_roles(sides):
    """Local roles (value True) and the inherit flag, per principal, via @sharing?search=."""
    p, n = sides
    assignments, blocked = 0, 0
    mismatches = []
    for path in p.docs:
        for principal, title in PRINCIPALS.items():
            def local(side):
                data = side.site.get(f"{path}/@sharing", params={"search": title})
                roles = next((sorted(r for r, v in e["roles"].items() if v is True)
                              for e in data["entries"] if e["id"] == principal), [])
                return data["inherit"], roles
            a = local(p)
            assignments += len(a[1])
            blocked += not a[0]
            mismatches += compare([(f"{path} {principal}", a, local(n))])
    assert assignments >= 4 and blocked > 0
    assert not mismatches, mismatches


@needs_sites
def test_blobs_byte_equal(sides):
    p, n = sides
    count = 0
    mismatches = []
    for path, src in p.docs.items():
        for field, (filename, ctype, data) in src["blobs"].items():
            count += 1
            other = n.docs[path]["blobs"].get(field)
            mismatches += compare([(f"{path} {field}", (filename, ctype, len(data), data),
                                    other[:2] + (len(other[2]), other[2]) if other else None)])
    assert count >= 4
    assert not mismatches, [(k, a[:3] if a else a, b[:3] if b else b) for k, a, b in mismatches]


@needs_sites
def test_translations(sides):
    p, n = sides
    count = 0
    mismatches = []
    for path, src in p.docs.items():
        def translations(side):
            items = (side.docs[path]["json"].get("@components", {}).get("translations") or {}).get("items", [])
            return sorted((rel(side.site, i["@id"]), i["language"]) for i in items
                          if rel(side.site, i["@id"]) != path)
        count += len(translations(p))
        mismatches += compare([(path, translations(p), translations(n))])
    assert count >= 4  # /en <-> /de and /en/about <-> /de/ueber-uns
    assert not mismatches, mismatches


@needs_sites
def test_redirects(plone, nick):
    def aliases(site):
        return sorted((a["path"], a["redirect-to"], a["manual"], norm_time(a["datetime"]))
                      for a in site.get("@aliases")["items"])
    assert aliases(plone) == aliases(nick)
    for path, target, _manual, _time in aliases(plone):
        r = nick.req("GET", path, ok=None)
        assert r.status_code in (301, 302) and r.headers["location"].rstrip("/").endswith(target), (path, r)


@needs_sites
def test_comments(sides):
    p, n = sides
    count = 0
    mismatches = []
    for path in p.docs:
        def comments(side):
            return sorted((c["comment_id"], c["in_reply_to"], c["author_username"], c["text"],
                           norm_time(c["creation_date"])) for c in side.docs[path]["comments"])
        count += len(p.docs[path]["comments"])
        mismatches += compare([(path, comments(p), comments(n))])
    assert count > 0
    assert not mismatches, mismatches


@needs_sites
def test_users_and_groups(plone, nick):
    role = {"Manager": "Administrator", "Site Administrator": "Administrator"}
    for uid in USERS:
        a, b = plone.get(f"@users/{uid}"), nick.get(f"@users/{uid}")
        assert (b["fullname"], b["email"]) == (a["fullname"], a["email"])
        assert sorted(role.get(r, r) for r in a["roles"] if r not in ("Member", "Authenticated")) == \
            sorted(r for r in b["roles"] if r not in ("Authenticated",))
        assert sorted(g["id"] for g in a["groups"]["items"] if g["id"] != "AuthenticatedUsers") == \
            sorted(g["id"] if isinstance(g, dict) else g for g in (b.get("groups") or []))
    for gid, (title, roles, _members) in GROUPS.items():
        b = nick.get(f"@groups/{gid}")
        assert b["title"] == title
        assert sorted(b["roles"]) == sorted(roles)


@needs_sites
def test_own_passwords(plone, nick, passwords):
    """Every migrated user has its own random password; the Plone password does not work."""
    assert len({passwords[u] for u in USERS}) == len(USERS)
    for uid in USERS:
        assert nick.http.post(nick.url("@login"), json={"login": uid, "password": PASSWORD},
                              headers={"Accept": "application/json"}).status_code in (401, 403, 400)
        nick.login(uid, passwords[uid])


@needs_sites
def test_workflow_transitions(sides):
    """Workflow definitions: the transitions offered to the admin are the same."""
    p, n = sides
    mismatches = []
    for path in p.docs:
        # The custom workflow is compared per persona in test_custom_workflow_guards
        if p.docs[path]["json"]["@type"] in (*NO_WORKFLOW, GUARDED_TYPE):
            continue
        def transitions(side):
            data = side.site.get(f"{path}/@workflow")
            return (data.get("state", {}).get("id"),
                    sorted((t["@id"].rsplit("/", 1)[-1], None if localized(path) else t["title"])
                           for t in data["transitions"]))
        mismatches += compare([(path, transitions(p), transitions(n))])
    assert not mismatches, mismatches


@needs_sites
def test_search_as_admin(sides):
    p, n = sides
    a = {k: (v["@type"], v.get("review_state"), v.get("title")) for k, v in p.search.items()}
    b = {k: (v["@type"], v.get("review_state"), v.get("title")) for k, v in n.search.items()}
    mismatches = compare([(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b))
                          if p.docs.get(k, {}).get("json", {}).get("@type") not in NO_WORKFLOW])
    assert not mismatches, mismatches


QUERIES = [
    ("", {"portal_type": "News Item"}),
    ("", {"portal_type": "Event"}),
    ("", {"review_state": "pending"}),
    ("", {"review_state": "private"}),
    ("", {"review_state": "internal"}),
    ("", {"SearchableText": "Minutes"}),
    ("en/intranet", {}),
    ("en/news", {"review_state": "published"}),
]


@needs_sites
@pytest.mark.parametrize("context,query", QUERIES,
                         ids=lambda v: v if isinstance(v, str) else "&".join(f"{k}={x}" for k, x in v.items()))
def test_search_queries(sides, context, query):
    """Same results for index queries and context searches. Files and images have no review_state in
    Plone (no workflow), so they are left out of state queries."""
    p, n = sides

    def paths(side):
        found = side.site.req("GET", f"{context}/@search", params={**query, "b_size": 1000}).json()["items"]
        return sorted(rel(side.site, i["@id"]) for i in found if i.get("@type") != "Discussion Item"
                      and not ("review_state" in query and p.docs[rel(side.site, i["@id"])]["json"]["@type"]
                               in NO_WORKFLOW))
    assert paths(p) == paths(n)


@needs_sites
@pytest.mark.parametrize("persona", SEARCH_PERSONAS, ids=lambda u: u or "anonymous")
def test_permission_filtered_search(plone, nick, passwords, persona):
    if persona:
        nick.login(persona, passwords[persona])
    a = sorted(rel(plone, i["@id"]) for i in search_all(plone, persona))
    b = sorted(rel(nick, i["@id"]) for i in search_all(nick, persona))
    assert a == b, {"only in plone": sorted(set(a) - set(b)), "only in nick": sorted(set(b) - set(a))}


@needs_sites
@pytest.mark.parametrize("persona", ["admin", *SEARCH_PERSONAS], ids=lambda u: u or "anonymous")
def test_custom_workflow_guards(plone, nick, passwords, persona):
    """A custom workflow chain with guards (roles, a group, expressions), a
    variable and a second workflow survives: every persona is offered the
    same transitions on every item, and the history has the variable."""
    items = [i["@id"] for i in search_all(plone, portal_type=GUARDED_TYPE)]
    if not items:
        pytest.skip(f"the source has no {GUARDED_TYPE} (seeded before the custom workflow was added)")
    if persona not in (None, "admin"):
        nick.login(persona, passwords[persona])

    def offered(site, path):
        r = site.req("GET", f"{path}/@workflow", as_=persona, ok=None)
        if r.status_code != 200:
            return r.status_code, None, []
        data = r.json()
        ids = sorted(t["@id"].rsplit("/", 1)[-1] for t in data["transitions"])
        if site is plone and persona == "admin" and path.endswith("/own-item"):
            # admin owns the folders above; Plone inherits the Owner local
            # role, Nick gives Owner only to the owner of the item (N8)
            ids = [i for i in ids if i != "send"]
        return 200, data["state"]["id"], ids

    mismatches = []
    for url in items:
        path = rel(plone, url)
        mismatches += compare([(path, offered(plone, path), offered(nick, path))])
    assert not mismatches, mismatches

    if persona == "admin":
        def history(site, path):
            return [(h.get("action"), h.get("actor"), h.get("review_state"), h.get("approved_by", ""))
                    for h in site.get(f"{path}/@workflow")["history"]]
        assert history(plone, "en/guarded/done-item") == history(nick, "en/guarded/done-item")
        assert history(nick, "en/guarded/done-item")[-1] == ("approve", "reviewer1", "done", "reviewer1")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "seed":
        seed_plone(sys.argv[2], *(sys.argv[3:5] or ["admin", "admin"]))
        print("seeded", sys.argv[2])
    else:
        print(__doc__)
