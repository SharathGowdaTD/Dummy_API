"""One codebase, one kind of login per deployment: AUTH_MODE."""
import base64

import pytest

from app import auth_modes
from tests.conftest import API

ADMIN_KEY, READONLY_KEY = "rk_test_admin_9f2c41d7a8e3", "rk_test_readonly_2e6f8a0c4d1b"
ADMIN_CLIENT, ADMIN_SECRET = "cl_admin_app", "cs_test_admin_3f9e1c7a5b2d"


@pytest.fixture
def mode():
    def switch(m):
        auth_modes.settings.auth_mode = m
    yield switch
    auth_modes.settings.auth_mode = "bearer"


def _basic(email, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{email}:{password}".encode()).decode()}


def _token(client, cid=ADMIN_CLIENT, secret=ADMIN_SECRET, **extra):
    return client.post(f"{API}/oauth/token", data={"grant_type": "client_credentials", "client_id": cid,
                                                   "client_secret": secret, **extra})


# ── the spec says which login the deployment wants ────────────────────────

@pytest.mark.parametrize("m, schemes, has, lacks", [
    ("bearer", ["HTTPBearer"], "/auth/login", "/oauth/token"),
    ("basic", ["HTTPBasic"], "/auth/me", "/auth/login"),
    ("api_key", ["ApiKeyHeader", "ApiKeyQuery", "ApiKeyCookie"], "/api-keys", "/auth/login"),
    ("oauth2", ["OAuth2ClientCredentials"], "/oauth/token", "/auth/refresh"),
])
def test_the_spec_declares_only_the_active_mode(client, mode, m, schemes, has, lacks):
    mode(m)
    spec = client.get("/openapi.json").json()
    assert list(spec["components"]["securitySchemes"]) == schemes
    assert API + has in spec["paths"] and API + lacks not in spec["paths"]
    secured = spec["paths"][f"{API}/products"]["post"]["security"]
    assert [list(s)[0] for s in secured] == schemes


def test_routes_of_another_mode_are_not_there(client, mode):
    mode("basic")
    assert client.post(f"{API}/auth/login", json={"email": "a@b.co", "password": "x"}).status_code == 404
    assert _token(client).status_code == 404
    mode("oauth2")
    assert client.get(f"{API}/api-keys").status_code == 404


# ── basic ─────────────────────────────────────────────────────────────────

def test_basic_sends_email_and_password_on_every_call(client, mode, staff):
    mode("basic")
    ok = client.get(f"{API}/auth/me", headers=_basic("staff@retailhub.in", "Staff12345"))
    assert ok.status_code == 200 and ok.json()["email"] == "staff@retailhub.in"
    none = client.get(f"{API}/auth/me")
    assert none.status_code == 401 and none.headers["WWW-Authenticate"] == 'Basic realm="retail"'
    wrong = client.get(f"{API}/auth/me", headers=_basic("staff@retailhub.in", "nope12345"))
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"
    bearer = client.get(f"{API}/auth/me", headers={"Authorization": "Bearer abc"})
    assert bearer.status_code == 401, "another mode's credentials count as none"


def test_basic_keeps_roles(client, mode, staff):
    mode("basic")
    r = client.post(f"{API}/categories", json={"name": "Basic Cat"}, headers=_basic("staff@retailhub.in", "Staff12345"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "INSUFFICIENT_ROLE"


# ── api key ───────────────────────────────────────────────────────────────

def test_an_api_key_works_in_the_header_query_or_cookie(client, mode):
    mode("api_key")
    assert client.get(f"{API}/auth/me", headers={"X-API-Key": ADMIN_KEY}).json()["email"] == "admin@retailhub.in"
    assert client.get(f"{API}/auth/me", params={"api_key": ADMIN_KEY}).status_code == 200
    client.cookies.set("retail_key", ADMIN_KEY)
    try:
        assert client.get(f"{API}/auth/me").status_code == 200
    finally:
        client.cookies.clear()
    missing = client.get(f"{API}/auth/me")
    assert missing.status_code == 401 and missing.json()["error"]["code"] == "MISSING_API_KEY"
    bad = client.get(f"{API}/auth/me", headers={"X-API-Key": "rk_nope"})
    assert bad.json()["error"]["code"] == "INVALID_API_KEY"


def test_a_read_only_key_may_look_but_not_change(client, mode):
    mode("api_key")
    assert client.get(f"{API}/products", headers={"X-API-Key": READONLY_KEY}).status_code == 200
    r = client.post(f"{API}/categories", json={"name": "RO Cat"}, headers={"X-API-Key": READONLY_KEY})
    assert r.status_code == 403 and r.json()["error"]["code"] == "INSUFFICIENT_SCOPE"


def test_admins_make_and_revoke_keys_and_a_key_acts_as_its_owner(client, mode, staff):
    mode("api_key")
    admin = {"X-API-Key": ADMIN_KEY}
    staff_id = next(u["id"] for u in client.get(f"{API}/users", headers=admin).json()["items"]
                    if u["email"] == "staff@retailhub.in")
    made = client.post(f"{API}/api-keys", json={"name": "Staff key", "user_id": staff_id}, headers=admin)
    assert made.status_code == 201 and made.json()["key"].startswith("rk_") and made.json()["scopes"] == ["read", "write"]
    key = {"X-API-Key": made.json()["key"]}
    assert client.get(f"{API}/auth/me", headers=key).json()["email"] == "staff@retailhub.in"
    assert client.post(f"{API}/api-keys", json={"name": "x", "user_id": staff_id}, headers=key).status_code == 403
    assert "key" not in client.get(f"{API}/api-keys", headers=admin).json()[0]
    assert client.delete(f"{API}/api-keys/{made.json()['id']}", headers=admin).status_code == 204
    gone = client.get(f"{API}/auth/me", headers=key)
    assert gone.status_code == 401 and gone.json()["error"]["code"] == "API_KEY_REVOKED"


# ── oauth2 client credentials ─────────────────────────────────────────────

def test_a_client_gets_a_token_as_a_form_and_uses_it(client, mode):
    mode("oauth2")
    r = _token(client)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 900 and body["scope"] == "read write"
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == "admin@retailhub.in"
    by_basic = client.post(f"{API}/oauth/token", data={"grant_type": "client_credentials"},
                           headers=_basic(ADMIN_CLIENT, ADMIN_SECRET))
    assert by_basic.status_code == 200, "client id and secret may come as HTTP Basic"


@pytest.mark.parametrize("data, status, error", [
    ({}, 400, "invalid_request"),
    ({"grant_type": "password"}, 400, "unsupported_grant_type"),
    ({"grant_type": "client_credentials"}, 401, "invalid_client"),
    ({"grant_type": "client_credentials", "client_id": ADMIN_CLIENT, "client_secret": "wrong"}, 401, "invalid_client"),
    ({"grant_type": "client_credentials", "client_id": ADMIN_CLIENT, "client_secret": ADMIN_SECRET,
      "scope": "read admin"}, 400, "invalid_scope"),
])
def test_token_refusals_use_the_oauth_error_shape(client, mode, data, status, error):
    mode("oauth2")
    r = client.post(f"{API}/oauth/token", data=data)
    assert r.status_code == status and r.json()["error"] == error and "error_description" in r.json()


def test_a_read_scope_token_cannot_write_and_a_user_token_is_not_a_client_token(client, mode, admin):
    mode("oauth2")
    read = _token(client, scope="read").json()["access_token"]
    r = client.post(f"{API}/categories", json={"name": "Scope Cat"}, headers={"Authorization": f"Bearer {read}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "INSUFFICIENT_SCOPE"
    assert client.get(f"{API}/auth/me", headers=admin).status_code == 401, "a login token doesn't work here"


def test_a_disabled_client_loses_its_tokens(client, mode, staff):
    mode("oauth2")
    admin = {"Authorization": f"Bearer {_token(client).json()['access_token']}"}
    users = client.get(f"{API}/users", headers=admin).json()["items"]
    made = client.post(f"{API}/oauth/clients", json={"name": "Temp", "user_id": users[0]["id"], "scopes": ["read"]},
                       headers=admin).json()
    tok = _token(client, made["client_id"], made["client_secret"]).json()["access_token"]
    assert client.get(f"{API}/products", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.delete(f"{API}/oauth/clients/{made['client_id']}", headers=admin).status_code == 204
    gone = client.get(f"{API}/products", headers={"Authorization": f"Bearer {tok}"})
    assert gone.status_code == 401 and gone.json()["error"]["code"] == "INVALID_CLIENT"
    assert _token(client, made["client_id"], made["client_secret"]).json()["error"] == "invalid_client"



# ── one deployment, the login picked by the URL's first segment ───────────

@pytest.mark.parametrize("prefix, schemes", [
    ("/basic", ["HTTPBasic"]), ("/apikey", ["ApiKeyHeader", "ApiKeyQuery", "ApiKeyCookie"]),
    ("/oauth2", ["OAuth2ClientCredentials"]), ("/bearer", ["HTTPBearer"]), ("", ["HTTPBearer"]),
])
def test_the_url_prefix_picks_the_login(client, prefix, schemes):
    spec = client.get(f"{prefix}/openapi.json").json()
    assert list(spec["components"]["securitySchemes"]) == schemes
    assert spec.get("servers", [{"url": ""}])[0]["url"] == prefix or (not prefix and "servers" not in spec)


def test_the_same_endpoint_answers_by_its_prefix(client, admin):
    assert client.get(f"{API}/auth/me", headers=admin).status_code == 200
    assert client.get(f"/basic{API}/auth/me", headers=admin).status_code == 401, "a login token is not Basic"
    assert client.get(f"/basic{API}/auth/me", headers=_basic("admin@retailhub.in", "Admin12345")).status_code == 200
    assert client.get(f"/apikey{API}/auth/me", headers={"X-API-Key": ADMIN_KEY}).json()["email"] == "admin@retailhub.in"
    tok = client.post(f"/oauth2{API}/oauth/token", data={"grant_type": "client_credentials", "client_id": ADMIN_CLIENT,
                                                         "client_secret": ADMIN_SECRET}).json()["access_token"]
    assert client.get(f"/oauth2{API}/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.post(f"{API}/oauth/token", data={}).status_code == 404, "no token endpoint without /oauth2"
    assert client.get("/apikey/health").json()["status"] == "ok"


def test_the_oauth_token_url_carries_the_prefix(client):
    spec = client.get("/oauth2/openapi.json").json()
    flow = spec["components"]["securitySchemes"]["OAuth2ClientCredentials"]["flows"]["clientCredentials"]
    assert flow["tokenUrl"] == f"/oauth2{API}/oauth/token" and f"{API}/oauth/token" in spec["paths"]
