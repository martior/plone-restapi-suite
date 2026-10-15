"""Markdown report from one or more results/<run>.jsonl files.

    restapi-suite-report [results-dir-or-files ...] [--out report.md] [--details N]

Sections: run summary, per-area pass counts, per-test outcome matrix, failure
messages, known divergences / Plone-bug notes, differential divergences.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SYM = {"passed": "PASS", "failed": "FAIL", "error": "ERR", "skipped": "skip", "xfailed": "XFAIL",
       "xpassed": "XPASS", None: "?"}


def load(paths: list[str]) -> dict[str, list[dict]]:
    files: list[Path] = []
    for p in paths or ["results"]:
        pp = Path(p)
        files.extend(sorted(pp.glob("*.jsonl")) if pp.is_dir() else [pp])
    runs: dict[str, list[dict]] = {}
    for f in files:
        rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        if rows:
            runs[f.stem] = rows
    return runs


def esc(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def pass_rate(rows) -> str:
    c = Counter(r["outcome"] for r in rows)
    denom = len(rows) - c["skipped"]
    return f"{(c['passed'] + c['xpassed']) / denom * 100:.1f}%" if denom else "n/a"


def summary(runs) -> list[str]:
    out = ["## Runs", "",
           "| run | target | reference | tests | passed | failed | error | xfailed (bug / deliberate / plone-bug) "
           "| xpassed | skipped | pass rate |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, rows in runs.items():
        c = Counter(r["outcome"] for r in rows)
        kinds = Counter(r.get("divergence_kind", "bug") for r in rows if r["outcome"] == "xfailed")
        xf = f"{c['xfailed']} ({kinds.get('bug', 0)} / {kinds.get('deliberate', 0)} / {kinds.get('plone-bug', 0)})"
        out.append(f"| {name} | {rows[0].get('target')} | {rows[0].get('reference') or '-'} | {len(rows)} | "
                   f"{c['passed']} | {c['failed']} | {c['error']} | {xf} | {c['xpassed']} | {c['skipped']} | "
                   f"{pass_rate(rows)} |")
    out += ["", "pass rate = (passed + xpassed) / (tests - skipped); xfailed counts as not passing.", ""]
    return out


def per_area(runs) -> list[str]:
    names = list(runs)
    areas: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for n, rows in runs.items():
        for r in rows:
            areas[r.get("area", "?")][n][r["outcome"]] += 1
    out = ["## Per area (passed / tests, excluding skipped)", "",
           "| area | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for a in sorted(areas):
        cells = []
        for n in names:
            c = areas[a][n]
            tot = sum(c.values()) - c["skipped"]
            cells.append(f"{c['passed'] + c['xpassed']} / {tot}" if tot else "-")
        out.append(f"| {a} | " + " | ".join(cells) + " |")
    return out + [""]


def matrix(runs) -> list[str]:
    names = list(runs)
    by_test: dict[str, dict[str, dict]] = defaultdict(dict)
    for n, rows in runs.items():
        for r in rows:
            by_test[r["nodeid"]][n] = r
    out = ["## Per test", "", "<details><summary>outcome per test and run</summary>", "",
           "| test | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for t in sorted(by_test):
        cells = [SYM.get(by_test[t].get(n, {}).get("outcome"), "-") if n in by_test[t] else "-" for n in names]
        out.append(f"| `{esc(t.replace('tests/', ''))}` | " + " | ".join(cells) + " |")
    return out + ["", "</details>", ""]


def failures(runs) -> list[str]:
    out = ["## Failures", ""]
    for n, rows in runs.items():
        bad = [r for r in rows if r["outcome"] in ("failed", "error")]
        out += [f"### {n}: {len(bad)} failing", ""]
        if bad:
            out += ["| test | message |", "|---|---|"]
            out += [f"| `{esc(r['nodeid'].split('::', 1)[1])}` | {esc(r['message'][:220])} |" for r in bad]
        out.append("")
    return out


def notes(runs) -> list[str]:
    out = ["## Known divergences and Plone-bug notes", ""]
    seen: dict[tuple, set] = defaultdict(set)
    for n, rows in runs.items():
        for r in rows:
            if r.get("known_divergence"):
                seen[(r["nodeid"], r.get("divergence_kind", "bug"), r["known_divergence"])].add(
                    f"{n}:{SYM[r['outcome']]}")
    if seen:
        out += ["| test | kind | reason | runs |", "|---|---|---|---|"]
        for (t, k, why), where in sorted(seen.items()):
            out.append(f"| `{esc(t.split('::', 1)[1])}` | {k} | {esc(why)} | {', '.join(sorted(where))} |")
    pb = {(r["nodeid"], r["plone_bug"]) for rows in runs.values() for r in rows if r.get("plone_bug")}
    if pb:
        out += ["", "Tests that encode Plone 6.2 behaviour which looks like a bug (`@pytest.mark.plone_bug`):", "",
                "| test | note |", "|---|---|"]
        out += [f"| `{esc(t.split('::', 1)[1])}` | {esc(why)} |" for t, why in sorted(pb)]
    return out + [""]


def differential(runs, details: int) -> list[str]:
    out = []
    for n, rows in runs.items():
        diffs = [r for r in rows if r.get("differential")]
        if not diffs:
            continue
        compared = sum(r["differential"]["compared"] for r in diffs)
        steps = [(r, d) for r in diffs for d in r["differential"]["divergences"]]
        out += [f"## Differential: {n}", "",
                f"{compared} compared requests in {len(diffs)} tests; {len(steps)} requests diverge "
                f"after normalisation, in {len({r['nodeid'] for r, _ in steps})} tests.", ""]
        status = Counter((d["status"][0], d["status"][1]) for _, d in steps if d["status"][0] != d["status"][1])
        if status:
            out += ["Status pairs (reference -> target) among diverging requests:", "",
                    "| reference | target | requests |", "|---|---|---|"]
            out += [f"| {a} | {b} | {c} |" for (a, b), c in status.most_common(20)]
            out.append("")
        paths = Counter()
        for _, d in steps:
            for x in d["diffs"]:
                paths[(x["kind"], re.sub(r"\[\d+\]", "[*]", x["path"]))] += 1
        out += ["Most frequent diverging JSON paths:", "", "| kind | path | requests |", "|---|---|---|"]
        out += [f"| {k} | `{esc(p)}` | {c} |" for (k, p), c in paths.most_common(40)]
        out += ["", "<details><summary>per-request divergences</summary>", ""]
        for r, d in steps[:details]:
            out.append(f"- `{esc(r['nodeid'].split('::', 1)[1])}` step {d['step']} `{esc(d['request'])}` as "
                       f"{d['as']}: status {d['status'][0]} -> {d['status'][1]}")
            for x in d["diffs"][:8]:
                out.append(f"  - {x['kind']} `{esc(x['path'])}`: {esc(str(x['expected'])[:80])} -> "
                           f"{esc(str(x['actual'])[:80])}")
        out += ["", "</details>", ""]
    return out


def render(runs, details: int = 400) -> str:
    lines = ["# restapi-suite report", ""]
    for f in (summary, per_area, failures, notes, matrix):
        lines += f(runs)
    lines += differential(runs, details)
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="results dirs or .jsonl files (default: results)")
    ap.add_argument("--out", help="write markdown here (default stdout)")
    ap.add_argument("--details", type=int, default=400, help="max per-request differential entries")
    a = ap.parse_args(argv)
    runs = load(a.paths)
    if not runs:
        sys.exit("no results found")
    md = render(runs, a.details)
    if a.out:
        Path(a.out).write_text(md)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
