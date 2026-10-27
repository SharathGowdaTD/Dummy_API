import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from threading import Lock

import bcrypt
import jwt

from app.config import get_settings
from app.errors import AppError

settings = get_settings()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def _create_token(user_id: int, role: str, token_type: str, lifetime: timedelta) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires = now + lifetime
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": expires,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires


def create_access_token(user_id: int, role: str) -> str:
    return _create_token(user_id, role, "access", timedelta(minutes=settings.access_token_minutes))[0]


def create_refresh_token(user_id: int, role: str) -> str:
    return _create_token(user_id, role, "refresh", timedelta(days=settings.refresh_token_days))[0]


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise AppError(401, "TOKEN_EXPIRED", "Token has expired", headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError:
        raise AppError(401, "INVALID_TOKEN", "Token is invalid", headers={"WWW-Authenticate": "Bearer"})
    if payload.get("type") != expected_type:
        raise AppError(401, "INVALID_TOKEN", f"Expected a {expected_type} token", headers={"WWW-Authenticate": "Bearer"})
    return payload


def new_reset_token() -> tuple[str, str]:
    """Returns (raw token for the user, sha256 hash for storage)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_reset_token(raw)


def hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class LoginRateLimiter:
    """In-memory sliding window per (client ip, email). Swap for Redis when running multiple workers."""

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and now - attempts[0] > self.window:
                attempts.popleft()
            if len(attempts) >= self.max_attempts:
                retry_after = int(self.window - (now - attempts[0])) + 1
                raise AppError(
                    429,
                    "TOO_MANY_LOGIN_ATTEMPTS",
                    f"Too many failed login attempts. Try again in {retry_after}s",
                    headers={"Retry-After": str(retry_after)},
                )

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._attempts[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


login_limiter = LoginRateLimiter(settings.login_max_attempts, settings.login_window_seconds)
