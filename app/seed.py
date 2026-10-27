"""Idempotent seed: python -m app.seed"""

from decimal import Decimal

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Category, MovementType, Product, Role, Supplier, User
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

        db.commit()
    print("seed complete")


if __name__ == "__main__":
    seed()
