from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from app.deps import DB, AdminUser, CurrentUser, ManagerUser
from app.errors import AppError, not_found
from app.models import Product, Supplier
from app.schemas import ProductBrief, SupplierDetailOut, SupplierIn, SupplierOut

router = APIRouter(prefix="/suppliers", tags=["Suppliers"])


def _get(db, supplier_id: int) -> Supplier:
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise not_found("Supplier")
    return supplier


@router.get("", response_model=list[SupplierOut])
def list_suppliers(db: DB, _: CurrentUser):
    return db.scalars(select(Supplier).order_by(Supplier.name)).all()


@router.post("", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(body: SupplierIn, db: DB, _: ManagerUser):
    if db.scalar(select(Supplier.id).where(func.lower(Supplier.name) == body.name.lower())):
        raise AppError(409, "SUPPLIER_EXISTS", f"Supplier '{body.name}' already exists")
    supplier = Supplier(**body.model_dump())
    db.add(supplier)
    db.commit()
    return supplier


@router.get("/{supplier_id}", response_model=SupplierDetailOut)
def get_supplier(supplier_id: int, db: DB, _: CurrentUser):
    supplier = _get(db, supplier_id)
    products = db.scalars(
        select(Product).where(Product.supplier_id == supplier_id, Product.is_deleted.is_(False)).order_by(Product.sku)
    ).all()
    return SupplierDetailOut(
        **SupplierOut.model_validate(supplier).model_dump(),
        products=[ProductBrief.model_validate(p) for p in products],
    )


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supplier(supplier_id: int, db: DB, _: AdminUser):
    supplier = _get(db, supplier_id)
    for product in supplier.products:
        product.supplier_id = None
    db.delete(supplier)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
