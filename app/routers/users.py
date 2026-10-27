from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select

from app.deps import DB, AdminUser
from app.errors import AppError, not_found
from app.models import Role, User
from app.schemas import Page, RoleIn, StatusIn, UserOut
from app.services import MAX_PAGE_SIZE, paginate

router = APIRouter(prefix="/users", tags=["Users (admin)"])


def _get_user(db, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.is_deleted:
        raise not_found("User")
    return user


def _not_self(admin: User, user: User, action: str) -> None:
    if admin.id == user.id:
        raise AppError(400, "CANNOT_MODIFY_SELF", f"You cannot {action} your own account")


@router.get("", response_model=Page[UserOut])
def list_users(
    db: DB,
    _: AdminUser,
    role: Role | None = None,
    is_active: bool | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Search email or name")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
):
    stmt = select(User).where(User.is_deleted.is_(False)).order_by(User.id)
    if role:
        stmt = stmt.where(User.role == role.value)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q}%") | User.full_name.ilike(f"%{q}%"))
    return paginate(db, stmt, page, size)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: DB, _: AdminUser):
    return _get_user(db, user_id)


@router.patch("/{user_id}/role", response_model=UserOut)
def change_role(user_id: int, body: RoleIn, db: DB, admin: AdminUser):
    user = _get_user(db, user_id)
    _not_self(admin, user, "change the role of")
    user.role = body.role.value
    db.commit()
    return user


@router.patch("/{user_id}/status", response_model=UserOut)
def change_status(user_id: int, body: StatusIn, db: DB, admin: AdminUser):
    user = _get_user(db, user_id)
    _not_self(admin, user, "deactivate")
    user.is_active = body.is_active
    db.commit()
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: DB, admin: AdminUser):
    user = _get_user(db, user_id)
    _not_self(admin, user, "delete")
    user.is_deleted = True
    user.is_active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
