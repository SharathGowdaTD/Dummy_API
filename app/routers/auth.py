from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.deps import DB, CurrentUser, as_utc, get_token_payload, load_user_from_token
from app.errors import AppError
from app.models import PasswordResetToken, RevokedToken, Role, User
from app.schemas import (
    ChangePasswordIn,
    ForgotPasswordIn,
    ForgotPasswordOut,
    LoginIn,
    LogoutIn,
    RefreshIn,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
    UserUpdateIn,
)
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_reset_token,
    login_limiter,
    new_reset_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Auth"])
settings = get_settings()


def _issue_tokens(user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id, user.role),
        expires_in=settings.access_token_minutes * 60,
    )


def _revoke(db, payload: dict) -> None:
    if not db.get(RevokedToken, payload["jti"]):
        db.add(RevokedToken(jti=payload["jti"], expires_at=datetime.fromtimestamp(payload["exp"], UTC)))


def _find_by_email(db, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.lower()))


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: DB):
    if _find_by_email(db, body.email):
        raise AppError(409, "EMAIL_ALREADY_REGISTERED", "An account with this email already exists")
    user = User(
        email=body.email.lower(),
        full_name=body.full_name,
        phone=body.phone,
        hashed_password=hash_password(body.password),
        role=Role.staff.value,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "EMAIL_ALREADY_REGISTERED", "An account with this email already exists")
    return user


@router.post(
    "/login",
    response_model=TokenOut,
    responses={401: {"description": "Bad credentials"}, 403: {"description": "Inactive"}, 429: {"description": "Rate limited"}},
)
def login(body: LoginIn, request: Request, db: DB):
    key = f"{request.client.host if request.client else '-'}:{body.email.lower()}"
    login_limiter.check(key)
    user = _find_by_email(db, body.email)
    if user is None or user.is_deleted or not verify_password(body.password, user.hashed_password):
        login_limiter.record_failure(key)
        raise AppError(401, "INVALID_CREDENTIALS", "Incorrect email or password")
    if not user.is_active:
        raise AppError(403, "USER_INACTIVE", "User account is deactivated")
    login_limiter.reset(key)
    return _issue_tokens(user)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: DB):
    user, payload = load_user_from_token(db, body.refresh_token, "refresh")
    _revoke(db, payload)  # rotation: each refresh token is single-use
    db.commit()
    return _issue_tokens(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(ctx: Annotated[tuple[User, dict], Depends(get_token_payload)], db: DB, body: LogoutIn | None = None):
    _revoke(db, ctx[1])
    if body and body.refresh_token:
        try:
            _revoke(db, decode_token(body.refresh_token, "refresh"))
        except AppError:
            pass  # an invalid refresh token is already unusable
    db.execute(delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(UTC)))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: UserUpdateIn, user: CurrentUser, db: DB):
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: ChangePasswordIn, user: CurrentUser, db: DB):
    if not verify_password(body.old_password, user.hashed_password):
        raise AppError(400, "INVALID_OLD_PASSWORD", "Old password is incorrect")
    if body.old_password == body.new_password:
        raise AppError(400, "PASSWORD_UNCHANGED", "New password must differ from the old one")
    user.hashed_password = hash_password(body.new_password)
    user.password_changed_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/forgot-password", response_model=ForgotPasswordOut, status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: ForgotPasswordIn, db: DB):
    out = ForgotPasswordOut(message="If that email is registered, a reset link has been sent")
    user = _find_by_email(db, body.email)
    if user and user.is_active and not user.is_deleted:
        raw, hashed = new_reset_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hashed,
                expires_at=datetime.now(UTC) + timedelta(minutes=settings.reset_token_minutes),
            )
        )
        db.commit()
        if settings.expose_reset_token:
            out.reset_token = raw
    return out


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: ResetPasswordIn, db: DB):
    record = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_reset_token(body.token)))
    now = datetime.now(UTC)
    if record is None or record.used_at is not None or as_utc(record.expires_at) < now:
        raise AppError(400, "INVALID_RESET_TOKEN", "Reset token is invalid or has expired")
    user = db.get(User, record.user_id)
    user.hashed_password = hash_password(body.new_password)
    user.password_changed_at = now
    record.used_at = now
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
