from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.deps import DB, AdminUser, CurrentUser, ManagerUser
from app.errors import AppError, not_found
from app.models import Category, MovementType, Product, Supplier
from app.schemas import Page, ProductCreate, ProductOut, ProductPatch, ProductReplace
from app.services import MAX_PAGE_SIZE, get_product, move_stock, paginate, product_out

router = APIRouter(prefix="/products", tags=["Products"])

SORT_FIELDS = {
    "name": Product.name,
    "sku": Product.sku,
    "price": Product.price,
    "stock_quantity": Product.stock_quantity,
    "created_at": Product.created_at,
}


def _check_refs(db, category_id: int | None, supplier_id: int | None) -> None:
    if category_id is not None and db.get(Category, category_id) is None:
        raise not_found("Category")
    if supplier_id is not None and db.get(Supplier, supplier_id) is None:
        raise not_found("Supplier")


def _ensure_sku_free(db, sku: str, exclude_id: int | None = None) -> None:
    stmt = select(Product).where(Product.sku == sku)
    if exclude_id:
        stmt = stmt.where(Product.id != exclude_id)
    existing = db.scalar(stmt)
    if existing:
        suffix = " (archived)" if existing.is_deleted else ""
        raise AppError(409, "SKU_EXISTS", f"SKU {sku} is already in use{suffix}")


@router.get("", response_model=Page[ProductOut])
def list_products(
    db: DB,
    _: CurrentUser,
    q: Annotated[str | None, Query(max_length=100, description="Search by name or SKU")] = None,
    category_id: int | None = None,
    supplier_id: int | None = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    in_stock: bool | None = None,
    is_active: bool | None = None,
    sort: Annotated[str, Query(description="Field, prefix with - for descending. e.g. -price")] = "sku",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
):
    field = sort.lstrip("-")
    if field not in SORT_FIELDS:
        raise AppError(400, "INVALID_SORT_FIELD", f"Cannot sort by '{field}'. Allowed: {', '.join(SORT_FIELDS)}")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise AppError(400, "INVALID_PRICE_RANGE", "min_price cannot be greater than max_price")

    column = SORT_FIELDS[field]
    stmt = (
        select(Product)
        .options(joinedload(Product.category))
        .where(Product.is_deleted.is_(False))
        .order_by(column.desc() if sort.startswith("-") else column.asc(), Product.id)
    )
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%") | Product.sku.ilike(f"%{q}%"))
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    if supplier_id is not None:
        stmt = stmt.where(Product.supplier_id == supplier_id)
    if min_price is not None:
        stmt = stmt.where(Product.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Product.price <= max_price)
    if in_stock is not None:
        stmt = stmt.where(Product.stock_quantity > 0 if in_stock else Product.stock_quantity <= 0)
    if is_active is not None:
        stmt = stmt.where(Product.is_active.is_(is_active))

    result = paginate(db, stmt, page, size)
    result["items"] = [product_out(p) for p in result["items"]]
    return result


@router.get("/sku/{sku}", response_model=ProductOut)
def get_by_sku(sku: str, db: DB, _: CurrentUser):
    product = db.scalar(select(Product).where(Product.sku == sku.upper(), Product.is_deleted.is_(False)))
    if product is None:
        raise AppError(404, "PRODUCT_NOT_FOUND", f"No product with SKU {sku.upper()}")
    return product_out(product)


@router.get("/{product_id}", response_model=ProductOut)
def get_product_by_id(product_id: int, db: DB, _: CurrentUser):
    return product_out(get_product(db, product_id))


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(body: ProductCreate, db: DB, user: ManagerUser, response: Response):
    _ensure_sku_free(db, body.sku)
    _check_refs(db, body.category_id, body.supplier_id)
    data = body.model_dump(exclude={"stock_quantity"})
    product = Product(**data, stock_quantity=0)
    db.add(product)
    db.flush()
    if body.stock_quantity:
        move_stock(db, product, body.stock_quantity, MovementType.initial, user, "Initial stock")
    db.commit()
    db.refresh(product)
    response.headers["Location"] = f"{router.prefix}/{product.id}"
    return product_out(product)


@router.put("/{product_id}", response_model=ProductOut)
def replace_product(product_id: int, body: ProductReplace, db: DB, _: ManagerUser):
    product = get_product(db, product_id)
    _ensure_sku_free(db, body.sku, exclude_id=product_id)
    _check_refs(db, body.category_id, body.supplier_id)
    for field, value in body.model_dump().items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product_out(product)


@router.patch("/{product_id}", response_model=ProductOut)
def patch_product(product_id: int, body: ProductPatch, db: DB, _: ManagerUser):
    product = get_product(db, product_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise AppError(400, "EMPTY_UPDATE", "Provide at least one field to update")
    if changes.get("category_id") is None:
        changes.pop("category_id", None)  # category is mandatory; null means "leave as is"
    _check_refs(db, changes.get("category_id"), changes.get("supplier_id"))
    for field, value in changes.items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product_out(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: DB, _: AdminUser):
    product = get_product(db, product_id)
    product.is_deleted = True
    product.is_active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
