# restapi-suite

Backend-agnostic, black-box tests for the [plone.restapi](https://github.com/plone/plone.restapi)
HTTP API. **Tests assert correct behaviour**: what Plone 6.2 does, unless that
is a bug. Then the test asserts what plone.restapi documents (or, where the
docs are silent, HTTP semantics: 400 for invalid input, 404 for missing
resources, 401/403 for refusals) and marks Plone with
`known_divergence("plone", kind="plone-bug")`. Plone behaviour that is wrong
but that clients (Volto) depend on stays asserted with `@pytest.mark.compat`.
The same tests run against Nick and, later, other backends. Everything goes
over HTTP, including seeding. No in-process Python is used.

```
pip install -e .                    # or: python3 -m venv .venv && .venv/bin/pip install -e .
pytest --target plone -n 8          # assertions against Plone 6.2
pytest --target nick  -n 8          # assertions against Nick
pytest --target nick --reference plone -n 6   # + differential diff of every request
restapi-suite-report results --out results/report.md
```

## Targets

Targets are defined in `restapi_suite/targets.toml`: base URL, admin
credentials, a logical type map (`document` -> `Document` on Plone, `Page` on
Nick), a logical role map (`member`, `site_admin`, `manager`), feature flags
and the sandbox container type.

| how | CLI | env |
|---|---|---|
| named target | `--target plone\|nick\|plone-volto` | `RESTAPI_TARGET` |
| override URL / admin | `--base-url --admin-user --admin-password` | `BASE_URL ADMIN_USER ADMIN_PASSWORD` |
| mappings for a bare URL | `--target-kind nick` (default `plone`) | `TARGET_KIND` |
| differential reference | `--reference plone` or `--reference-url URL` (+ `--reference-admin-user/-password`) | `REFERENCE REFERENCE_URL REFERENCE_ADMIN_USER REFERENCE_ADMIN_PASSWORD` |
| results dir / file stem | `--results-dir`, `--run-name` | `RESULTS_DIR RUN_NAME` |
| alternative targets file | `--targets-file` | `RESTAPI_TARGETS_FILE` |
| known divergences fail normally | `--strict-divergences` | `STRICT_DIVERGENCES=1` |

### Running targets

All containers, networks and volumes are prefixed `plonex-s4-` and bound to
127.0.0.1 on the docker host. By default the scripts use local docker; set
`DOCKER_HOST_SSH=<ssh alias>` to run them on a remote docker host and use
`targets/tunnel.sh` to forward the ports.

| target | container(s) | host port | URL (after tunnel) | admin |
|---|---|---|---|---|
| plone | `plonex-s4-plone` (plone/plone-backend:6.2, classic + `plone.restapi:default`) | 18640 | http://127.0.0.1:18640/plone | admin/admin |
| plone-volto | `plonex-s4-plone-volto` (distribution volto, for blocks) | 18643 | http://127.0.0.1:18643/plone | admin/admin |
| nick | `plonex-s4-nick` (node:26, copy of vendor/nick in `~/plonex-s4/nick`) + `plonex-s4-nick-pg` (postgres:18) on network `plonex-s4-net` | 18641 (pg: 18642) | http://127.0.0.1:18641 | admin/admin |

```
targets/plone-up.sh [classic|volto]   targets/plone-down.sh [classic|volto]
targets/nick-up.sh                    targets/nick-down.sh      # NICK_SRC=/path/to/patched/nick to test a patch
targets/tunnel.sh                     # ssh -fNT -L 18640:127.0.0.1:18640 -L 18641:... -L 18643:... -L 18642:... $DOCKER_HOST_SSH
```

Notes:
- Plone's classic distribution with `SETUP_CONTENT=false` does **not**
  install plone.restapi. Without the explicit profile, anonymous gets 401
  "Missing 'plone.restapi: Use REST API' permission".
- Nick runs with `API_RATE_LIMIT`/`AUTH_RATE_LIMIT=1000000`. The defaults (100 API and 5
  auth requests per 15 minutes) break any test run. Profiles are
  `core` + `examplecontent` (this provides the admin user).
- The scripts recreate the containers from scratch. Nick's DB volume is dropped too.
- `PREFIX`, `REMOTE_DIR` and the port variables in `common.sh` can be overridden
  to run a second, independent Nick (e.g. `PREFIX=plonex-n2 REMOTE_DIR=plonex-n2
  NICK_PORT=18821 NICK_PG_PORT=18822`); open your own tunnel for its ports.
- `NICK_EXTRA_PROFILES="nick-profiles/restapi_suite_acquire"` adds fixture
  profiles (directories relative to `targets/`) to the Nick copy and to its
  `config.ts`. `tests/test_workflow_acquire.py` needs that fixture (a global
  `Auditor` role with View, workflows with the `acquire`, `additive` and default
  permission models, and the types `AcquirePage`, `AdditivePage`, `DefaultPage`);
  it is Nick-only and skips itself without the fixture, against Plone and in
  differential mode, because Plone can't be given custom roles and workflows over HTTP.

## Writing tests

```python
import pytest
from restapi_suite.plugin import by_persona
from restapi_suite.assertions import DENIED, item_ids, same_outcome, err_message

pytestmark = pytest.mark.area("content")          # report grouping (default: module name)

def test_something(api, sandbox):
    path = api.doc(sandbox, "d", title="T")        # seed over HTTP (checked, runs on all sides)
    r = api.get(path, as_="member")                # probe (diffed in differential mode)
    assert r.status in DENIED, r                   # repr(r) shows method, URL, status, body

@by_persona(anonymous=401, member=401, local_editor=200, site_admin=200, manager=200)
def test_matrix_like(api, sandbox, persona, expected):
    assert same_outcome(expected, api.get(sandbox, as_=persona).status)
```

### Fixtures
- `api` (function): an `Api` facade. `api.get/post/patch/delete(path, as_=persona, json=..., params=...,
  headers=..., content=..., accept=..., compare=True, ignore=(), label="")` returns a
  `Resp` with `.status .headers .json .text .path .ref`. Paths are relative to the site root.
- `sandbox` (function): a fresh, uniquely named **private** folder
  (`rs-<worker>-<id>/rs-<test>-<hex>`), deleted with its content at teardown.
  The session persona `local_editor` has local Editor on it. It sits inside a
  **published** per-worker folder (`worker_root`), so a published sandbox can
  be reached by anyone.
- `personas` (session, per xdist worker): users `member`, `local_editor` (a
  member who gets local Editor in each sandbox), `site_admin`, `manager`. Same
  ids on target and reference. `as_` also accepts `"anonymous"`, `"admin"`
  (the configured admin) or a `User` from `api.create_user()`.
- `target`, `reference` (session): the resolved `Target` definitions.

### Seeding helpers (HTTP only; target status checked strictly; run on every side)
`api.create(parent, logical_type, id=, title=, **fields)`, `api.doc(...)`,
`api.folder(...)`, `api.transition(path, t, **body)`, `api.publish(path)`,
`api.state(path)`, `api.create_user(id=, roles=("member",))`,
`api.create_group(id=, roles=, users=)`, `api.add_to_group(g, u)`,
`api.share(path, principal, ["Editor"] | {"Editor": False}, type_="user", inherit=True)`,
`api.block_inheritance(path)`, `api.each(lambda side: ...)` for side-specific
payloads, and `api.defer(fn)` for custom cleanup. Users and groups created with
the helpers are deleted at teardown.

### Placeholders
Strings in paths, params, headers and JSON bodies can contain `{base}` (the
side's base URL) and `{type:<logical>}` (the side's portal_type). Each side
resolves them itself, so one request works on Plone and Nick:
`api.post(sandbox, json={"@type": "{type:document}", "title": "x"})`,
`{"source": f"{{base}}/{sandbox}/a"}`. Use `api.type("document")` in assertions.

### Conventions
- **Plone decides, unless it is a bug.** Write the test, run it against Plone,
  and encode what Plone does. If that looks wrong, decide which of these applies:
  - *Plone bug* (bad input answered with 500, a permission or locking hole,
    behaviour that contradicts the docs): assert the correct behaviour (docs
    first, then HTTP semantics), cite the source in the docstring, and add
    `@pytest.mark.known_divergence("plone", reason=..., kind="plone-bug", ref=...)`.
    Plone then shows XFAIL, and XPASS once it is fixed.
  - *Compat* (wrong, but a client depends on it): keep asserting Plone's
    behaviour and add `@pytest.mark.compat("why; evidence")`. The evidence is a
    `file:line` in the client's source (for example Volto 19). Without that
    evidence it is not compat; it is a Plone bug.
  - *Not a bug* on reflection (a deliberate Plone policy): assert it without a
    marker and explain it in the docstring. A backend that differs on purpose
    gets `kind="deliberate"`.
- **Permissions: assert the outcome, not the deny code.** Plone answers
  401 to authenticated users who lack permission, while Nick answers 403. Use
  `DENIED` / `same_outcome()`. The exact codes have their own tests
  (`test_unauthorized_authenticated_gets_401`, `test_get_private_as_anonymous_is_401`).
- Listings: Plone summaries have no `id`. Use `item_ids(json)`, which reads the last `@id` segment.
- Give explicit ids whenever a later step needs the path. Generated ids may
  differ between backends, and in differential mode both sides must stay in step.
- `compare=False` for requests that are setup or bookkeeping in differential mode.
- Concurrency tests: build extra `Side(api.t.target)` clients per thread and
  share the token (`side._tokens[...] = api.t.token(user)`). Logging in
  concurrently as one user is itself a tested Nick defect
  (`test_concurrent_logins_yield_valid_tokens`).
- Never mutate global state (registry, control panels, type schemas,
  workflows) without restoring it. Such tests must also be safe under
  `pytest -n`. If a test needs a fixed global name (for example a group called
  `Editor`), delete leftovers first (see `role_named_group`).
- Global state fixtures (`restapi_suite/services.py`, import them into the
  test module): `global_state` takes an exclusive cross-worker file lock and
  yields a helper; `global_state.controlpanel("security", {...})` PATCHes only
  the fields each side's panel schema knows and restores the old values at
  teardown, `global_state.set(path, body, restore)` / `.defer(fn)` cover the
  rest. Tests whose assertion depends on the default (for example "anonymous
  cannot register") take `default_global_state`, a shared lock, so they never
  run while a `global_state` test has changed something. Example:
  `tests/test_misc_services.py::test_self_registration_toggle_is_restored`.

### Markers
- `@pytest.mark.known_divergence("nick", reason="...", ref="A2#2", kind="bug"|"deliberate"|"plone-bug", ids=["local_editor"])`:
  turns the test into `xfail(strict=False)` on targets whose name or kind
  matches (`"plone"` matches the kind, so it covers `plone` and `plone-volto`).
  A fix then shows up as XPASS. `ids` limits it to parametrized
  ids (globs). Use `kind="bug"` for a known bug of the target, `kind="deliberate"`
  for design differences (for example Nick is headless) and `kind="plone-bug"`
  on `"plone"` for a Plone 6.2 bug the test asserts the correct behaviour for.
  Target-wide entries can also go in `restapi_suite/known_divergences.toml`.
- `@pytest.mark.compat("why; evidence")`: the test asserts Plone behaviour that
  is arguably wrong but that a client depends on. Its own category in the report.
- `@pytest.mark.target_only("nick", reason="...")`: the test applies only to the
  given targets (name or kind globs) and is skipped elsewhere. Its own category
  in the report (for example `tests/test_workflow_acquire.py`).
- `@pytest.mark.plone_bug("...")`: legacy informational note, still shown in
  the report; the suite no longer uses it.
- `@pytest.mark.requires("blocks")`: skip unless the target declares the feature in targets.toml.
- `@pytest.mark.area("types")`: report grouping.

## Workflow matrix (opt-in)

`tests/test_workflow_matrix.py` (marker `workflow_matrix`) checks, for every
standard Plone 6.2 workflow (simple_publication, one_state, intranet,
intranet_folder, folder, plone_workflow), every state, transition and role
(Anonymous, Member, Owner, Reader, Contributor, Editor, Reviewer, Site
Administrator, Manager), whether GET @workflow offers the transition, against
the matrix captured from Plone 6.2 in `tests/fixtures/workflow_matrix_plone62.json`
(792 cells). It is skipped unless `WORKFLOW_MATRIX_URL` is set:

```sh
WORKFLOW_MATRIX_URL=http://127.0.0.1:18640/plone WORKFLOW_MATRIX_KIND=plone pytest -m workflow_matrix tests/test_workflow_matrix.py
WORKFLOW_MATRIX_URL=http://127.0.0.1:18641 WORKFLOW_MATRIX_KIND=nick pytest -m workflow_matrix tests/test_workflow_matrix.py
```

On Plone the test creates a Dexterity type per workflow and assigns the
workflow with the types control panel form (there is no REST endpoint for
workflow chains), then removes the types, users and content again. Nick needs
the fixture profile `targets/nick-profiles/restapi_suite_workflows` (the same
definition.xml files, loaded by Nick's DCWorkflow loader):
`NICK_EXTRA_PROFILES="nick-profiles/restapi_suite_acquire nick-profiles/restapi_suite_workflows"`.
`python tests/test_workflow_matrix.py capture <plone-url> plone` rewrites the
fixture, `compare <url> <kind>` prints the match rate, and `generate <workflows
dir> <rolemap.xml>...` adds the comment workflows of plone.app.discussion (not
installed on the reference site) from DCWorkflow semantics, after checking the
generator against the captured workflows.

## Migration round trip (opt-in)

`tests/test_migration_roundtrip.py` (marker `migration`) checks that Nick's
`pnpm export` migrates a Plone 6.2 site without loss. It needs a freshly
seeded Plone and a fresh Nick that imported the exported profile, so it is
skipped unless `MIGRATION_SOURCE_URL` and `MIGRATION_TARGET_URL` are set.

```
python tests/test_migration_roundtrip.py seed http://127.0.0.1:18700/plone   # empty volto site, admin/admin
(cd nick && pnpm export http://127.0.0.1:18700/plone admin admin migrated)
# fresh Nick with profiles core + migrated (config.ts), then:
MIGRATION_SOURCE_URL=http://127.0.0.1:18700/plone MIGRATION_TARGET_URL=http://127.0.0.1:18701 \
MIGRATION_TARGET_USERS=nick/src/profiles/migrated/users.json \
  pytest -m migration tests/test_migration_roundtrip.py --target plone
```

The seed covers folders, pages, news, events (intranet workflow), files and
images, private/pending/published items with workflow history and comments
on transitions, versioned edits, a rename and manual aliases, local roles
for users and groups, a folder that blocks inheritance, relations, teaser,
image and listing blocks, comments with a reply, en/de translations, 130
archive pages (more than one export batch), users and groups. The test
compares item by item: tree, UUIDs, states, field values, folder order,
relations, workflow history, versions, local roles and the inherit flag,
blobs (byte equality), translations, redirects, comments, users and groups,
workflow transitions, `@search` as admin, index queries, and
permission-filtered `@search` for anonymous and five users. Migrated users
get random passwords, so `MIGRATION_TARGET_USERS` points at the exported
`users.json` to log in on Nick (the Plone password must not work there).

The seed adds a type `guarded_item` with a custom workflow chain
(`tests/fixtures/roundtrip_workflows`: guards with roles, a group and
expressions, a variable, a worklist, a second workflow), imported with
`portal_setup/manage_importTarball`; `test_custom_workflow_guards` compares
the transitions every persona is offered on those items. It skips on sources
seeded before (like `plonex-s2-plone`).

## Differential mode

With `--reference`, every `api` request goes to both backends, so their state
changes identically. The target response is returned and asserted. The
pair is normalised and diffed with the comparator vendored from the S1
conformance harness (`restapi_suite/compare.py`, plus `default_rules.json`
copied unchanged and `suite_rules.json`, which adds suite rules). Normalisation:
each side's base URL becomes `http://localhost:55001/plone`, portal_types become
logical names, UUIDs/datetimes/JWTs/lock tokens are masked, and role and
permission lists are sorted. Divergences are **recorded, never raised**:
`results/<target>-vs-<ref>.jsonl` → "Differential" section of the report.
Override the rules with `RESTAPI_RULES=path.json`; suppress per request with
`ignore=("@components.*",)`.

## Results and report

Each run writes `results/<run>.jsonl` (one line per test: outcome, message,
area, category, known divergence, kind, compat note, differential data) and
`<run>.meta.json`. `restapi-suite-report [dirs|files] --out report.md`
renders the run summary, pass counts per category, per-area pass counts,
failures, known divergences and compat notes, a per-test outcome matrix across
runs, and the differential divergences.

Categories (one per test): **correct behaviour** (the default; a Plone bug
counts as not passing on Plone), **compat** (`@pytest.mark.compat`) and
**target-only** (`@pytest.mark.target_only`). Skips are counted separately.

## Gotchas found while building this
- `@login` sets an auth cookie on both backends. The client never stores
  cookies; otherwise "anonymous" requests are silently authenticated
  (`test_anonymous_is_really_anonymous` guards this).
- Plone under `-n 8` raises ZODB ConflictErrors (500 "database conflict
  error") when many requests touch one container. The client retries those
  (the transaction was aborted, so this is safe). Nothing else is retried.
- Nick deadlocks on `position_in_parent` updates when siblings are
  created or deleted in parallel. That is why sandboxes live in a per-worker folder.
- `plone-volto` only allows Documents at the site root (`sandbox_type = "document"`).
  Run only `-k blocks` there.

## Adding a service area
1. `tests/test_<area>.py` with `pytestmark = pytest.mark.area("<area>")`.
2. Read `docs/source/endpoints/<area>.md`, `services/<area>/` and the
   `http-examples/<area>_*` pairs. Turn each into a self-contained test that
   seeds its own state in `sandbox`.
3. Add probes for edges: missing or extra fields, wrong types, unknown ids, persona
   matrix, inherited/blocked local roles, and concurrent sequences.
4. Run against Plone until it is green (or Plone bugs are marked), then against Nick. Mark deliberate or
   understood divergences with `known_divergence`. Leave Nick bugs failing,
   or mark them `kind="bug"` with a reference.
5. Add any new helper to `Api` and document it here.

The comparator is vendored from plonex `tools/conformance/conformance/compare.py` (task S1), unchanged except for its header.

## License

GPL version 2, like plone.restapi, whose documentation and HTTP examples the
tests are based on. Copyrights hold Plone Foundation.
See [LICENSE.md](LICENSE.md) and [LICENSE.GPL](LICENSE.GPL) for details.

The workflow definitions in
`targets/nick-profiles/restapi_suite_workflows/workflows/wfmatrix_*/definition.xml`
are copied unchanged from Products.CMFPlone (`profiles/default/workflows/`),
GPL-2.0-only, Copyright Plone Foundation.
