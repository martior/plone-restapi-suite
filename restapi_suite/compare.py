"""JSON-aware comparator with explicit, configurable normalisation rules.

VENDORED from the plonex S1 conformance harness
(tools/conformance/conformance/compare.py in the plonex discovery repo, S1 task),
unchanged except for this header, so that restapi-suite is self-contained.
Only Rules / normalise / diff / rewrite_base_url are used by the suite;
compare_response is kept for parity with the original.


Pipeline for each fixture (see Rules for the knobs):
  1. base-URL rewrite: the target's base URL in the *actual* response is
     replaced by the fixture's canonical base (http://localhost:55001/plone).
  2. ignore_paths: values at matching JSON paths become "<IGNORED>" on both sides.
  3. patterns: regex substitutions on every string value (both sides), each
     optionally scoped with include_paths / exclude_paths.
  4. unordered_lists: lists at matching paths are sorted (canonical JSON)
     before comparison.
  5. structural diff -> Divergence(kind, path, expected, actual).

Paths look like `items[*].@id` (list indices are rendered as [*] for
matching, concrete indices are kept in the reported path). Path globs:
`*` matches anything including dots, brackets are literal (see _path_glob).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

DEFAULT_RULES_FILE = Path(__file__).with_name("default_rules.json")

# Divergence kinds
STATUS = "status_mismatch"
HEADER = "header_mismatch"
MISSING = "missing_key"
EXTRA = "extra_key"
VALUE = "value_mismatch"
NON_JSON = "non_json"
BODY = "body_mismatch"  # non-JSON bodies differ, or body present/absent
KINDS = [STATUS, HEADER, MISSING, EXTRA, VALUE, NON_JSON, BODY]


@dataclass
class Divergence:
    kind: str
    path: str
    expected: Any = None
    actual: Any = None

    def to_dict(self, max_len: int = 300) -> dict:
        d = asdict(self)
        for k in ("expected", "actual"):
            s = json.dumps(d[k], sort_keys=True, ensure_ascii=False)
            if len(s) > max_len:
                d[k] = s[:max_len] + "...<truncated>"
        return d


@dataclass
class Pattern:
    name: str
    regex: str
    replace: str
    include_paths: list[str] = field(default_factory=lambda: ["*"])
    exclude_paths: list[str] = field(default_factory=list)
    fixtures: str = "*"

    def __post_init__(self):
        self._re = re.compile(self.regex)


@dataclass
class Rules:
    canonical_base_url: str = "http://localhost:55001/plone"
    patterns: list[Pattern] = field(default_factory=list)
    # [{"fixtures": glob, "path": glob}]
    ignore_paths: list[dict] = field(default_factory=list)
    unordered_lists: list[dict] = field(default_factory=list)
    compare_headers: list[str] = field(default_factory=lambda: ["content-type", "location"])
    set_valued_headers: list[str] = field(default_factory=lambda: ["allow"])

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Rules":
        data = json.loads(Path(path or DEFAULT_RULES_FILE).read_text())
        data.pop("_doc", None)
        pats = [Pattern(**{k: v for k, v in p.items() if k != "_doc"}) for p in data.pop("patterns", [])]
        return cls(patterns=pats, **data)

    def for_fixture(self, name: str) -> "Rules":
        def sel(items):
            return [i for i in items if fnmatchcase(name, i.get("fixtures", "*"))]

        return Rules(
            canonical_base_url=self.canonical_base_url,
            patterns=[p for p in self.patterns if fnmatchcase(name, p.fixtures)],
            ignore_paths=sel(self.ignore_paths),
            unordered_lists=sel(self.unordered_lists),
            compare_headers=self.compare_headers,
            set_valued_headers=self.set_valued_headers,
        )


_GLOB_CACHE: dict[str, re.Pattern] = {}


def _path_glob(g: str) -> re.Pattern:
    """Path glob: '*' matches anything (dots included), '?' one char, and
    brackets are literal so 'items[*].x' means 'any index of items'."""
    if g not in _GLOB_CACHE:
        parts = re.escape(g.replace("[*]", "QQIDXQQ")).replace("\\*", ".*").replace("\\?", ".")
        _GLOB_CACHE[g] = re.compile(parts.replace("QQIDXQQ", r"\[\*\]") + r"\Z", re.S)
    return _GLOB_CACHE[g]


def _match(path: str, globs) -> bool:
    return any(_path_glob(g).match(path) for g in globs)


def _glob_path(path: str) -> str:
    return re.sub(r"\[\d+\]", "[*]", path)


def _join(path: str, key) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else str(key)


def rewrite_base_url(value: Any, target_base: str, canonical_base: str) -> Any:
    """Replace target base URL with the canonical one in all strings."""
    target_base = target_base.rstrip("/")
    canonical_base = canonical_base.rstrip("/")
    if target_base == canonical_base:
        return value
    if isinstance(value, str):
        return value.replace(target_base, canonical_base)
    if isinstance(value, list):
        return [rewrite_base_url(v, target_base, canonical_base) for v in value]
    if isinstance(value, dict):
        return {k: rewrite_base_url(v, target_base, canonical_base) for k, v in value.items()}
    return value


def normalise(value: Any, rules: Rules, path: str = "") -> Any:
    gp = _glob_path(path)
    if path and _match(gp, [i["path"] for i in rules.ignore_paths]):
        return "<IGNORED>"
    if isinstance(value, dict):
        return {k: normalise(v, rules, _join(path, k)) for k, v in value.items()}
    if isinstance(value, list):
        items = [normalise(v, rules, _join(path, i)) for i, v in enumerate(value)]
        if _match(gp, [u["path"] for u in rules.unordered_lists]):
            items.sort(key=lambda x: json.dumps(x, sort_keys=True))
        return items
    if isinstance(value, str):
        for p in rules.patterns:
            if _match(gp, p.include_paths) and not _match(gp, p.exclude_paths):
                value = p._re.sub(p.replace, value)
        return value
    return value


def normalise_text(text: str, rules: Rules) -> str:
    for p in rules.patterns:
        if "*" in p.include_paths and not p.exclude_paths:
            text = p._re.sub(p.replace, text)
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def diff(expected: Any, actual: Any, path: str = "") -> list[Divergence]:
    out: list[Divergence] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in expected:
            if k not in actual:
                out.append(Divergence(MISSING, _join(path, k), expected[k], None))
            else:
                out.extend(diff(expected[k], actual[k], _join(path, k)))
        for k in actual:
            if k not in expected:
                out.append(Divergence(EXTRA, _join(path, k), None, actual[k]))
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        for i in range(max(len(expected), len(actual))):
            p = _join(path, i)
            if i >= len(actual):
                out.append(Divergence(MISSING, p, expected[i], None))
            elif i >= len(expected):
                out.append(Divergence(EXTRA, p, None, actual[i]))
            else:
                out.extend(diff(expected[i], actual[i], p))
        return out
    # bool is an int subclass in Python; keep True != 1
    if type(expected) is not type(actual) and not (
        isinstance(expected, (int, float))
        and isinstance(actual, (int, float))
        and not isinstance(expected, bool)
        and not isinstance(actual, bool)
    ):
        return [Divergence(VALUE, path or "$", expected, actual)]
    if expected != actual:
        return [Divergence(VALUE, path or "$", expected, actual)]
    return []


def _media_type(v: str | None) -> str | None:
    return v.split(";")[0].strip().lower() if v else v


def compare_response(
    name: str,
    expected,  # fixtures.Response
    status: int,
    headers: dict[str, str],  # lower-cased keys
    body: bytes,
    rules: Rules,
    target_base: str,
) -> list[Divergence]:
    r = rules.for_fixture(name)
    out: list[Divergence] = []
    if expected.status != status:
        out.append(Divergence(STATUS, "<status>", expected.status, status))

    for h in r.compare_headers:
        e = expected.header(h)
        if e is None:
            continue  # fixtures only record a subset; absent means "not asserted"
        a = headers.get(h)
        if h == "content-type":
            e, a = _media_type(e), _media_type(a)
        elif a is not None:
            a = rewrite_base_url(a, target_base, r.canonical_base_url)
            e, a = normalise(e, r, f"<header:{h}>"), normalise(a, r, f"<header:{h}>")
        if e != a:
            out.append(Divergence(HEADER, f"<header:{h}>", e, a))
    for h in r.set_valued_headers:
        e = expected.header(h)
        if e is None:
            continue
        es = {x.strip() for x in e.split(",")}
        as_ = {x.strip() for x in (headers.get(h) or "").split(",") if x.strip()}
        if es != as_:
            out.append(Divergence(HEADER, f"<header:{h}>", sorted(es), sorted(as_)))

    exp_body = expected.body.strip()
    act_text = body.decode("utf-8", errors="replace")
    if not exp_body:
        if body.strip():
            out.append(Divergence(BODY, "<body>", "", act_text[:300]))
        return out
    if expected.is_json:
        try:
            exp_json = expected.json()
        except ValueError:
            # A handful of fixtures declare JSON but contain something else.
            exp_json = None
        if exp_json is not None:
            try:
                act_json = json.loads(act_text)
            except ValueError:
                out.append(Divergence(NON_JSON, "<body>", "<json>", act_text[:300]))
                return out
            act_json = rewrite_base_url(act_json, target_base, r.canonical_base_url)
            out.extend(diff(normalise(exp_json, r), normalise(act_json, r)))
            return out
    e_txt = normalise_text(expected.body.decode("utf-8", errors="replace"), r)
    a_txt = normalise_text(rewrite_base_url(act_text, target_base, r.canonical_base_url), r)
    if e_txt != a_txt:
        out.append(Divergence(BODY, "<body>", e_txt[:300], a_txt[:300]))
    return out
