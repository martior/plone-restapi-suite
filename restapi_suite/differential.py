"""Differential mode: normalise a (reference, target) response pair and diff it
with the vendored S1 comparator (compare.py). Divergences are recorded, never
raised.

Canonicalisation per side, before the S1 rules run:
  1. the side's base URL -> the S1 canonical base (http://localhost:55001/plone),
     so the S1 default rules (UUIDs, datetimes, JWTs, ...) apply unchanged;
  2. portal_type values (keys "@type", "portal_type", "type" in @types items)
     -> logical type names from targets.toml, so Page (Nick) == Document (Plone).
"""

from __future__ import annotations

import json
import os
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from . import compare
from .client import Resp

SUITE_RULES = Path(__file__).with_name("suite_rules.json")
_RULES: compare.Rules | None = None
TYPE_KEYS = {"@type", "portal_type"}
MAX_DIFFS = 40


def rules() -> compare.Rules:
    global _RULES
    if _RULES is None:
        _RULES = compare.Rules.load(os.environ.get("RESTAPI_RULES") or SUITE_RULES)
    return _RULES


def _map_types(v: Any, side) -> Any:
    if isinstance(v, dict):
        return {
            k: (side.target.logical_type(x) if k in TYPE_KEYS and isinstance(x, str) else _map_types(x, side))
            for k, x in v.items()
        }
    if isinstance(v, list):
        return [_map_types(x, side) for x in v]
    return v


def canonical(resp: Resp) -> Any:
    r = rules()
    side = resp.side
    if resp.is_json and resp.json is not None:
        body: Any = compare.rewrite_base_url(resp.json, side.base, r.canonical_base_url)
        body = _map_types(body, side)
        return compare.normalise(body, r)
    txt = compare.rewrite_base_url(resp.text, side.base, r.canonical_base_url)
    return compare.normalise_text(txt, r)


def diff_pair(ref: Resp, tgt: Resp, ignore: tuple[str, ...] = ()) -> list[dict]:
    out: list[compare.Divergence] = []
    if ref.status != tgt.status:
        out.append(compare.Divergence(compare.STATUS, "<status>", ref.status, tgt.status))
    ct = lambda x: (x.headers.get("content-type") or "").split(";")[0].strip().lower()  # noqa: E731
    if ct(ref) != ct(tgt):
        out.append(compare.Divergence(compare.HEADER, "<header:content-type>", ct(ref), ct(tgt)))
    a, b = canonical(ref), canonical(tgt)
    if isinstance(a, str) or isinstance(b, str):
        if a != b:
            out.append(compare.Divergence(compare.BODY, "<body>", str(a)[:300], str(b)[:300]))
    else:
        out.extend(compare.diff(a, b))
    if ignore:
        out = [d for d in out if not any(fnmatchcase(d.path, g) for g in ignore)]
    return [d.to_dict(200) for d in out[:MAX_DIFFS]] + (
        [{"kind": "truncated", "path": f"+{len(out) - MAX_DIFFS} more", "expected": None, "actual": None}]
        if len(out) > MAX_DIFFS
        else []
    )


class Recorder:
    """Collects exchanges for one test; the plugin stores them in the results."""

    def __init__(self):
        self.steps = 0
        self.divergences: list[dict] = []

    def record(self, label: str, method: str, path: str, persona: str, ref: Resp, tgt: Resp, ignore=()):
        self.steps += 1
        diffs = diff_pair(ref, tgt, tuple(ignore))
        if diffs:
            self.divergences.append(
                {
                    "step": self.steps,
                    "label": label,
                    "request": f"{method} {path}",
                    "as": persona,
                    "status": [ref.status, tgt.status],
                    "diffs": diffs,
                }
            )

    def to_json(self) -> str:
        return json.dumps({"compared": self.steps, "divergences": self.divergences}, ensure_ascii=False)
