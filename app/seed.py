"""Idempotent seed: python -m app.seed"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import ApiKey, Category, MovementType, OAuthClient, Product, Role, Supplier, User
from app.security import hash_password
from app.services import move_stock

CATEGORIES = [
    ("Electronics", "Gadgets, chargers and accessories"),
    ("Grocery", "Staples, oils and packaged food"),
    ("Apparel", "Clothing and basics"),
]

SUPPLIERS = [
    ("TechSource Distributors", "orders@techsource.in", "+91 80 4100 2000", "Bengaluru, KA"),
    ("FreshFarm Wholesale", "supply@freshfarm.in", "+91 22 6100 3000", "Mumbai, MH"),
]

# sku, name, category, supplier, price, cost, stock, reorder level
PRODUCTS = [
    ("ELEC-001", "Wireless Mouse", "Electronics", "TechSource Distributors", "799.00", "450.00", 120, 20),
    ("ELEC-002", "USB-C Charger 65W", "Electronics", "TechSource Distributors", "1999.00", "1200.00", 45, 10),
    ("GROC-001", "Basmati Rice 5kg", "Grocery", "FreshFarm Wholesale", "649.00", "480.00", 200, 30),
    ("GROC-002", "Cold Pressed Oil 1L", "Grocery", "FreshFarm Wholesale", "289.00", "190.00", 8, 15),
    ("APRL-001", "Cotton T-Shirt (M)", "Apparel", None, "499.00", "220.00", 0, 10),
]


# Test credentials for AUTH_MODE=api_key and oauth2. Known values on purpose:
# this is a dummy API for testing tools, and every test needs them. Owners
# that don't exist (alice and bob come from the reset SQL) are skipped.
# name, raw key, owner email, scopes, active, expired
TEST_KEYS = [
    ("Admin key", "rk_test_admin_9f2c41d7a8e3", "admin", "read write", True, False),
    ("Alice (manager) key", "rk_test_alice_4b8e19c2f0a6", "alice@retailhub.in", "read write", True, False),
    ("Bob (staff) key", "rk_test_bob_7d1a3e5c9b20", "bob@retailhub.in", "read write", True, False),
    ("Read-only admin key", "rk_test_readonly_2e6f8a0c4d1b", "admin", "read", True, False),
    ("Revoked key", "rk_test_revoked_5c3b7e9a1f42", "bob@retailhub.in", "read write", False, False),
    ("Expired key", "rk_test_expired_8a4d2c6e0b13", "bob@retailhub.in", "read write", True, True),
]
# name, client id, secret, owner email, scopes, active
TEST_CLIENTS = [
    ("Admin app", "cl_admin_app", "cs_test_admin_3f9e1c7a5b2d", "admin", "read write", True),
    ("Alice reports app", "cl_alice_reports", "cs_test_alice_6a2d8f4c0e1b", "alice@retailhub.in", "read", True),
    ("Bob POS app", "cl_bob_pos", "cs_test_bob_1c5e9a3d7f20", "bob@retailhub.in", "read write", True),
    ("Disabled app", "cl_disabled", "cs_test_disabled_4e8a2c6f0d3b", "bob@retailhub.in", "read write", False),
]


def seed_access(db, admin: User) -> None:
    """The test keys and clients, added once each."""
    def owner(email: str) -> User | None:
        return admin if email == "admin" else db.scalar(select(User).where(User.email == email))

    have = set(db.scalars(select(ApiKey.key_hash)))
    for name, raw, email, scopes, active, expired in TEST_KEYS:
        user, digest = owner(email), sha256(raw.encode()).hexdigest()
        if user is None or digest in have:
            continue
        db.add(ApiKey(name=name, prefix=raw[:10], key_hash=digest, user_id=user.id, scopes=scopes, is_active=active,
                      expires_at=datetime.now(UTC) - timedelta(days=1) if expired else None))
        print(f"created API key {name}")
    have = set(db.scalars(select(OAuthClient.client_id)))
    for name, client_id, secret, email, scopes, active in TEST_CLIENTS:
        user = owner(email)
        if user is None or client_id in have:
            continue
        db.add(OAuthClient(name=name, client_id=client_id, secret_hash=sha256(secret.encode()).hexdigest(),
                           user_id=user.id, scopes=scopes, is_active=active))
        print(f"created OAuth client {name}")


def seed() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == settings.seed_admin_email.lower()))
        if admin is None:
            admin = User(
                email=settings.seed_admin_email.lower(),
                full_name="Store Admin",
                hashed_password=hash_password(settings.seed_admin_password),
                role=Role.admin.value,
            )
            db.add(admin)
            db.flush()
            print(f"created admin {admin.email}")

        categories = {c.name: c for c in db.scalars(select(Category))}
        for name, description in CATEGORIES:
            if name not in categories:
                categories[name] = Category(name=name, description=description)
                db.add(categories[name])

        suppliers = {s.name: s for s in db.scalars(select(Supplier))}
        for name, email, phone, address in SUPPLIERS:
            if name not in suppliers:
                suppliers[name] = Supplier(name=name, email=email, phone=phone, address=address)
                db.add(suppliers[name])
        db.flush()

        existing = set(db.scalars(select(Product.sku)))
        for sku, name, category, supplier, price, cost, stock, reorder in PRODUCTS:
            if sku in existing:
                continue
            product = Product(
                sku=sku,
                name=name,
                category_id=categories[category].id,
                supplier_id=suppliers[supplier].id if supplier else None,
                price=Decimal(price),
                cost_price=Decimal(cost),
                stock_quantity=0,
                reorder_level=reorder,
            )
            db.add(product)
            db.flush()
            if stock:
                move_stock(db, product, stock, MovementType.initial, admin, "Initial stock")
            print(f"created product {sku}")

        seed_access(db, admin)
        db.commit()
    print("seed complete")


if __name__ == "__main__":
    seed()
