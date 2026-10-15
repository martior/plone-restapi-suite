"""Framework smoke tests: the target answers, admin can log in, sandbox works."""


def test_site_root_json(api):
    r = api.get("")
    assert r.status == 200, r
    assert r.json["@id"].rstrip("/") == api.t.base


def test_sandbox_is_folder(api, sandbox):
    r = api.get(sandbox)
    assert r.status == 200, r
    assert r.json["@type"] == api.type(api.target.sandbox_type)
    assert r.json["id"] == sandbox.rsplit("/", 1)[-1]


def test_personas_can_login(api, personas):
    for name in ("member", "local_editor", "site_admin", "manager"):
        assert api.t.token(personas.users[name])


def test_anonymous_is_really_anonymous(api, sandbox):
    """Guards against cookie leakage: @login sets auth cookies on both backends."""
    api.doc(sandbox, "private-doc")
    api.t.token(api.t.admin, fresh=True)
    assert api.get(f"{sandbox}/private-doc", as_="anonymous").status == 401


def test_concurrent_logins_yield_valid_tokens(api):
    """Six parallel @login calls for one user: every returned token must work.
    (Found while building the suite: Nick stores tokens in a per-user array
    with read-modify-write, so concurrent logins can drop each other's token.)"""
    from concurrent.futures import ThreadPoolExecutor
    from restapi_suite.client import Side

    user = api.create_user()  # dedicated: racing logins may invalidate this user's tokens

    def login(_):
        side = Side(api.t.target)
        tok = side.token(user, fresh=True, lock=False)
        side.close()
        return tok

    with ThreadPoolExecutor(6) as ex:
        tokens = list(ex.map(login, range(6)))
    bad = []
    for tok in tokens:
        r = api.t.http.get(api.t.url(f"@users/{user.id}"),
                           headers={"Accept": "application/json", "Authorization": f"Bearer {tok}"})
        if r.status_code != 200:
            bad.append(r.status_code)
    assert not bad, f"{len(bad)} of 6 tokens rejected: {bad}"
