"""Test-facing API facade.

`Api.request()` sends a request to the target and, in differential mode
(--reference), sends the *same* request to the reference so both backends go
through identical state changes. The target response is returned (its
`.ref` holds the reference response) and the normalised pair is diffed into
the test's Recorder unless compare=False.

Seeding helpers (create, transition, create_user, create_group, share, ...)
always run on every side, check the target's status strictly and register
cleanups (users/groups) that `cleanup()` undoes at test teardown.

Personas: "anonymous", "admin" (the configured admin), the session personas
"member", "local_editor", "site_admin", "manager" (see Personas), or any
`User` object created by the test.
"""

from __future__ import annotations

import secrets
from typing import Any, Callable, Iterable

from .client import MISSING, User, Resp, SeedError, Side
from .differential import Recorder

PERSONAS = ("anonymous", "member", "local_editor", "site_admin", "manager")
# Global (logical) roles of each session persona; local_editor is a plain
# member that every sandbox grants the local role Editor.
PERSONA_ROLES = {
    "member": ["member"],
    "local_editor": ["member"],
    "site_admin": ["site_admin"],
    "manager": ["manager"],
}


def uid(n: int = 6) -> str:
    return secrets.token_hex(n)[:n]


class Api:
    def __init__(self, target: Side, ref: Side | None = None, personas: "Personas | None" = None,
                 recorder: Recorder | None = None):
        self.t = target
        self.r = ref
        self.personas = personas
        self.recorder = recorder or Recorder()
        self._cleanup: list[Callable[[], Any]] = []

    # -- basics -------------------------------------------------------
    @property
    def sides(self) -> list[Side]:
        return [self.t] + ([self.r] if self.r else [])

    @property
    def target(self):
        return self.t.target

    @property
    def differential(self) -> bool:
        return self.r is not None

    def type(self, logical: str) -> str:
        """Target's portal_type for a logical type (for assertions)."""
        return self.t.target.type(logical)

    def principal(self, who) -> User | None:
        if who is None or who == "anonymous":
            return None
        if isinstance(who, User):
            return who
        if who == "admin":
            return self.t.admin  # each side resolves its own admin below
        if self.personas and who in self.personas.users:
            return self.personas.users[who]
        raise KeyError(f"unknown persona {who!r}")

    def _principal_for(self, side: Side, who) -> User | None:
        if who == "admin":
            return side.admin
        return self.principal(who)

    def request(self, method: str, path: str = "", as_: Any = "admin", *, json: Any = MISSING,
                params: dict | None = None, headers: dict | None = None, content: bytes | str | None = None,
                accept: str | None = "application/json", compare: bool = True, ignore: Iterable[str] = (),
                label: str = "") -> Resp:
        kw = dict(json=json, params=params, headers=headers, content=content, accept=accept)
        resp = self.t.request(method, path, self._principal_for(self.t, as_), **kw)
        if self.r is not None:
            rr = self.r.request(method, path, self._principal_for(self.r, as_), **kw)
            resp.ref = rr
            if compare:
                self.recorder.record(label, method, path, str(as_), rr, resp, ignore)
        return resp

    def get(self, path="", as_="admin", **kw) -> Resp:
        return self.request("GET", path, as_, **kw)

    def post(self, path="", as_="admin", **kw) -> Resp:
        return self.request("POST", path, as_, **kw)

    def patch(self, path="", as_="admin", **kw) -> Resp:
        return self.request("PATCH", path, as_, **kw)

    def delete(self, path="", as_="admin", **kw) -> Resp:
        return self.request("DELETE", path, as_, **kw)

    def each(self, fn: Callable[[Side], Resp], expect: Iterable[int] | None = None, what: str = "") -> Resp:
        """Run fn(side) on every side (for side-specific payloads)."""
        res = None
        for side in self.sides:
            r = fn(side)
            if expect is not None and side is self.t and r.status not in tuple(expect):
                raise SeedError(f"[{side.target.name}] {what or 'seed'} failed: {r!r}")
            if res is None:
                res = r
            else:
                res.ref = r
        return res

    def _seed(self, method, path, as_="admin", expect=(200, 201, 204), what="", **kw) -> Resp:
        r = self.request(method, path, as_, compare=False, **kw)
        if r.status not in expect:
            raise SeedError(f"[{self.t.target.name}] {what or method + ' ' + path} -> {r!r}")
        return r

    # -- content ------------------------------------------------------
    def create(self, parent: str, type_: str, id: str | None = None, title: str | None = None,
               as_="admin", expect=(201,), **fields) -> Resp:
        """POST a child of logical type `type_` into `parent` (a relative path)."""
        body = {"@type": "{type:%s}" % type_, **fields}
        if id is not None:
            body["id"] = id
        body["title"] = title if title is not None else (id or f"Item {uid()}")
        return self._seed("POST", parent, as_, expect=expect, what=f"create {type_} in {parent!r}", json=body)

    def doc(self, parent: str, id: str, **kw) -> str:
        """Create a document and return its relative path."""
        r = self.create(parent, "document", id=id, **kw)
        return r.path or f"{parent}/{id}"

    def folder(self, parent: str, id: str, **kw) -> str:
        r = self.create(parent, "folder", id=id, **kw)
        return r.path or f"{parent}/{id}"

    def transition(self, path: str, transition: str, as_="admin", expect=(200,), **body) -> Resp:
        return self._seed("POST", f"{path}/@workflow/{transition}", as_, expect=expect,
                          what=f"transition {transition} on {path!r}", json=body)

    def publish(self, path: str, **kw) -> Resp:
        return self.transition(path, "publish", **kw)

    def state(self, path: str) -> str | None:
        r = self.request("GET", path, "admin", compare=False)
        return r.get("review_state")

    # -- principals ---------------------------------------------------
    def roles_for(self, side: Side, roles: Iterable[str]) -> list[str]:
        """Logical roles (member/site_admin/manager) -> real ids; others verbatim."""
        out: list[str] = []
        for role in roles:
            out.extend(side.target.roles.get(role, [role]))
        return out

    def create_user(self, id: str | None = None, roles: Iterable[str] = ("member",), password: str | None = None,
                    fullname: str | None = None, cleanup: bool = True, expect=(201,)) -> User:
        user = User(id or f"rsu{uid(8)}", password or f"Pw-{secrets.token_hex(8)}", "")
        email = f"{user.id.lower()}@example.org"
        user = User(user.id, user.password, email)
        roles = list(roles)

        def mk(side: Side) -> Resp:
            return side.request("POST", "@users", side.admin, json={
                "username": user.id, "email": email, "password": user.password,
                "fullname": fullname or f"RS {user.id}", "roles": self.roles_for(side, roles)})

        self.each(mk, expect=expect, what=f"create user {user.id}")
        if cleanup:
            self._cleanup.append(lambda: self.delete_user(user.id))
        return user

    def delete_user(self, user_id: str) -> None:
        from .cleanup import delete_retry
        for side in self.sides:
            delete_retry(side, f"@users/{user_id}")
            side.forget(user_id)

    def create_group(self, id: str | None = None, roles: Iterable[str] = (), users: Iterable[str] = (),
                     title: str | None = None, cleanup: bool = True) -> str:
        gid = id or f"rsg{uid(8)}"
        users = [str(u) for u in users]

        def mk(side: Side) -> Resp:
            return side.request("POST", "@groups", side.admin, json={
                "groupname": gid, "title": title or gid, "roles": self.roles_for(side, roles), "users": users})

        self.each(mk, expect=(201,), what=f"create group {gid}")
        if cleanup:
            from .cleanup import delete_retry
            self._cleanup.append(lambda: [delete_retry(s, f"@groups/{gid}") for s in self.sides])
        return gid

    def add_to_group(self, gid: str, user_id: str) -> None:
        self._seed("PATCH", f"@groups/{gid}", json={"users": {user_id: True}}, what=f"add {user_id} to {gid}")

    def share(self, path: str, principal, roles: Iterable[str] | dict, type_: str = "user",
              inherit: bool | None = True, as_="admin", expect=(200, 204)) -> Resp:
        """POST @sharing. roles: list (granted) or {role: bool}. `inherit` is
        always sent unless None (Nick mishandles a missing key, see tests)."""
        rmap = roles if isinstance(roles, dict) else {r: True for r in roles}
        body: dict[str, Any] = {"entries": [{"id": str(principal), "type": type_, "roles": rmap}]}
        if inherit is not None:
            body["inherit"] = inherit
        return self._seed("POST", f"{path}/@sharing", as_, expect=expect, what=f"share {path!r}", json=body)

    def block_inheritance(self, path: str) -> Resp:
        return self._seed("POST", f"{path}/@sharing", json={"entries": [], "inherit": False},
                          what=f"block inheritance on {path!r}")

    # -- teardown -----------------------------------------------------
    def defer(self, fn: Callable[[], Any]) -> None:
        self._cleanup.append(fn)

    def cleanup(self) -> None:
        while self._cleanup:
            fn = self._cleanup.pop()
            try:
                fn()
            except Exception:  # best effort
                pass


class Personas:
    """Session-scoped persona users (per xdist worker), identical ids on all sides."""

    def __init__(self, sides: list[Side]):
        self.sides = sides
        self.prefix = f"rs{uid(5)}"
        self.users: dict[str, User] = {}

    def setup(self) -> "Personas":
        api = Api(self.sides[0], self.sides[1] if len(self.sides) > 1 else None)
        for name, roles in PERSONA_ROLES.items():
            uid_ = f"{self.prefix}{name.replace('_', '')}"
            self.users[name] = api.create_user(uid_, roles=roles, fullname=f"Persona {name}", cleanup=False)
        return self

    def teardown(self) -> None:
        api = Api(self.sides[0], self.sides[1] if len(self.sides) > 1 else None)
        for u in self.users.values():
            api.delete_user(u.id)

    def __getitem__(self, name: str) -> User | None:
        return None if name == "anonymous" else self.users[name]
