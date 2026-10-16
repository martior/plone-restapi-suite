"""pytest plugin: target selection, fixtures, markers and the results file.

Loaded via `-p restapi_suite.plugin` (pyproject addopts).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import time
import tomllib
import warnings
from pathlib import Path

import pytest

from . import targets as _targets
from .api import PERSONAS, Api, Personas, uid
from .cleanup import delete_retry
from .client import Side
from .differential import Recorder

KNOWN_FILE = Path(__file__).with_name("known_divergences.toml")

OPTS = [
    # flag, env, help
    ("--target", "RESTAPI_TARGET", "target name from targets.toml (plone, nick, plone-volto)"),
    ("--target-kind", "TARGET_KIND", "definition supplying type/role mappings when only --base-url is given"),
    ("--base-url", "BASE_URL", "override target base URL"),
    ("--admin-user", "ADMIN_USER", "override target admin user"),
    ("--admin-password", "ADMIN_PASSWORD", "override target admin password"),
    ("--reference", "REFERENCE", "differential mode: reference target name (e.g. plone)"),
    ("--reference-url", "REFERENCE_URL", "differential mode: reference base URL (implies --reference plone)"),
    ("--reference-admin-user", "REFERENCE_ADMIN_USER", "reference admin user"),
    ("--reference-admin-password", "REFERENCE_ADMIN_PASSWORD", "reference admin password"),
    ("--results-dir", "RESULTS_DIR", "where <run>.jsonl results go (default ./results)"),
    ("--targets-file", "RESTAPI_TARGETS_FILE", "alternative targets.toml"),
    ("--run-name", "RUN_NAME", "results file stem (default: target or target-vs-reference)"),
]
DIVERGENCE_KINDS = ("bug", "deliberate", "plone-bug")


def _strict(config) -> bool:
    return bool(config.getoption("--strict-divergences") or os.environ.get("STRICT_DIVERGENCES"))


def pytest_addoption(parser):
    g = parser.getgroup("restapi-suite")
    for flag, env, help_ in OPTS:
        g.addoption(flag, default=None, help=f"{help_} [env {env}]")
    g.addoption("--strict-divergences", action="store_true", default=False,
                help="do not turn known divergences into xfail (they fail normally) [env STRICT_DIVERGENCES]")


def _opt(config, flag):
    env = dict((f, e) for f, e, _ in OPTS)[flag]
    return config.getoption(flag) or os.environ.get(env) or None


def _resolve(config):
    tf = _opt(config, "--targets-file")
    base = _opt(config, "--base-url")
    name = _opt(config, "--target")
    kind = _opt(config, "--target-kind")
    if name is None and base is None:
        name = "plone"
    tgt = _targets.resolve(name, base, _opt(config, "--admin-user"), _opt(config, "--admin-password"), kind, tf)
    ref = None
    rname, rurl = _opt(config, "--reference"), _opt(config, "--reference-url")
    if rname or rurl:
        ref = _targets.resolve(rname or "plone", rurl, _opt(config, "--reference-admin-user"),
                               _opt(config, "--reference-admin-password"), "plone" if not rname else None, tf)
        ref.name = ref.name if rname else "plone"
    return tgt, ref


def pytest_configure(config):
    tgt, ref = _resolve(config)
    config._rs_target, config._rs_reference = tgt, ref
    run = _opt(config, "--run-name") or (f"{tgt.name}-vs-{ref.name}" if ref else tgt.name)
    rdir = Path(_opt(config, "--results-dir") or "results")
    config._rs_results = rdir / f"{run}.jsonl"
    config._rs_run = run
    if not hasattr(config, "workerinput"):  # controller / single process
        rdir.mkdir(parents=True, exist_ok=True)
        config._rs_results.write_text("")
        (rdir / f"{run}.meta.json").write_text(json.dumps({
            "run": run, "target": tgt.name, "kind": tgt.kind, "base_url": tgt.base_url,
            "reference": ref.name if ref else None, "reference_url": ref.base_url if ref else None,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=1))


def pytest_report_header(config):
    t, r = config._rs_target, config._rs_reference
    s = f"restapi-suite target: {t.name} ({t.kind}) {t.base_url}"
    if r:
        s += f" | reference: {r.name} {r.base_url} (differential)"
    return s


# -- markers / known divergences ---------------------------------------

def _known_entries():
    if not KNOWN_FILE.exists():
        return []
    return tomllib.loads(KNOWN_FILE.read_text()).get("divergence", [])


def _matches_target(spec: str, tgt) -> bool:
    return any(fnmatch.fnmatchcase(x, spec) for x in (tgt.name, tgt.kind))


def pytest_collection_modifyitems(config, items):
    tgt = config._rs_target
    known = _known_entries()
    for item in items:
        reasons, kinds = [], []
        for m in item.iter_markers("known_divergence"):
            spec = m.args[0] if m.args else m.kwargs.get("target", "*")
            ids = m.kwargs.get("ids")
            if not _matches_target(spec, tgt):
                continue
            if ids and not any(fnmatch.fnmatchcase(getattr(item, "callspec", None) and item.callspec.id or "", i)
                               for i in ids):
                continue
            kind = m.kwargs.get("kind", "bug")
            if kind not in DIVERGENCE_KINDS:
                raise ValueError(f"{item.nodeid}: known_divergence kind must be one of {DIVERGENCE_KINDS}")
            reasons.append(m.kwargs.get("reason") or (m.args[1] if len(m.args) > 1 else "known divergence"))
            kinds.append(kind)
            if m.kwargs.get("ref"):
                reasons[-1] += f" [{m.kwargs['ref']}]"
        for k in known:
            if _matches_target(k.get("target", "*"), tgt) and fnmatch.fnmatchcase(item.nodeid, k["test"]):
                reasons.append(k["reason"] + (f" [{k['ref']}]" if k.get("ref") else ""))
                kinds.append(k.get("kind", "bug"))
        if reasons:
            if not _strict(config):
                item.add_marker(pytest.mark.xfail(reason="; ".join(reasons), strict=False))
            item.user_properties.append(("known_divergence", "; ".join(reasons)))
            item.user_properties.append(("divergence_kind", ",".join(sorted(set(kinds)))))
        for m in item.iter_markers("plone_bug"):  # legacy, informational
            item.user_properties.append(("plone_bug", m.args[0] if m.args else m.kwargs.get("reason", "")))
        compat = [m.args[0] if m.args else m.kwargs.get("reason", "") for m in item.iter_markers("compat")]
        if compat:
            item.user_properties.append(("compat", "; ".join(compat)))
        only = [m for m in item.iter_markers("target_only")]
        for m in only:
            if not any(_matches_target(spec, tgt) for spec in m.args):
                why = m.kwargs.get("reason") or f"only runs against {', '.join(m.args)}"
                item.add_marker(pytest.mark.skip(reason=f"target-only: {why}"))
        item.user_properties.append(("category", "target-only" if only else "compat" if compat else "correct"))
        for m in item.iter_markers("requires"):
            missing = [f for f in m.args if f not in tgt.features]
            if missing:
                item.add_marker(pytest.mark.skip(reason=f"target {tgt.name} lacks feature(s) {missing}"))
        area = next((m.args[0] for m in item.iter_markers("area")), None)
        item.user_properties.append(("area", area or re.sub(r"^test_", "", item.module.__name__.split(".")[-1])))


# -- fixtures ------------------------------------------------------------

@pytest.fixture(scope="session")
def target(pytestconfig):
    return pytestconfig._rs_target


@pytest.fixture(scope="session")
def reference(pytestconfig):
    return pytestconfig._rs_reference


@pytest.fixture(scope="session")
def sides(target, reference):
    ss = [Side(target, "target")] + ([Side(reference, "reference")] if reference else [])
    yield ss
    for s in ss:
        s.close()


@pytest.fixture(scope="session")
def personas(sides):
    p = Personas(sides).setup()
    yield p
    p.teardown()


@pytest.fixture
def api(request, sides, personas):
    rec = Recorder()
    a = Api(sides[0], sides[1] if len(sides) > 1 else None, personas, rec)
    yield a
    a.cleanup()
    if a.differential:
        request.node.user_properties.append(("differential", json.loads(rec.to_json())))


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40].strip("-")


@pytest.fixture(scope="session")
def worker_root(sides, personas):
    """Per-worker published parent folder for all sandboxes of this worker.

    Keeps parallel workers from creating/deleting siblings of each other:
    Nick deadlocks on position_in_parent updates and Plone raises ZODB
    ConflictErrors when many workers touch the same container. Published
    so every persona can traverse into published sandboxes."""
    wid = os.environ.get("PYTEST_XDIST_WORKER", "main")
    fid = f"rs-{wid}-{uid(6)}"
    a = Api(sides[0], sides[1] if len(sides) > 1 else None, personas)
    a.create("", sides[0].target.sandbox_type, id=fid, title=f"restapi-suite worker {wid}")
    a.publish(fid)
    yield fid
    for side in a.sides:
        if delete_retry(side, fid) not in (204, 404):
            warnings.warn(f"[{side.target.name}] could not delete worker folder {fid}; "
                          "run python -m restapi_suite.cleanup")


@pytest.fixture
def sandbox(request, api, personas, worker_root):
    """A uniquely named private folder for this test (inside the published
    per-worker folder). Returns its path relative to the site root.

    The session persona `local_editor` gets the local role Editor on it.
    Deleted (with everything inside) at teardown."""
    fid = f"{worker_root}/rs-{_slug(request.node.name)}-{uid(6)}"
    api.create(worker_root, api.target.sandbox_type, id=fid.rsplit("/", 1)[1], title=f"Sandbox {request.node.name}")
    api.share(fid, personas.users["local_editor"].id, ["Editor"])
    yield fid
    for side in api.sides:
        if delete_retry(side, fid) not in (204, 404):
            warnings.warn(f"[{side.target.name}] could not delete sandbox {fid}")


# -- results ---------------------------------------------------------------

def _first_line(report) -> str:
    try:
        crash = report.longrepr.reprcrash  # type: ignore[union-attr]
        return crash.message.splitlines()[0][:500] if crash else ""
    except AttributeError:
        return str(report.longrepr)[:500] if report.longrepr else ""


_STATE: dict[str, dict] = {}


def pytest_runtest_logreport(report):
    config = _CONFIG.get("c")
    if config is None or hasattr(config, "workerinput"):
        return
    st = _STATE.setdefault(report.nodeid, {"nodeid": report.nodeid, "outcome": None, "message": "", "duration": 0.0})
    st["duration"] += report.duration
    for k, v in report.user_properties:
        st[k] = v
    if report.when == "setup":
        if report.failed:
            st["outcome"], st["message"] = "error", _first_line(report)
        elif report.skipped:
            if hasattr(report, "wasxfail"):
                st["outcome"], st["message"] = "xfailed", report.wasxfail
            else:
                st["outcome"] = "skipped"
                lr = report.longrepr
                st["message"] = lr[2] if isinstance(lr, tuple) else str(lr)[:300]
    elif report.when == "call":
        if hasattr(report, "wasxfail"):
            st["outcome"] = "xfailed" if report.skipped else "xpassed"
            st["message"] = (_first_line(report) + " | " if report.skipped else "") + report.wasxfail
        elif report.passed:
            st["outcome"] = "passed"
        elif report.failed:
            st["outcome"], st["message"] = "failed", _first_line(report)
        elif report.skipped:  # pytest.skip() inside the test body
            lr = report.longrepr
            st["outcome"], st["message"] = "skipped", lr[2] if isinstance(lr, tuple) else str(lr)[:300]
    elif report.when == "teardown":
        if report.failed and st["outcome"] in (None, "passed"):
            st["outcome"], st["message"] = "error", "teardown: " + _first_line(report)
        _write(config, _STATE.pop(report.nodeid))


_CONFIG: dict = {}


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    _CONFIG["c"] = session.config


def _write(config, st):
    t, r = config._rs_target, config._rs_reference
    st.update(run=config._rs_run, target=t.name, reference=r.name if r else None)
    with open(config._rs_results, "a") as f:
        f.write(json.dumps(st, ensure_ascii=False) + "\n")


# helper for tests -----------------------------------------------------------

def by_persona(**expected):
    """@by_persona(anonymous=401, member=403, ...) -> parametrize(persona, expected)."""
    unknown = set(expected) - set(PERSONAS)
    if unknown:
        raise ValueError(f"unknown personas {unknown}")
    return pytest.mark.parametrize("persona,expected", list(expected.items()), ids=list(expected))
