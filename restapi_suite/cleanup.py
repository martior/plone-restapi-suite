"""Remove everything the suite may have left behind on a target (prefix based).

    python -m restapi_suite.cleanup --target nick [--base-url ... --admin-user ... --admin-password ...] [--dry-run]

Deletes root content whose id starts with "rs-", users whose id starts with
"rs" and matches the suite's patterns, and groups starting with "rsg" or named
"Editor" (A2#3 test). Safe to run while no suite run is active.
"""

from __future__ import annotations

import argparse
import re
import time

from . import targets
from .client import Side

USER_RE = re.compile(r"^(rs[0-9a-f]{5}(member|localeditor|siteadmin|manager)|rsu[0-9a-f]{8}|Reader)$")
GROUP_RE = re.compile(r"^(rsg[0-9a-f]{8}|Editor)$")


def delete_retry(side: Side, path: str, tries: int = 6) -> int:
    """DELETE with retries: Nick deadlocks (500) when siblings are deleted in
    parallel, Plone may raise ConflictErrors. Returns the final status."""
    status = 0
    for i in range(tries):
        status = side.request("DELETE", path, side.admin).status
        if status in (200, 204, 404):
            return status
        time.sleep(0.3 * (i + 1))
    return status


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="plone")
    ap.add_argument("--base-url")
    ap.add_argument("--admin-user")
    ap.add_argument("--admin-password")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    side = Side(targets.resolve(a.target, a.base_url, a.admin_user, a.admin_password))
    root = side.request("GET", "", side.admin, params={"b_size": "1000"}).json or {}
    content = [i["@id"].rsplit("/", 1)[-1] for i in root.get("items", [])]
    content = [c for c in content if c.startswith("rs-")]
    users = side.request("GET", "@users", side.admin).json or []
    users = [u["id"] for u in (users if isinstance(users, list) else users.get("items", [])) if USER_RE.match(u["id"])]
    groups = side.request("GET", "@groups", side.admin).json or []
    groups = [g["id"] for g in (groups if isinstance(groups, list) else groups.get("items", []))
              if GROUP_RE.match(g["id"])]
    print(f"{side.target.name}: {len(content)} content, {len(users)} users, {len(groups)} groups")
    if a.dry_run:
        print(content, users, groups)
        return
    bad = [c for c in content if delete_retry(side, c) not in (204, 404)]
    for u in users:
        delete_retry(side, f"@users/{u}")
    for g in groups:
        delete_retry(side, f"@groups/{g}")
    if bad:
        raise SystemExit(f"could not delete: {bad}")


if __name__ == "__main__":
    main()
