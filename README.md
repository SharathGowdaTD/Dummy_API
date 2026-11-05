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

Basic uses the users' own email and password.

## Errors

```json
{"error": {"code": "INSUFFICIENT_STOCK", "message": "...", "request_id": "...", "details": [...]}}
```

Every response carries `X-Request-ID` and `X-Process-Time` headers.
