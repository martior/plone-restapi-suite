"""HTTP layer: one `Side` per backend (target or reference).

Auth is always a JWT from POST @login (plone.restapi and Nick both support
it; Nick has no Basic auth). Tokens are cached per (side, user) for the
session. Strings in paths, params and JSON bodies may contain placeholders
that each side resolves on its own:

    {base}          the side's base URL (no trailing slash)
    {type:<name>}   the side's portal_type for logical type <name>
                    (targets.toml [<target>.types])
"""

from __future__ import annotations

import fcntl
import hashlib
import json as _json
import random
import tempfile
import re
import time
from http.cookiejar import CookieJar, DefaultCookiePolicy
from dataclasses import dataclass
from typing import Any

import httpx

from .targets import Target

MISSING = object()
_PH = re.compile(r"\{(base|type:[A-Za-z_]+)\}")


@dataclass(frozen=True)
class User:
    """A principal that exists with the same id/password on every side."""

    id: str
    password: str
    email: str = ""

    def __str__(self) -> str:
        return self.id


ANONYMOUS = None  # principal value meaning "no Authorization header"


class Resp:
    """Thin, picklable-friendly view of an httpx.Response."""

    def __init__(self, r: httpx.Response, side: "Side"):
        self.status = r.status_code
        self.headers = r.headers
        self.content = r.content
        self.url = str(r.request.url)
        self.method = r.request.method
        self.side = side
        self.ref: Resp | None = None  # reference response (differential mode)
        self._json: Any = MISSING

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    @property
    def is_json(self) -> bool:
        return "json" in (self.headers.get("content-type") or "")

    @property
    def json(self) -> Any:
        if self._json is MISSING:
            try:
                self._json = _json.loads(self.content) if self.content.strip() else None
            except ValueError:
                self._json = None
        return self._json

    def get(self, key: str, default=None):
        j = self.json
        return j.get(key, default) if isinstance(j, dict) else default

    @property
    def path(self) -> str | None:
        """Path of the returned object relative to the side's base URL."""
        oid = self.get("@id")
        if isinstance(oid, str) and oid.startswith(self.side.base):
            return oid[len(self.side.base) :].strip("/")
        return None

    def __repr__(self) -> str:
        return f"<Resp {self.method} {self.url} -> {self.status} {self.text[:400]!r}>"


def _client(timeout) -> httpx.Client:
    # Never store cookies: @login sets an auth cookie (Plone __ac/auth_token,
    # Nick auth_token) that would silently authenticate "anonymous" requests.
    jar = CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))
    return httpx.Client(timeout=timeout, follow_redirects=False, cookies=jar)


class SeedError(AssertionError):
    pass


class Side:
    def __init__(self, target: Target, role: str = "target", timeout: float = 60):
        self.target = target
        self.role = role
        self.base = target.base_url.rstrip("/")
        self.http = _client(timeout)
        self.admin = User(target.admin_user, target.admin_password)
        self._tokens: dict[str, str] = {}

    # -- placeholders -------------------------------------------------
    def sub(self, v: Any) -> Any:
        if isinstance(v, str):
            def rep(m):
                k = m.group(1)
                return self.base if k == "base" else self.target.type(k.split(":", 1)[1])

            return _PH.sub(rep, v)
        if isinstance(v, list):
            return [self.sub(x) for x in v]
        if isinstance(v, tuple):
            return tuple(self.sub(x) for x in v)
        if isinstance(v, dict):
            return {self.sub(k): self.sub(x) for k, x in v.items()}
        return v

    def url(self, path: str) -> str:
        path = self.sub(path)
        if path.startswith(("http://", "https://")):
            return path
        if not path:
            return self.base
        return self.base + (path if path.startswith("/") else "/" + path)

    # -- auth ---------------------------------------------------------
    def token(self, user: User, fresh: bool = False, lock: bool = True) -> str:
        """JWT for user (cached). Logins are serialised across xdist workers
        with a file lock: Nick keeps a per-user token array updated with
        read-modify-write, so concurrent logins as one user drop tokens
        (tested on purpose with lock=False in test_smoke.py)."""
        if fresh or user.id not in self._tokens:
            if not lock:
                return self._login(user)
            key = hashlib.sha1(self.base.encode()).hexdigest()[:12]
            with open(f"{tempfile.gettempdir()}/restapi-suite-login-{key}.lock", "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    return self._login(user)
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        return self._tokens[user.id]

    def _login(self, user: User) -> str:
        r = self._send("POST", self.url("@login"), headers={"Accept": "application/json"},
                       json={"login": user.id, "password": user.password})
        if r.status_code != 200 or "token" not in (r.json() or {}):
            raise SeedError(f"[{self.target.name}] login {user.id} failed: {r.status_code} {r.text[:300]}")
        self._tokens[user.id] = r.json()["token"]
        return self._tokens[user.id]

    def forget(self, user_id: str) -> None:
        self._tokens.pop(user_id, None)

    # -- requests -----------------------------------------------------
    CONFLICT_RETRIES = 6

    def _send(self, method, url, **kw) -> httpx.Response:
        """Send with two transport-level retries:
        - ZODB ConflictError surfacing as 500 (Plone under parallel load): the
          transaction was aborted, so the request is retried with backoff.
        - a poisoned keep-alive connection: retried once on a fresh client."""
        for attempt in range(self.CONFLICT_RETRIES):
            r = self._send_once(method, url, **kw)
            if r.status_code == 500 and (b"ConflictError" in r.content or b"database conflict error" in r.content):
                time.sleep(0.1 * (attempt + 1) + random.random() * 0.2)
                continue
            return r
        return r

    def _send_once(self, method, url, **kw) -> httpx.Response:
        try:
            return self.http.request(method, url, **kw)
        except (httpx.RemoteProtocolError, httpx.ReadError):
            # A previous response may have poisoned keep-alive (S1: Plone
            # sends a body on HEAD 404). Retry once on a fresh connection.
            self.http.close()
            self.http = _client(self.http.timeout)
            return self.http.request(method, url, **kw)

    def request(
        self,
        method: str,
        path: str,
        user: User | None = None,
        json: Any = MISSING,
        params: dict | None = None,
        headers: dict | None = None,
        content: bytes | str | None = None,
        accept: str | None = "application/json",
    ) -> Resp:
        h = {}
        if accept:
            h["Accept"] = accept
        if user is not None:
            h["Authorization"] = f"Bearer {self.token(user)}"
        if headers:
            h.update({k: self.sub(v) for k, v in headers.items()})
        kw: dict[str, Any] = {"headers": h}
        if params:
            kw["params"] = self.sub(params)
        if json is not MISSING:
            kw["content"] = _json.dumps(self.sub(json), ensure_ascii=False).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        elif content is not None:
            kw["content"] = self.sub(content) if isinstance(content, str) else content
        return Resp(self._send(method.upper(), self.url(path), **kw), self)

    def close(self):
        self.http.close()
