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

## Errors

```json
{"error": {"code": "INSUFFICIENT_STOCK", "message": "...", "request_id": "...", "details": [...]}}
```

Every response carries `X-Request-ID` and `X-Process-Time` headers.
