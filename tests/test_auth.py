"""@login, @login-renew, @logout: token lifecycle, wrong credentials,
forged/expired tokens, and concurrent sessions of one user (they must not
invalidate each other; see also test_smoke.py::test_concurrent_logins_yield_valid_tokens).

All token checks bypass the suite's token cache (services.with_token) and
use dedicated users, so racing or revoking never affects other tests."""

from __future__ import annotations

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from restapi_suite.assertions import DENIED
from restapi_suite.client import Side
from restapi_suite.services import jwt_payload, login, with_token

pytestmark = pytest.mark.area("auth")

NICK = "nick"
PLONE = "plone"


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def _me(api, user, token):
    """GET @users/<self> with an explicit token: 200 iff the token is valid for user."""
    return with_token(api.t, "GET", f"@users/{user.id}", token)


def _token(api, user) -> str:
    r = login(api.t, user.id, user.password)
    assert r.status == 200, r
    return r.json["token"]


# ------------------------------------------------------------------ login

def test_login_returns_jwt_for_user(api):
    u = api.create_user()
    r = login(api.t, u.id, u.password)
    assert r.status == 200 and set(r.json) == {"token"}, r
    p = jwt_payload(r.json["token"])
    assert p["sub"] == u.id, p
    assert p["exp"] > time.time() + 60, p
    assert _me(api, u, r.json["token"]).status == 200


@pytest.mark.parametrize("case", ["wrong-password", "unknown-user", "empty-password", "case-changed-login"])
def test_login_bad_credentials_is_401(api, case):
    u = api.create_user()
    body = {"wrong-password": {"login": u.id, "password": u.password + "x"},
            "unknown-user": {"login": "rs-nobody-here", "password": u.password},
            "empty-password": {"login": u.id, "password": ""},
            "case-changed-login": {"login": u.id.upper(), "password": u.password}}[case]
    r = api.post("@login", as_="anonymous", json=body)
    if case == "empty-password" and r.status == 400:
        return  # "Login and password must be provided" is acceptable for an empty password
    assert r.status == 401, r
    assert r.json["error"]["type"] == "Invalid credentials", r
    assert "token" not in (r.json or {})


def test_login_unknown_user_and_wrong_password_look_the_same(api):
    """No user enumeration: same status and body for both failures."""
    u = api.create_user()
    a = login(api.t, u.id, "wrong-password")
    b = login(api.t, "rs-nobody-here", "wrong-password")
    assert (a.status, a.json) == (b.status, b.json)


@pytest.mark.parametrize("body", [{}, {"login": "admin"}, {"password": "x"}], ids=["empty", "no-password",
                                                                                   "no-login"])
def test_login_missing_credentials_is_400(api, body):
    r = api.post("@login", as_="anonymous", json=body)
    assert r.status == 400, r
    assert r.json["error"]["type"] == "Missing credentials", r


@pytest.mark.known_divergence(PLONE, kind="plone-bug", ref="F1",
                              reason="a non-JSON @login body is a 500 ('No JSON object could be decoded')")
