from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import DB, ManagerUser
from app.models import Order, OrderItem, OrderStatus, Product
from app.schemas import SummaryOut, TopProduct
from app.services import money

router = APIRouter(prefix="/reports", tags=["Reports"])
settings = get_settings()

REVENUE_STATUSES = [OrderStatus.paid.value, OrderStatus.fulfilled.value]


@router.get("/summary", response_model=SummaryOut)
def summary(db: DB, _: ManagerUser):
    revenue = db.scalar(select(func.coalesce(func.sum(Order.total), 0)).where(Order.status.in_(REVENUE_STATUSES)))
    by_status = {s.value: 0 for s in OrderStatus}
    by_status.update(dict(db.execute(select(Order.status, func.count()).group_by(Order.status)).all()))

    top_rows = db.execute(
        select(
            OrderItem.sku,
            OrderItem.product_name,
            func.sum(OrderItem.quantity).label("units"),
            func.sum(OrderItem.line_total).label("revenue"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.status != OrderStatus.cancelled.value)
        .group_by(OrderItem.sku, OrderItem.product_name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(5)
    ).all()

    products = db.scalars(select(Product).where(Product.is_deleted.is_(False))).all()
    retail_value = sum((p.price * p.stock_quantity for p in products), Decimal(0))
    cost_value = sum(((p.cost_price or 0) * p.stock_quantity for p in products), Decimal(0))

    return SummaryOut(
        currency=settings.currency,
        revenue=money(Decimal(revenue)),
        orders_by_status=by_status,
        total_orders=sum(by_status.values()),
        top_products=[
            TopProduct(sku=r.sku, name=r.product_name, units_sold=r.units, revenue=money(Decimal(r.revenue)))
            for r in top_rows
        ],
        inventory_retail_value=money(retail_value),
        inventory_cost_value=money(Decimal(cost_value)),
        total_skus=len(products),
        low_stock_skus=sum(1 for p in products if 0 < p.stock_quantity <= p.reorder_level),
        out_of_stock_skus=sum(1 for p in products if p.stock_quantity <= 0),
    )
