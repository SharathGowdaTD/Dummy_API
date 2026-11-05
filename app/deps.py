import hashlib
import time
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasicCredentials
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth_modes
from app.auth_modes import CHALLENGE, basic, bearer, key_cookie, key_header, key_query, oauth2
from app.database import get_db
from app.errors import AppError
from app.models import ROLE_RANK, ApiKey, OAuthClient, RevokedToken, Role, User
from app.security import decode_token, login_limiter, verify_password

DB = Annotated[Session, Depends(get_db)]


def as_utc(dt: datetime | None) -> datetime | None:
    # SQLite drops tzinfo; Postgres keeps it. Normalise both to aware UTC.
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


def _unauthorized(message: str, code: str = "UNAUTHORIZED") -> AppError:
    return AppError(401, code, message, headers={"WWW-Authenticate": CHALLENGE[auth_modes.mode()]})


def sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _usable(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.is_deleted:
        raise _unauthorized("User no longer exists")
    if not user.is_active:
        raise AppError(403, "USER_INACTIVE", "User account is deactivated")
    return user


# bcrypt takes ~0.25 s, and Basic sends the password on every call: a
# checked (email, password) is trusted for a minute, unless the password
# changed in between.
_basic_seen: dict[tuple[str, str], tuple[int, float, float]] = {}
_basic_lock = Lock()


def _basic_user(db: Session, request: Request, creds: HTTPBasicCredentials) -> User:
    email, secret = creds.username.strip().lower(), sha256(creds.password)
    now = time.monotonic()
    with _basic_lock:
        hit = _basic_seen.get((email, secret))
    if hit and now - hit[1] < 60:
        user = _usable(db, hit[0])
        changed = as_utc(user.password_changed_at)
        if not changed or changed.timestamp() <= hit[2]:
            return user
    limiter_key = f"{request.client.host if request.client else '-'}:{email}"
    login_limiter.check(limiter_key)
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None or user.is_deleted or not verify_password(creds.password, user.hashed_password):
        login_limiter.record_failure(limiter_key)
        raise _unauthorized("Invalid email or password", "INVALID_CREDENTIALS")
    login_limiter.reset(limiter_key)
    user = _usable(db, user.id)
    with _basic_lock:
        _basic_seen[(email, secret)] = (user.id, now, time.time())
    return user


def _key_user(db: Session, raw: str) -> tuple[User, str]:
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == sha256(raw)))
    if key is None:
        raise _unauthorized("API key is not valid", "INVALID_API_KEY")
    if not key.is_active:
        raise _unauthorized("API key has been revoked", "API_KEY_REVOKED")
    expires = as_utc(key.expires_at)
    if expires and expires < datetime.now(UTC):
        raise _unauthorized("API key has expired", "API_KEY_EXPIRED")
    return _usable(db, key.user_id), key.scopes


def _client_user(db: Session, token: str) -> tuple[User, dict]:
    payload = decode_token(token, "client")
    client = db.scalar(select(OAuthClient).where(OAuthClient.client_id == payload.get("cid")))
    if client is None or not client.is_active:
        raise _unauthorized("The client of this token is no longer active", "INVALID_CLIENT")
    return _usable(db, int(payload["sub"])), payload


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


_SAFE = ("GET", "HEAD", "OPTIONS")


def get_token_payload(
    request: Request,
    db: DB,
    token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    login: Annotated[HTTPBasicCredentials | None, Depends(basic)],
    header_key: Annotated[str | None, Depends(key_header)],
    query_key: Annotated[str | None, Depends(key_query)],
    cookie_key: Annotated[str | None, Depends(key_cookie)],
    client_token: Annotated[str | None, Depends(oauth2)],
) -> tuple[User, dict]:
    """The caller, by the deployment's AUTH_MODE; credentials of any other
    kind are ignored, as if they weren't sent."""
    mode = auth_modes.mode()
    if mode == "bearer":
        if token is None:
            raise _unauthorized("Missing bearer token")
        return load_user_from_token(db, token.credentials, "access")
    if mode == "basic":
        if login is None:
            raise _unauthorized("Send your email and password with HTTP Basic auth")
        return _basic_user(db, request, login), {"via": "basic", "scope": "read write"}
    if mode == "api_key":
        raw = header_key or query_key or cookie_key
        if not raw:
            raise _unauthorized(f"Send an API key in the {auth_modes.KEY_HEADER} header", "MISSING_API_KEY")
        user, scopes = _key_user(db, raw)
        payload = {"via": "api_key", "scope": scopes}
    else:
        if not client_token:
            raise _unauthorized("Send a client token: POST /oauth/token with client credentials")
        scheme, _, value = client_token.partition(" ")
        user, payload = _client_user(db, value if scheme.lower() == "bearer" and value else client_token)
    # A read-only key or client may look but not change anything.
    if request.method not in _SAFE and "write" not in str(payload.get("scope", "")).split():
        raise AppError(403, "INSUFFICIENT_SCOPE", "This key or client may only read (needs the write scope)",
                       headers={"WWW-Authenticate": f'{CHALLENGE[mode]}, error="insufficient_scope", scope="write"'})
    return user, payload


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
