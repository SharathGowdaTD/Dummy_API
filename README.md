# Retail Inventory API

FastAPI + PostgreSQL (Supabase) backend for a retail store: JWT auth with roles, products/SKUs,
inventory with an audit trail, suppliers, orders with stock deduction, and reports.

- Tester page: http://localhost:8000/
- Swagger: http://localhost:8000/docs
- API base: `/api/v1`

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env        # then fill DB_PASSWORD, JWT_SECRET, SEED_ADMIN_PASSWORD
.venv/bin/alembic upgrade head
.venv/bin/python -m app.seed
.venv/bin/uvicorn app.main:app --reload --port 8000
AUTH_MODE=api_key .venv/bin/uvicorn app.main:app --port 8001   # another login type
```

Supabase: use the **Session pooler** host (IPv4). The direct `db.<ref>.supabase.co` host is IPv6-only.

Offline dev: set `DATABASE_URL=sqlite:///./local.db` in `.env`.

## Tests

```bash
.venv/bin/pytest -q
```

Runs against a temporary SQLite DB and covers every endpoint and status code.

## Roles

| Role | Can |
|---|---|
| staff | read catalog/inventory, create orders, cancel own orders, see own orders |
| manager | + create/update products, categories, suppliers, restock/adjust, order status, reports |
| admin | + manage users, delete products/categories/suppliers |

New registrations are `staff`; an admin promotes via `PATCH /api/v1/users/{id}/role`.

## Live URLs: three ways to log in

The same API and the same data behind three kinds of login, one URL each.
Switch the URL to see the same endpoint answer to a different login.

| URL | Branch | Login |
|---|---|---|
| https://dummyapi-nqa.vercel.app | `main` | Bearer token from a user login |
| https://dummyapi-nqa-apikey.vercel.app | `feat/auth-apikey` | API key |
| https://dummyapi-nqa-oauth2.vercel.app | `feat/auth-oauth2` | OAuth2 client credentials |

Each URL's `/openapi.json` declares only its own login, and `/docs` has an
**Authorize** button for it. Roles apply the same everywhere: a staff
user's key or client can't do what only a manager or admin may.

### 1. Bearer: https://dummyapi-nqa.vercel.app

Log in once with a user's email and password; send the token on every call.

```bash
curl -X POST https://dummyapi-nqa.vercel.app/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "bob@retailhub.in", "password": "Retail@2026"}'
# → {"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer", "expires_in": 900}

curl https://dummyapi-nqa.vercel.app/api/v1/products \
  -H "Authorization: Bearer <access_token>"
```

The token lasts 15 minutes. `POST /api/v1/auth/refresh` with
`{"refresh_token": "..."}` gives a new pair (each refresh token works once);
`POST /api/v1/auth/logout` revokes the token.

| Without it | Wrong or expired token |
|---|---|
| 401 `UNAUTHORIZED` | 401 `INVALID_TOKEN` / `TOKEN_EXPIRED` / `TOKEN_REVOKED` |

### 2. API key: https://dummyapi-nqa-apikey.vercel.app

No login: the key itself goes with every call, in any one of three places.

```bash
# header
curl https://dummyapi-nqa-apikey.vercel.app/api/v1/products \
  -H "X-API-Key: rk_test_admin_9f2c41d7a8e3"

# query string
curl "https://dummyapi-nqa-apikey.vercel.app/api/v1/products?api_key=rk_test_admin_9f2c41d7a8e3"

# cookie
curl https://dummyapi-nqa-apikey.vercel.app/api/v1/products \
  --cookie "retail_key=rk_test_admin_9f2c41d7a8e3"
```

