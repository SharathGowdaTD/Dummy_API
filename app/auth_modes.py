"""One API, a different kind of login per URL.

The first path segment picks it, on the same deployment:

    https://host/...            AUTH_MODE (bearer unless set)
    https://host/basic/...      basic
    https://host/apikey/...     api_key
    https://host/oauth2/...     oauth2

so switching the URL in a client shows the same endpoints behind another
login. `PathMode` strips the prefix before routing (as root_path), and the
spec at /basic/openapi.json says `servers: [{url: /basic}]`.

Every protected endpoint depends on `deps.get_token_payload`, which takes
all four kinds of credentials and accepts only the active mode's. The
OpenAPI spec is filtered the same way, so a client reading
/openapi.json sees one login type: the deployment's.

    bearer   HTTPBearer               POST /auth/login, /refresh, /logout
    basic    HTTPBasic                every call carries email:password
    api_key  ApiKeyHeader / ApiKeyQuery / ApiKeyCookie   /api-keys to manage
    oauth2   OAuth2ClientCredentials  POST /oauth/token, /oauth/clients

Routes that only make sense in one mode (logging in is Bearer's, the
token endpoint is OAuth's) answer 404 in the others and are left out of
the spec.
"""
from contextvars import ContextVar

from fastapi import Depends, FastAPI
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.openapi.utils import get_openapi
from fastapi.security import APIKeyCookie, APIKeyHeader, APIKeyQuery, HTTPBasic, HTTPBearer, OAuth2

from app.config import get_settings
from app.errors import AppError

settings = get_settings()

KEY_HEADER, KEY_QUERY, KEY_COOKIE = "X-API-Key", "api_key", "retail_key"
SCOPES = {"read": "Read the catalog, stock, orders and reports", "write": "Create, change and delete"}

bearer = HTTPBearer(auto_error=False, scheme_name="HTTPBearer")
basic = HTTPBasic(auto_error=False, scheme_name="HTTPBasic", realm="retail")
key_header = APIKeyHeader(name=KEY_HEADER, auto_error=False, scheme_name="ApiKeyHeader")
key_query = APIKeyQuery(name=KEY_QUERY, auto_error=False, scheme_name="ApiKeyQuery")
key_cookie = APIKeyCookie(name=KEY_COOKIE, auto_error=False, scheme_name="ApiKeyCookie")
oauth2 = OAuth2(
    flows=OAuthFlows(clientCredentials=OAuthFlowClientCredentials(
        tokenUrl=f"{settings.api_prefix}/oauth/token", scopes=SCOPES)),
    scheme_name="OAuth2ClientCredentials", auto_error=False,
)

SCHEMES = {
    "bearer": {"HTTPBearer"},
    "basic": {"HTTPBasic"},
    "api_key": {"ApiKeyHeader", "ApiKeyQuery", "ApiKeyCookie"},
    "oauth2": {"OAuth2ClientCredentials"},
}
# What a 401 tells the caller to send instead.
CHALLENGE = {
    "bearer": "Bearer",
    "basic": 'Basic realm="retail"',
    "api_key": f'ApiKey header="{KEY_HEADER}"',
    "oauth2": 'Bearer error="invalid_token"',
}


# Paths (after the API prefix) that exist in one mode only.
ONLY = {"/auth/login": "bearer", "/auth/refresh": "bearer", "/auth/logout": "bearer",
        "/api-keys": "api_key", "/oauth/": "oauth2"}


PREFIXES = {"/basic": "basic", "/apikey": "api_key", "/oauth2": "oauth2", "/bearer": "bearer"}
_prefix: ContextVar[str] = ContextVar("auth_prefix", default="")


def mode() -> str:
    """This request's mode: its URL prefix's, else the deployment's."""
    return PREFIXES.get(_prefix.get(), settings.auth_mode)


class PathMode:
    """ASGI middleware: /basic/api/v1/... is /api/v1/... in basic mode."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        prefix = ""
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            prefix = next((p for p in PREFIXES if path == p or path.startswith(p + "/")), "")
            if prefix:
                # Starlette routes on the path after root_path.
                scope = dict(scope, root_path=scope.get("root_path", "") + prefix)
                if path == prefix:
                    scope["path"] = prefix + "/"
        token = _prefix.set(prefix)
        try:
            await self.app(scope, receive, send)
        finally:
            _prefix.reset(token)


def path_mode(path: str) -> str | None:
    rest = path[len(settings.api_prefix):] if path.startswith(settings.api_prefix) else path
    return next((m for prefix, m in ONLY.items() if rest == prefix or rest.startswith(prefix.rstrip("/") + "/")
                 or (prefix.endswith("/") and rest.startswith(prefix))), None)


def only_in(wanted: str):
    """A dependency: this route exists only when AUTH_MODE is `wanted`."""
    def check() -> None:
        if mode() != wanted:
            raise AppError(404, "NOT_FOUND", "Not Found")
    return Depends(check)


def install_openapi(app: FastAPI) -> None:
    """/openapi.json for the active mode only, built once per mode."""
    cache: dict[tuple[str, str], dict] = {}

    def openapi() -> dict:
        current, prefix = mode(), _prefix.get()
        if (current, prefix) in cache:
            return cache[(current, prefix)]
        spec = get_openapi(title=app.title, version=app.version, description=app.description,
                           routes=app.routes)
        spec["paths"] = {p: item for p, item in spec.get("paths", {}).items()
                         if path_mode(p) in (None, current)}
        allowed = SCHEMES[current]
        schemes = spec.get("components", {}).get("securitySchemes", {})
        for name in list(schemes):
            if name not in allowed:
                del schemes[name]
        for item in spec.get("paths", {}).values():
            for op in item.values():
                if isinstance(op, dict) and "security" in op:
                    kept = [req for req in op["security"] if set(req) <= allowed]
                    if kept:
                        op["security"] = kept
                    else:
                        del op["security"]
        spec["info"]["description"] = f"{app.description} Login: {current}."
        if prefix:
            spec["servers"] = [{"url": prefix}]
            # Absolute paths in the spec resolve against the server.
            flows = schemes.get("OAuth2ClientCredentials", {}).get("flows", {})
            for flow in flows.values():
                if str(flow.get("tokenUrl", "")).startswith("/"):
                    flow["tokenUrl"] = prefix + flow["tokenUrl"]
        cache[(current, prefix)] = spec
        return spec

    app.openapi = openapi
