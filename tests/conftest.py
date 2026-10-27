import os
import tempfile

import pytest

_db_file = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["SEED_ADMIN_EMAIL"] = "admin@retailhub.in"
os.environ["SEED_ADMIN_PASSWORD"] = "Admin12345"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

API = "/api/v1"


@pytest.fixture(scope="session")
def client():
    Base.metadata.create_all(engine)
    seed()
    with TestClient(app) as c:
        yield c


def login(client, email, password) -> dict:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return login(client, "admin@retailhub.in", "Admin12345")


@pytest.fixture(scope="session")
def staff(client):
    client.post(f"{API}/auth/register", json={"email": "staff@retailhub.in", "password": "Staff12345", "full_name": "Staff One"})
    return login(client, "staff@retailhub.in", "Staff12345")


@pytest.fixture(scope="session")
def manager(client, admin):
    r = client.post(f"{API}/auth/register", json={"email": "mgr@retailhub.in", "password": "Manager123", "full_name": "Mgr One"})
    client.patch(f"{API}/users/{r.json()['id']}/role", json={"role": "manager"}, headers=admin)
    return login(client, "mgr@retailhub.in", "Manager123")
