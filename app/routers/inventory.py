from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.deps import DB, CurrentUser, ManagerUser
from app.models import MovementType, Product, StockMovement
from app.schemas import AdjustIn, InventoryItem, MovementOut, Page, ProductOut, RestockIn
from app.services import MAX_PAGE_SIZE, get_product, money, move_stock, paginate, product_out, stock_status

router = APIRouter(prefix="/inventory", tags=["Inventory"])


def _item(p: Product) -> InventoryItem:
    return InventoryItem(
        product_id=p.id,
        sku=p.sku,
        name=p.name,
        stock_quantity=p.stock_quantity,
        reorder_level=p.reorder_level,
        stock_status=stock_status(p),
        stock_value=money(p.price * p.stock_quantity),
    )


def _active_products(db) -> list[Product]:
    return db.scalars(select(Product).where(Product.is_deleted.is_(False)).order_by(Product.sku)).all()


@router.get("", response_model=list[InventoryItem])
def stock_levels(db: DB, _: CurrentUser):
    return [_item(p) for p in _active_products(db)]


@router.get("/low-stock", response_model=list[InventoryItem])
def low_stock(db: DB, _: CurrentUser):
    return [_item(p) for p in _active_products(db) if p.stock_quantity <= p.reorder_level]


@router.post("/{product_id}/restock", response_model=ProductOut)
def restock(product_id: int, body: RestockIn, db: DB, user: ManagerUser):
    product = get_product(db, product_id, for_update=True)
    move_stock(db, product, body.quantity, MovementType.restock, user, body.reason)
    db.commit()
    db.refresh(product)
    return product_out(product)


@router.post("/{product_id}/adjust", response_model=ProductOut)
def adjust(product_id: int, body: AdjustIn, db: DB, user: ManagerUser):
    product = get_product(db, product_id, for_update=True)
    move_stock(db, product, body.change, MovementType.adjustment, user, body.reason)
    db.commit()
    db.refresh(product)
    return product_out(product)


@router.get("/{product_id}/movements", response_model=Page[MovementOut])
def movements(
    product_id: int,
    db: DB,
    _: CurrentUser,
    movement_type: MovementType | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
):
    get_product(db, product_id)
    stmt = (
        select(StockMovement)
        .where(StockMovement.product_id == product_id)
        .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
    )
    if movement_type:
        stmt = stmt.where(StockMovement.movement_type == movement_type.value)
    return paginate(db, stmt, page, size)
