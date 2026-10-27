import math
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.errors import AppError, not_found
from app.models import MovementType, Product, StockMovement, User
from app.schemas import ProductOut

MAX_PAGE_SIZE = 100


def paginate(db: Session, stmt: Select, page: int, size: int) -> dict:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    items = db.scalars(stmt.offset((page - 1) * size).limit(size)).all()
    return {"items": items, "total": total, "page": page, "size": size, "pages": math.ceil(total / size) if size else 0}


def stock_status(product: Product) -> str:
    if product.stock_quantity <= 0:
        return "out_of_stock"
    if product.stock_quantity <= product.reorder_level:
        return "low_stock"
    return "in_stock"


def product_out(product: Product) -> ProductOut:
    data = {k: getattr(product, k) for k in ProductOut.model_fields if hasattr(product, k)}
    data["stock_status"] = stock_status(product)
    data["category_name"] = product.category.name if product.category else None
    return ProductOut(**data)


def get_product(db: Session, product_id: int, for_update: bool = False) -> Product:
    stmt = select(Product).where(Product.id == product_id, Product.is_deleted.is_(False))
    if for_update:
        stmt = stmt.with_for_update()
    product = db.scalar(stmt)
    if product is None:
        raise not_found("Product")
    return product


def move_stock(
    db: Session,
    product: Product,
    change: int,
    movement_type: MovementType,
    user: User | None,
    reason: str | None = None,
    order_id: int | None = None,
) -> StockMovement:
    new_balance = product.stock_quantity + change
    if new_balance < 0:
        raise AppError(
            400 if movement_type == MovementType.adjustment else 409,
            "INSUFFICIENT_STOCK",
            f"Only {product.stock_quantity} units of {product.sku} available",
        )
    product.stock_quantity = new_balance
    movement = StockMovement(
        product_id=product.id,
        change=change,
        balance_after=new_balance,
        movement_type=movement_type.value,
        reason=reason,
        user_id=user.id if user else None,
        order_id=order_id,
    )
    db.add(movement)
    return movement


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))