def test_login_invalid_json_is_400(api):
    """A malformed body is a client error (usage/content.md: 400 for a malformed request)."""
    r = api.post("@login", as_="anonymous", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status == 400, r


def test_login_by_email_not_enabled_by_default(api):
    u = api.create_user()
    assert login(api.t, u.email, u.password).status == 401


def test_login_after_password_change_needs_new_password(api):
    u = api.create_user()
    assert api.patch(f"@users/{u.id}", json={"password": "Pw-changed-123456"}).status == 204
    assert login(api.t, u.id, u.password).status == 401
    assert login(api.t, u.id, "Pw-changed-123456").status == 200


@pytest.mark.known_divergence(NICK, reason="Nick has no GET @login (external login options)", kind="deliberate")
def test_login_get_lists_options(api):
    r = api.get("@login", as_="anonymous")
    assert r.status == 200 and "options" in r.json, r


@pytest.mark.known_divergence(NICK, reason="Nick has no HTTP Basic auth", kind="deliberate")
def test_basic_auth_works(api):
    u = api.create_user()
    cred = base64.b64encode(f"{u.id}:{u.password}".encode()).decode()
    r = api.t.request("GET", f"@users/{u.id}", None, headers={"Authorization": f"Basic {cred}"})
    assert r.status == 200, r


# ------------------------------------------------------------------ tokens

@pytest.mark.parametrize("kind", ["garbage", "alg-none-admin", "tampered-sub", "tampered-exp", "truncated"])
def test_invalid_token_is_not_authenticated(api, kind):
    """Forged tokens never authenticate. Public resources still answer (as
    anonymous); protected ones are denied."""
    u = api.create_user()
    tok = _token(api, u)
    h, p, sig = tok.split(".")
    payload = jwt_payload(tok)
    forged = {
        "garbage": "abc.def.ghi",
        "alg-none-admin": _b64({"alg": "none", "typ": "JWT"}) + "." + _b64({"sub": api.t.admin.id,
                                                                            "exp": 4102444800}) + ".",
        "tampered-sub": h + "." + _b64({**payload, "sub": api.t.admin.id}) + "." + sig,
        "tampered-exp": h + "." + _b64({**payload, "exp": payload["exp"] + 10 ** 7}) + "." + sig,
        "truncated": tok[:-5],
    }[kind]
    assert with_token(api.t, "GET", "@users", forged).status in DENIED
    assert with_token(api.t, "GET", f"@users/{api.t.admin.id}", forged).status in DENIED
    assert with_token(api.t, "GET", "", forged).status == 200


def test_expired_token_is_rejected(api):
    """An expired token cannot be minted without the server secret; an
    exp moved into the past invalidates the signature, which must also fail."""
    u = api.create_user()
    tok = _token(api, u)
    h, _, sig = tok.split(".")
    expired = h + "." + _b64({**jwt_payload(tok), "exp": int(time.time()) - 3600}) + "." + sig
    assert _me(api, u, expired).status in DENIED


def test_token_of_other_user_does_not_grant_access(api):
    a, b = api.create_user(), api.create_user()
    assert _me(api, b, _token(api, a)).status in DENIED


# ------------------------------------------------------------------ renew

def test_login_renew_returns_new_working_token(api):
    u = api.create_user()
    tok = _token(api, u)
    time.sleep(1.1)  # iat/exp have second resolution
    r = with_token(api.t, "POST", "@login-renew", tok)
    assert r.status == 200 and set(r.json) == {"token"}, r
    new = r.json["token"]
    assert new != tok
    assert jwt_payload(new)["sub"] == u.id and jwt_payload(new)["exp"] > jwt_payload(tok)["exp"]
    assert _me(api, u, new).status == 200


def test_login_renew_keeps_old_token_valid(api):
    """Renewing in one tab must not log out other holders of the old token."""
    u = api.create_user()
    tok = _token(api, u)
    assert with_token(api.t, "POST", "@login-renew", tok).status == 200
    assert _me(api, u, tok).status == 200


def test_login_renew_without_token_is_401(api):
    r = api.post("@login-renew", as_="anonymous")
    assert r.status == 401, r
    assert "token" not in (r.json or {})


def test_login_renew_with_forged_token_is_401(api):
    r = with_token(api.t, "POST", "@login-renew", "abc.def.ghi")
    assert r.status == 401, r


# ------------------------------------------------------------------ logout

def test_logout_returns_204(api):
    u = api.create_user()
    assert with_token(api.t, "POST", "@logout", _token(api, u)).status == 204


def test_logout_anonymous_is_204(api):
    assert api.post("@logout", as_="anonymous").status == 204


@pytest.mark.known_divergence(PLONE, kind="plone-bug", ref="F1",
                              reason="@logout answers 204 but does not invalidate the JWT (store_tokens=False "
                                     "by default): a logged-out token keeps working until it expires")
def test_logout_invalidates_token(api):
    """usage/authentication.md: "The @logout endpoint can be used to invalidate tokens." A token that
    still works after a successful logout survives the user's attempt to end the session."""
    u = api.create_user()
    tok = _token(api, u)
    assert with_token(api.t, "POST", "@logout", tok).status == 204
    assert _me(api, u, tok).status == 401


def test_logout_does_not_affect_other_session(api):
    """Two sessions (devices) of one user: logging out one keeps the other."""
    u = api.create_user()
    t1 = _token(api, u)
    t2 = _token(api, u)
    assert with_token(api.t, "POST", "@logout", t1).status == 204
    assert _me(api, u, t2).status == 200


def test_logout_does_not_affect_other_users(api):
    a, b = api.create_user(), api.create_user()
    ta, tb = _token(api, a), _token(api, b)
    with_token(api.t, "POST", "@logout", ta)
    assert _me(api, b, tb).status == 200


# ------------------------------------------------------------------ concurrency

def test_sequential_logins_both_valid(api):
    u = api.create_user()
    t1, t2 = _token(api, u), _token(api, u)
    assert _me(api, u, t1).status == 200 and _me(api, u, t2).status == 200


def _parallel(n, fn):
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, range(n)))


def test_concurrent_logins_same_user_all_valid(api):
    """8 parallel logins as one user (separate connections): all tokens stay valid."""
    u = api.create_user()

    def one(_):
        side = Side(api.t.target)
        try:
            return login(side, u.id, u.password).json["token"]
        finally:
            side.close()

    tokens = _parallel(8, one)
    bad = [i for i, t in enumerate(tokens) if _me(api, u, t).status != 200]
    assert not bad, f"{len(bad)} of {len(tokens)} concurrent-login tokens rejected"


def test_concurrent_renews_same_user_all_valid(api):
    u = api.create_user()
    base = _token(api, u)

    def one(_):
        side = Side(api.t.target)
        try:
            r = with_token(side, "POST", "@login-renew", base)
            return r.json["token"] if r.status == 200 else None
        finally:
            side.close()

    tokens = _parallel(6, one)
    assert all(tokens), tokens
    bad = [t for t in tokens + [base] if _me(api, u, t).status != 200]
    assert not bad, f"{len(bad)} of {len(tokens) + 1} tokens rejected after concurrent renews"


def test_concurrent_login_and_logout_keeps_other_sessions(api):
    """One session logs out while others log in: only the logged-out token
    may become invalid."""
    u = api.create_user()
    victim = _token(api, u)

    def one(i):
        side = Side(api.t.target)
        try:
            if i == 0:
                with_token(side, "POST", "@logout", victim)
                return None
            return login(side, u.id, u.password).json["token"]
        finally:
            side.close()

    tokens = [t for t in _parallel(6, one) if t]
    bad = [t for t in tokens if _me(api, u, t).status != 200]
    assert not bad, f"{len(bad)} of {len(tokens)} fresh tokens rejected"


def test_concurrent_requests_with_one_token(api):
    u = api.create_user()
    tok = _token(api, u)

    def one(_):
        side = Side(api.t.target)
        try:
            return with_token(side, "GET", f"@users/{u.id}", tok).status
        finally:
            side.close()

    assert set(_parallel(10, one)) == {200}
