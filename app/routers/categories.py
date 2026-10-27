from fastapi import APIRouter, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.deps import DB, AdminUser, CurrentUser, ManagerUser
from app.errors import AppError, not_found
from app.models import Category, Product
from app.schemas import CategoryIn, CategoryOut

router = APIRouter(prefix="/categories", tags=["Categories"])


def _product_count(db, category_id: int) -> int:
    return db.scalar(
        select(func.count()).where(Product.category_id == category_id, Product.is_deleted.is_(False))
    )


def _out(db, category: Category) -> CategoryOut:
    return CategoryOut(
        id=category.id,
        name=category.name,
        description=category.description,
        product_count=_product_count(db, category.id),
    )


def _get(db, category_id: int) -> Category:
    category = db.get(Category, category_id)
    if category is None:
        raise not_found("Category")
    return category


def _ensure_unique(db, name: str, exclude_id: int | None = None) -> None:
    stmt = select(Category.id).where(func.lower(Category.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(Category.id != exclude_id)
    if db.scalar(stmt):
        raise AppError(409, "CATEGORY_EXISTS", f"Category '{name}' already exists")


@router.get("", response_model=list[CategoryOut])
def list_categories(db: DB, _: CurrentUser):
    return [_out(db, c) for c in db.scalars(select(Category).order_by(Category.name))]


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryIn, db: DB, _: ManagerUser):
    _ensure_unique(db, body.name)
    category = Category(**body.model_dump())
    db.add(category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "CATEGORY_EXISTS", f"Category '{body.name}' already exists")
    return _out(db, category)


@router.put("/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, body: CategoryIn, db: DB, _: ManagerUser):
    category = _get(db, category_id)
    _ensure_unique(db, body.name, exclude_id=category_id)
    category.name = body.name
    category.description = body.description
    db.commit()
    return _out(db, category)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, db: DB, _: AdminUser):
    category = _get(db, category_id)
    if db.scalar(select(func.count()).where(Product.category_id == category_id)):
        raise AppError(409, "CATEGORY_IN_USE", "Category still has products; move or delete them first")
    db.delete(category)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
