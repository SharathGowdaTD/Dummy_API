from tests.conftest import API, login


def test_health_and_page(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}
    assert client.get("/").status_code == 200


# ---------- auth ----------


def test_register_conflict_and_validation(client):
    body = {"email": "dup@retailhub.in", "password": "Passw0rd1", "full_name": "Dup"}
    assert client.post(f"{API}/auth/register", json=body).status_code == 201
    r = client.post(f"{API}/auth/register", json=body | {"email": "DUP@retailhub.in"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"
    r = client.post(f"{API}/auth/register", json={"email": "bad", "password": "short", "full_name": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_login_failures_and_rate_limit(client):
    client.post(f"{API}/auth/register", json={"email": "rl@retailhub.in", "password": "Passw0rd1", "full_name": "RL"})
    for _ in range(5):
        assert client.post(f"{API}/auth/login", json={"email": "rl@retailhub.in", "password": "wrong"}).status_code == 401
    r = client.post(f"{API}/auth/login", json={"email": "rl@retailhub.in", "password": "Passw0rd1"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_me_requires_auth(client, staff):
    assert client.get(f"{API}/auth/me").status_code == 401
    assert client.get(f"{API}/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    r = client.patch(f"{API}/auth/me", json={"phone": "+91 99999 00000"}, headers=staff)
    assert r.status_code == 200 and r.json()["role"] == "staff"


def test_refresh_rotation_and_logout(client):
    client.post(f"{API}/auth/register", json={"email": "tok@retailhub.in", "password": "Passw0rd1", "full_name": "Tok"})
    tokens = client.post(f"{API}/auth/login", json={"email": "tok@retailhub.in", "password": "Passw0rd1"}).json()
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["access_token"]}).status_code == 401
    new = r.json()
    headers = {"Authorization": f"Bearer {new['access_token']}"}
    assert client.post(f"{API}/auth/logout", json={"refresh_token": new["refresh_token"]}, headers=headers).status_code == 204
    r = client.get(f"{API}/auth/me", headers=headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_REVOKED"


def test_password_change_and_reset(client):
    client.post(f"{API}/auth/register", json={"email": "pw@retailhub.in", "password": "Passw0rd1", "full_name": "Pw"})
    headers = login(client, "pw@retailhub.in", "Passw0rd1")
    bad = client.post(f"{API}/auth/change-password", json={"old_password": "nope", "new_password": "Passw0rd2"}, headers=headers)
    assert bad.status_code == 400
    ok = client.post(f"{API}/auth/change-password", json={"old_password": "Passw0rd1", "new_password": "Passw0rd2"}, headers=headers)
    assert ok.status_code == 204

    r = client.post(f"{API}/auth/forgot-password", json={"email": "pw@retailhub.in"})
    assert r.status_code == 202 and r.json()["reset_token"]
    assert client.post(f"{API}/auth/forgot-password", json={"email": "ghost@retailhub.in"}).json()["reset_token"] is None
    token = r.json()["reset_token"]
    assert client.post(f"{API}/auth/reset-password", json={"token": token, "new_password": "Passw0rd3"}).status_code == 204
    assert client.post(f"{API}/auth/reset-password", json={"token": token, "new_password": "Passw0rd4"}).status_code == 400
    login(client, "pw@retailhub.in", "Passw0rd3")


# ---------- users ----------


def test_user_admin(client, admin, staff):
    assert client.get(f"{API}/users", headers=staff).status_code == 403
    users = client.get(f"{API}/users?role=admin", headers=admin).json()
    assert users["total"] == 1
    admin_id = users["items"][0]["id"]
    assert client.patch(f"{API}/users/{admin_id}/role", json={"role": "staff"}, headers=admin).status_code == 400
    assert client.get(f"{API}/users/99999", headers=admin).status_code == 404

    uid = client.post(f"{API}/auth/register", json={"email": "gone@retailhub.in", "password": "Passw0rd1", "full_name": "Gone"}).json()["id"]
    assert client.patch(f"{API}/users/{uid}/status", json={"is_active": False}, headers=admin).status_code == 200
    r = client.post(f"{API}/auth/login", json={"email": "gone@retailhub.in", "password": "Passw0rd1"})
    assert r.status_code == 403
    assert client.delete(f"{API}/users/{uid}", headers=admin).status_code == 204
    assert client.get(f"{API}/users/{uid}", headers=admin).status_code == 404


# ---------- catalog ----------


def test_categories(client, admin, manager, staff):
    assert len(client.get(f"{API}/categories", headers=staff).json()) == 3
    assert client.post(f"{API}/categories", json={"name": "Toys"}, headers=staff).status_code == 403
    r = client.post(f"{API}/categories", json={"name": "Toys"}, headers=manager)
    assert r.status_code == 201
    assert client.post(f"{API}/categories", json={"name": "toys"}, headers=manager).status_code == 409
    assert client.put(f"{API}/categories/{r.json()['id']}", json={"name": "Grocery"}, headers=manager).status_code == 409
    assert client.delete(f"{API}/categories/1", headers=admin).status_code == 409
    assert client.delete(f"{API}/categories/{r.json()['id']}", headers=admin).status_code == 204
    assert client.delete(f"{API}/categories/{r.json()['id']}", headers=admin).status_code == 404


def test_products(client, admin, manager, staff):
    r = client.get(f"{API}/products?sort=-price", headers=staff).json()
    assert r["total"] == 5 and r["items"][0]["sku"] == "ELEC-002"
    assert client.get(f"{API}/products?sort=bogus", headers=staff).status_code == 400
    assert client.get(f"{API}/products?min_price=500&max_price=100", headers=staff).status_code == 400
    assert client.get(f"{API}/products?in_stock=false", headers=staff).json()["items"][0]["sku"] == "APRL-001"
    assert client.get(f"{API}/products/sku/groc-002", headers=staff).json()["stock_status"] == "low_stock"
    assert client.get(f"{API}/products/sku/NOPE-1", headers=staff).status_code == 404

    new = {"sku": "home-001", "name": "Steel Bottle", "category_id": 1, "price": "549.00", "stock_quantity": 5}
    assert client.post(f"{API}/products", json=new, headers=staff).status_code == 403
    r = client.post(f"{API}/products", json=new, headers=manager)
    assert r.status_code == 201 and r.json()["sku"] == "HOME-001" and r.headers["Location"]
    pid = r.json()["id"]
    assert client.post(f"{API}/products", json=new, headers=manager).status_code == 409
    assert client.post(f"{API}/products", json=new | {"sku": "X-999", "category_id": 999}, headers=manager).status_code == 404
    assert client.post(f"{API}/products", json=new | {"sku": "X-998", "price": "-1"}, headers=manager).status_code == 422
    assert client.patch(f"{API}/products/{pid}", json={}, headers=manager).status_code == 400
    assert client.patch(f"{API}/products/{pid}", json={"price": "599.00"}, headers=manager).json()["price"] == "599.00"
    put = new | {"sku": "HOME-001", "name": "Steel Bottle 1L"}
    put.pop("stock_quantity")
    assert client.put(f"{API}/products/{pid}", json=put, headers=manager).json()["name"] == "Steel Bottle 1L"
    assert client.put(f"{API}/products/{pid}", json=put | {"sku": "ELEC-001"}, headers=manager).status_code == 409
    assert client.delete(f"{API}/products/{pid}", headers=manager).status_code == 403
    assert client.delete(f"{API}/products/{pid}", headers=admin).status_code == 204
    assert client.get(f"{API}/products/{pid}", headers=staff).status_code == 404


def test_suppliers(client, admin, manager, staff):
    assert len(client.get(f"{API}/suppliers", headers=staff).json()) == 2
    assert len(client.get(f"{API}/suppliers/1", headers=staff).json()["products"]) == 2
    r = client.post(f"{API}/suppliers", json={"name": "Temp Co"}, headers=manager)
    assert r.status_code == 201
    assert client.post(f"{API}/suppliers", json={"name": "temp co"}, headers=manager).status_code == 409
    assert client.delete(f"{API}/suppliers/{r.json()['id']}", headers=admin).status_code == 204
    assert client.get(f"{API}/suppliers/{r.json()['id']}", headers=staff).status_code == 404


# ---------- inventory & orders ----------


def _pid(client, headers, sku):
    return client.get(f"{API}/products/sku/{sku}", headers=headers).json()["id"]


def test_inventory(client, manager, staff):
    low = {i["sku"] for i in client.get(f"{API}/inventory/low-stock", headers=staff).json()}
    assert low == {"GROC-002", "APRL-001"}
    pid = _pid(client, staff, "GROC-002")
    assert client.post(f"{API}/inventory/{pid}/restock", json={"quantity": 50}, headers=staff).status_code == 403
    assert client.post(f"{API}/inventory/{pid}/restock", json={"quantity": 0}, headers=manager).status_code == 422
    assert client.post(f"{API}/inventory/{pid}/restock", json={"quantity": 50}, headers=manager).json()["stock_quantity"] == 58
    r = client.post(f"{API}/inventory/{pid}/adjust", json={"change": -1000, "reason": "audit"}, headers=manager)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    assert client.post(f"{API}/inventory/{pid}/adjust", json={"change": -3, "reason": "damaged"}, headers=manager).json()["stock_quantity"] == 55
    assert client.post(f"{API}/inventory/99999/restock", json={"quantity": 1}, headers=manager).status_code == 404
    moves = client.get(f"{API}/inventory/{pid}/movements", headers=staff).json()
    assert [m["movement_type"] for m in moves["items"]] == ["adjustment", "restock", "initial"]


def test_orders_flow(client, admin, manager, staff):
    assert client.post(f"{API}/orders", json={"items": []}, headers=staff).status_code == 400
    assert client.post(f"{API}/orders", json={"items": [{"sku": "NOPE-1", "quantity": 1}]}, headers=staff).status_code == 404
    r = client.post(f"{API}/orders", json={"items": [{"sku": "APRL-001", "quantity": 1}]}, headers=staff)
    assert r.status_code == 409 and r.json()["error"]["details"][0]["available"] == 0

    r = client.post(
        f"{API}/orders",
        json={"customer_name": "Walk-in", "items": [{"sku": "ELEC-001", "quantity": 2}, {"sku": "elec-001", "quantity": 1}, {"sku": "GROC-001", "quantity": 1}]},
        headers=staff,
    )
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["subtotal"] == "3046.00" and order["tax"] == "548.28" and order["total"] == "3594.28"
    assert client.get(f"{API}/products/sku/ELEC-001", headers=staff).json()["stock_quantity"] == 117

    other = client.post(f"{API}/auth/register", json={"email": "other@retailhub.in", "password": "Passw0rd1", "full_name": "Other"})
    other_h = login(client, "other@retailhub.in", "Passw0rd1")
    assert other.status_code == 201
    assert client.get(f"{API}/orders/{order['id']}", headers=other_h).status_code == 403
    assert client.get(f"{API}/orders", headers=other_h).json()["total"] == 0
    assert client.get(f"{API}/orders/99999", headers=staff).status_code == 404

    oid = order["id"]
    assert client.patch(f"{API}/orders/{oid}/status", json={"status": "paid"}, headers=staff).status_code == 403
    assert client.patch(f"{API}/orders/{oid}/status", json={"status": "fulfilled"}, headers=manager).status_code == 400
    assert client.patch(f"{API}/orders/{oid}/status", json={"status": "paid"}, headers=manager).json()["status"] == "paid"

    r2 = client.post(f"{API}/orders", json={"items": [{"sku": "ELEC-002", "quantity": 5}]}, headers=staff).json()
    assert client.post(f"{API}/orders/{r2['id']}/cancel", headers=staff).json()["status"] == "cancelled"
    assert client.post(f"{API}/orders/{r2['id']}/cancel", headers=staff).status_code == 409
    assert client.get(f"{API}/products/sku/ELEC-002", headers=staff).json()["stock_quantity"] == 45

    assert client.patch(f"{API}/orders/{oid}/status", json={"status": "fulfilled"}, headers=manager).status_code == 200
    assert client.post(f"{API}/orders/{oid}/cancel", headers=manager).status_code == 409

    assert client.get(f"{API}/reports/summary", headers=staff).status_code == 403
    s = client.get(f"{API}/reports/summary", headers=admin).json()
    assert s["revenue"] == "3594.28" and s["orders_by_status"]["cancelled"] == 1
    assert s["top_products"][0]["sku"] == "ELEC-001"
