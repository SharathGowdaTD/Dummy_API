import secrets
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.deps import DB, CurrentUser, ManagerUser, StaffUser, is_manager
from app.errors import AppError, not_found
from app.models import MovementType, Order, OrderItem, OrderStatus, Product, User
from app.schemas import OrderCreate, OrderOut, OrderStatusIn, Page
from app.services import MAX_PAGE_SIZE, money, move_stock, paginate

router = APIRouter(prefix="/orders", tags=["Orders"])
settings = get_settings()

# Allowed forward transitions via PATCH /status (cancellation has its own endpoint).
TRANSITIONS = {
    OrderStatus.pending: {OrderStatus.paid},
    OrderStatus.paid: {OrderStatus.fulfilled},
}


def _order_number() -> str:
    return f"ORD-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(3).upper()}"


def _get_visible(db, order_id: int, user: User) -> Order:
    order = db.scalar(select(Order).options(selectinload(Order.items)).where(Order.id == order_id))
    if order is None:
        raise not_found("Order")
    if not is_manager(user) and order.user_id != user.id:
        raise AppError(403, "ORDER_FORBIDDEN", "You can only access your own orders")
    return order


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(body: OrderCreate, db: DB, user: StaffUser, response: Response):
    if not body.items:
        raise AppError(400, "EMPTY_ORDER", "Order must contain at least one item")

    quantities = Counter()
    for item in body.items:
        quantities[item.sku] += item.quantity

    # Lock rows in a stable order so concurrent orders can't oversell or deadlock.
    products = {
        p.sku: p
        for p in db.scalars(
            select(Product)
            .where(Product.sku.in_(quantities), Product.is_deleted.is_(False))
            .order_by(Product.id)
            .with_for_update()
        )
    }
    missing = sorted(set(quantities) - set(products))
    if missing:
        raise AppError(404, "PRODUCT_NOT_FOUND", f"Unknown SKU(s): {', '.join(missing)}")
    inactive = sorted(sku for sku, p in products.items() if not p.is_active)
    if inactive:
        raise AppError(409, "PRODUCT_INACTIVE", f"Not available for sale: {', '.join(inactive)}")
    short = [
        {"sku": sku, "requested": qty, "available": products[sku].stock_quantity}
        for sku, qty in quantities.items()
        if products[sku].stock_quantity < qty
    ]
    if short:
        raise AppError(409, "INSUFFICIENT_STOCK", "Not enough stock for one or more items", details=short)

    order = Order(
        order_number=_order_number(),
        user_id=user.id,
        customer_name=body.customer_name,
        status=OrderStatus.pending.value,
        subtotal=Decimal(0),
        tax=Decimal(0),
        total=Decimal(0),
    )
    db.add(order)
    db.flush()

    subtotal = Decimal(0)
    for sku, qty in quantities.items():
        product = products[sku]
        line_total = money(product.price * qty)
        subtotal += line_total
        order.items.append(
            OrderItem(
                product_id=product.id,
                sku=sku,
                product_name=product.name,
                unit_price=product.price,
                quantity=qty,
                line_total=line_total,
            )
        )
        move_stock(db, product, -qty, MovementType.sale, user, f"Order {order.order_number}", order.id)

    order.subtotal = money(subtotal)
    order.tax = money(subtotal * settings.tax_rate)
    order.total = order.subtotal + order.tax
    db.commit()
    response.headers["Location"] = f"{router.prefix}/{order.id}"
    return _get_visible(db, order.id, user)


@router.get("", response_model=Page[OrderOut])
def list_orders(
    db: DB,
    user: CurrentUser,
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
):
    if date_from and date_to and date_from > date_to:
        raise AppError(400, "INVALID_DATE_RANGE", "date_from cannot be after date_to")
    stmt = select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc(), Order.id.desc())
    if not is_manager(user):
        stmt = stmt.where(Order.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Order.status == status_filter.value)
    if date_from:
        stmt = stmt.where(Order.created_at >= datetime.combine(date_from, time.min, UTC))
    if date_to:
        stmt = stmt.where(Order.created_at < datetime.combine(date_to + timedelta(days=1), time.min, UTC))
    return paginate(db, stmt, page, size)


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: DB, user: CurrentUser):
    return _get_visible(db, order_id, user)


@router.patch("/{order_id}/status", response_model=OrderOut)
def update_status(order_id: int, body: OrderStatusIn, db: DB, user: ManagerUser):
    order = _get_visible(db, order_id, user)
    current = OrderStatus(order.status)
    if body.status == OrderStatus.cancelled:
        raise AppError(400, "USE_CANCEL_ENDPOINT", "Use POST /orders/{id}/cancel to cancel an order")
    if body.status not in TRANSITIONS.get(current, set()):
        raise AppError(400, "INVALID_STATUS_TRANSITION", f"Cannot move order from {current.value} to {body.status.value}")
    order.status = body.status.value
    db.commit()
    return order


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, db: DB, user: StaffUser):
    order = _get_visible(db, order_id, user)
    if order.status in (OrderStatus.cancelled.value, OrderStatus.fulfilled.value):
        raise AppError(409, "ORDER_NOT_CANCELLABLE", f"Order is already {order.status}")
    products = {
        p.id: p
        for p in db.scalars(
            select(Product).where(Product.id.in_([i.product_id for i in order.items])).order_by(Product.id).with_for_update()
        )
    }
    for item in order.items:
        move_stock(
            db, products[item.product_id], item.quantity, MovementType.cancellation, user,
            f"Cancelled {order.order_number}", order.id,
        )
    order.status = OrderStatus.cancelled.value
    db.commit()
    return order
