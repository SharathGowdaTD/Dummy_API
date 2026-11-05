"""API keys (AUTH_MODE=api_key) and OAuth2 client credentials (oauth2).

Each set of routes exists only in its mode. Making and revoking keys and
clients is for admins, who prove who they are the same way as everyone
else in that mode: with an admin's key, or an admin client's token.
"""
import base64
import binascii
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.auth_modes import only_in
from app.config import get_settings
from app.deps import DB, AdminUser, sha256
from app.errors import AppError, not_found
from app.models import ApiKey, OAuthClient, User
from app.schemas import (ApiKeyCreated, ApiKeyIn, ApiKeyOut, ClientTokenOut, OAuthClientCreated, OAuthClientIn,
                         OAuthClientOut)
from app.security import create_client_token

settings = get_settings()
keys = APIRouter(prefix="/api-keys", tags=["API keys"], dependencies=[only_in("api_key")])
oauth = APIRouter(prefix="/oauth", tags=["OAuth2"], dependencies=[only_in("oauth2")])


def _owner(db, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.is_deleted:
        raise not_found("User")
    return user


# ── API keys ──────────────────────────────────────────────────────────────

@keys.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_key(body: ApiKeyIn, _: AdminUser, db: DB):
    _owner(db, body.user_id)
    raw = "rk_" + secrets.token_urlsafe(24)
    key = ApiKey(name=body.name, prefix=raw[:10], key_hash=sha256(raw), user_id=body.user_id,
                 scopes=" ".join(sorted(set(body.scopes))),
                 expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    db.add(key)
    db.commit()
    return ApiKeyCreated.model_validate({**ApiKeyOut.model_validate(key).model_dump(), "key": raw})


@keys.get("", response_model=list[ApiKeyOut])
def list_keys(_: AdminUser, db: DB):
    return list(db.scalars(select(ApiKey).order_by(ApiKey.id)))


@keys.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(key_id: int, _: AdminUser, db: DB):
    key = db.get(ApiKey, key_id)
    if key is None:
        raise not_found("API key")
    key.is_active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── OAuth2 client credentials ─────────────────────────────────────────────

def _oauth_error(status_code: int, error: str, description: str) -> JSONResponse:
    """RFC 6749 §5.2: the token endpoint's own error shape, not the API's."""
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if status_code == 401:
        headers["WWW-Authenticate"] = 'Basic realm="oauth"'
    return JSONResponse({"error": error, "error_description": description}, status_code=status_code, headers=headers)


def _basic_client(request: Request) -> tuple[str | None, str | None]:
    scheme, _, value = (request.headers.get("Authorization") or "").partition(" ")
    if scheme.lower() != "basic" or not value:
        return None, None
    try:
        cid, _, secret = base64.b64decode(value).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None, None
    return cid, secret


@oauth.post(
    "/token",
    response_model=ClientTokenOut,
    responses={400: {"description": "invalid_request, unsupported_grant_type or invalid_scope"},
               401: {"description": "invalid_client"}},
)
def token(
    request: Request,
    db: DB,
    grant_type: Annotated[str | None, Form(description="Always client_credentials",
                                           examples=["client_credentials"])] = None,
    client_id: Annotated[str | None, Form(examples=["cl_admin_app"])] = None,
    client_secret: Annotated[str | None, Form()] = None,
    scope: Annotated[str | None, Form(description="space-separated: read write",
                                      examples=["read write"])] = None,
):
    """Client credentials grant (RFC 6749 §4.4), sent as a form. The client
    may instead send its id and secret as HTTP Basic."""
    basic_id, basic_secret = _basic_client(request)
    client_id, client_secret = client_id or basic_id, client_secret or basic_secret
    if not grant_type:
        return _oauth_error(400, "invalid_request", "grant_type is required")
    if grant_type != "client_credentials":
        return _oauth_error(400, "unsupported_grant_type", "Only client_credentials is supported")
    if not client_id or not client_secret:
        return _oauth_error(401, "invalid_client", "client_id and client_secret are required")
    client = db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
    if client is None or not secrets.compare_digest(client.secret_hash, sha256(client_secret)):
        return _oauth_error(401, "invalid_client", "Unknown client or wrong secret")
    if not client.is_active:
        return _oauth_error(401, "invalid_client", "This client has been disabled")
    allowed = set(client.scopes.split())
    asked = set((scope or "").split()) or allowed
    if not asked <= allowed:
        return _oauth_error(400, "invalid_scope", f"This client may only ask for: {' '.join(sorted(allowed))}")
    owner = db.get(User, client.user_id)
    if owner is None or owner.is_deleted or not owner.is_active:
        return _oauth_error(401, "invalid_client", "The client's owner is no longer active")
    granted = " ".join(sorted(asked))
    raw, _ = create_client_token(owner.id, owner.role, client.client_id, granted)
    return JSONResponse(ClientTokenOut(access_token=raw, expires_in=settings.client_token_minutes * 60,
                                       scope=granted).model_dump(),
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@oauth.post("/clients", response_model=OAuthClientCreated, status_code=status.HTTP_201_CREATED)
def create_client(body: OAuthClientIn, _: AdminUser, db: DB):
    _owner(db, body.user_id)
    secret = "cs_" + secrets.token_urlsafe(24)
    client = OAuthClient(name=body.name, client_id="cl_" + secrets.token_hex(8), secret_hash=sha256(secret),
                         user_id=body.user_id, scopes=" ".join(sorted(set(body.scopes))))
    db.add(client)
    db.commit()
    return OAuthClientCreated.model_validate({**OAuthClientOut.model_validate(client).model_dump(),
                                              "client_secret": secret})


@oauth.get("/clients", response_model=list[OAuthClientOut])
def list_clients(_: AdminUser, db: DB):
    return list(db.scalars(select(OAuthClient).order_by(OAuthClient.id)))


@oauth.delete("/clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def disable_client(client_id: str, _: AdminUser, db: DB):
    client = db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
    if client is None:
        raise not_found("OAuth client")
    client.is_active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