A key acts as its owner (the owner's role applies). A `read` key may only
GET. Admins make keys with `POST /api/v1/api-keys` (the raw key is shown
once) and revoke them with `DELETE /api/v1/api-keys/{id}`.

| Without it | Unknown key | Revoked / expired key | Read-only key on a write |
|---|---|---|---|
| 401 `MISSING_API_KEY` | 401 `INVALID_API_KEY` | 401 `API_KEY_REVOKED` / `API_KEY_EXPIRED` | 403 `INSUFFICIENT_SCOPE` |

### 3. OAuth2 client credentials: https://dummyapi-nqa-oauth2.vercel.app

An app (not a person) gets a token with its client id and secret, sent as a
**form**, then sends the token like a Bearer token.

```bash
curl -X POST https://dummyapi-nqa-oauth2.vercel.app/api/v1/oauth/token \
  -d grant_type=client_credentials \
  -d client_id=cl_admin_app \
  -d client_secret=cs_test_admin_3f9e1c7a5b2d \
  -d "scope=read write"
# → {"access_token": "eyJ...", "token_type": "Bearer", "expires_in": 900, "scope": "read write"}

curl https://dummyapi-nqa-oauth2.vercel.app/api/v1/products \
  -H "Authorization: Bearer <access_token>"
```

The client id and secret may instead go as HTTP Basic
(`-u cl_admin_app:cs_test_admin_3f9e1c7a5b2d`). `scope` is optional: leave it
out for everything the client may have. There is no refresh token: ask for
a new token when one expires (15 minutes). Admins make clients with
`POST /api/v1/oauth/clients` (the secret is shown once) and disable them with
`DELETE /api/v1/oauth/clients/{client_id}`.

The token endpoint answers errors in the OAuth shape,
`{"error": "...", "error_description": "..."}`; the rest of the API in its own.

| Token request | Answer |
|---|---|
| no `grant_type` | 400 `invalid_request` |
| `grant_type` other than `client_credentials` | 400 `unsupported_grant_type` |
| unknown client, wrong secret, disabled client | 401 `invalid_client` |
| a scope the client doesn't have | 400 `invalid_scope` |

| Calling the API | Answer |
|---|---|
| no token | 401 `UNAUTHORIZED` |
| a user's login token instead of a client token | 401 `INVALID_TOKEN` |
| a `read` token on a write | 403 `INSUFFICIENT_SCOPE` |
| token of a client disabled since | 401 `INVALID_CLIENT` |

The keys and clients below exist once the migration and seed have run on
the database (`alembic upgrade head`, then `python -m app.seed`, or the
reset SQL).

## Login per deployment: `AUTH_MODE`

One codebase; each deployment picks how callers prove who they are, and
`/openapi.json` declares only that one. The endpoints and data are the same.

| `AUTH_MODE` | Every protected call sends | Extra endpoints |
|---|---|---|
| `bearer` (default) | `Authorization: Bearer <jwt>` from `POST /auth/login` | `/auth/login`, `/auth/refresh`, `/auth/logout` |
| `basic` | `Authorization: Basic base64(email:password)` | none (no login endpoint) |
| `api_key` | `X-API-Key: <key>`, or `?api_key=<key>`, or cookie `retail_key=<key>` | `/api-keys` (admin) |
| `oauth2` | `Authorization: Bearer <token>` from `POST /oauth/token` (form: `grant_type=client_credentials`, `client_id`, `client_secret`, `scope`) | `/oauth/token`, `/oauth/clients` (admin) |

Keys and clients act as their owner (the owner's role applies) and carry
scopes: `read` allows GET only, `read write` allows everything the role
does (otherwise 403 `INSUFFICIENT_SCOPE`). The token endpoint answers in
the OAuth error shape, `{"error": "invalid_client", "error_description": ...}`.

Test credentials made by `python -m app.seed` (owners from the reset SQL):

| Kind | Value | Acts as | Scopes |
|---|---|---|---|
| API key | `rk_test_admin_9f2c41d7a8e3` | admin | read write |
| API key | `rk_test_alice_4b8e19c2f0a6` | alice (manager) | read write |
| API key | `rk_test_bob_7d1a3e5c9b20` | bob (staff) | read write |
| API key | `rk_test_readonly_2e6f8a0c4d1b` | admin | read |
| API key | `rk_test_revoked_5c3b7e9a1f42` | bob, revoked | — |
| API key | `rk_test_expired_8a4d2c6e0b13` | bob, expired | — |
| OAuth client | `cl_admin_app` / `cs_test_admin_3f9e1c7a5b2d` | admin | read write |
| OAuth client | `cl_alice_reports` / `cs_test_alice_6a2d8f4c0e1b` | alice (manager) | read |
| OAuth client | `cl_bob_pos` / `cs_test_bob_1c5e9a3d7f20` | bob (staff) | read write |
| OAuth client | `cl_disabled` / `cs_test_disabled_4e8a2c6f0d3b` | bob, disabled | — |

Basic (`AUTH_MODE=basic`, not deployed) uses the users' own email and password.

## Errors

```json
{"error": {"code": "INSUFFICIENT_STOCK", "message": "...", "request_id": "...", "details": [...]}}
```

Every response carries `X-Request-ID` and `X-Process-Time` headers.
