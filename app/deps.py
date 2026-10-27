from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import AppError
from app.models import ROLE_RANK, RevokedToken, Role, User
from app.security import decode_token

bearer = HTTPBearer(auto_error=False)

DB = Annotated[Session, Depends(get_db)]


def as_utc(dt: datetime | None) -> datetime | None:
    # SQLite drops tzinfo; Postgres keeps it. Normalise both to aware UTC.
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def _unauthorized(message: str, code: str = "UNAUTHORIZED") -> AppError:
    return AppError(401, code, message, headers={"WWW-Authenticate": "Bearer"})


def load_user_from_token(db: Session, token: str, token_type: str) -> tuple[User, dict]:
    payload = decode_token(token, token_type)
    if db.get(RevokedToken, payload["jti"]):
        raise _unauthorized("Token has been revoked", "TOKEN_REVOKED")
    user = db.get(User, int(payload["sub"]))
    if user is None or user.is_deleted:
        raise _unauthorized("User no longer exists")
    if not user.is_active:
        raise AppError(403, "USER_INACTIVE", "User account is deactivated")
    changed = as_utc(user.password_changed_at)
    if changed and payload["iat"] < int(changed.timestamp()):
        raise _unauthorized("Token was issued before the last password change", "TOKEN_REVOKED")
    return user, payload


def get_token_payload(
    db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> tuple[User, dict]:
    if creds is None:
        raise _unauthorized("Missing bearer token")
    return load_user_from_token(db, creds.credentials, "access")


def get_current_user(ctx: Annotated[tuple[User, dict], Depends(get_token_payload)]) -> User:
    return ctx[0]


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(minimum: Role):
    def checker(user: CurrentUser) -> User:
        if ROLE_RANK[Role(user.role)] < ROLE_RANK[minimum]:
            raise AppError(403, "INSUFFICIENT_ROLE", f"Requires {minimum.value} role or higher")
        return user

    return checker


StaffUser = Annotated[User, Depends(require_role(Role.staff))]
ManagerUser = Annotated[User, Depends(require_role(Role.manager))]
AdminUser = Annotated[User, Depends(require_role(Role.admin))]


def is_manager(user: User) -> bool:
    return ROLE_RANK[Role(user.role)] >= ROLE_RANK[Role.manager]
