"""Target configuration: targets.toml + CLI flags + environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FILE = Path(__file__).with_name("targets.toml")


@dataclass
class Target:
    name: str
    kind: str
    base_url: str
    admin_user: str
    admin_password: str
    types: dict[str, str] = field(default_factory=dict)
    roles: dict[str, list[str]] = field(default_factory=dict)
    features: set[str] = field(default_factory=set)
    sandbox_type: str = "folder"

    def type(self, logical: str) -> str:
        return self.types.get(logical, logical)

    def logical_type(self, real: str) -> str:
        for k, v in self.types.items():
            if v == real:
                return k
        return real

    def has(self, *features: str) -> bool:
        return all(f in self.features for f in features)


def load_all(path: str | Path | None = None) -> dict[str, dict]:
    p = Path(path or os.environ.get("RESTAPI_TARGETS_FILE") or DEFAULT_FILE)
    return tomllib.loads(p.read_text())


def resolve(
    name: str | None,
    base_url: str | None = None,
    admin_user: str | None = None,
    admin_password: str | None = None,
    kind: str | None = None,
    path: str | Path | None = None,
) -> Target:
    """Resolve a target from a named definition plus overrides.

    With only base_url, the definition named by `kind` (default "plone")
    supplies types/roles/features and the target is named after kind."""
    defs = load_all(path)
    if name is None:
        name = kind or "plone"
    if name not in defs:
        if not base_url:
            raise ValueError(f"unknown target {name!r}; known: {', '.join(defs)}")
        d = dict(defs.get(kind or "plone", {}))
    else:
        d = dict(defs[name])
    return Target(
        name=name,
        kind=kind or d.get("kind", name),
        base_url=(base_url or d["base_url"]).rstrip("/"),
        admin_user=admin_user or d.get("admin_user", "admin"),
        admin_password=admin_password or d.get("admin_password", "admin"),
        types=dict(d.get("types", {})),
        roles={k: list(v) for k, v in d.get("roles", {}).items()},
        features=set(d.get("features", [])),
        sandbox_type=d.get("sandbox_type", "folder"),
    )
