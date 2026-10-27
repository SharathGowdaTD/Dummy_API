import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app")

_DEFAULT_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    503: "SERVICE_UNAVAILABLE",
}


class AppError(Exception):
    """Domain error rendered as {"error": {code, message, ...}}."""

    def __init__(self, status_code: int, code: str, message: str, headers: dict | None = None, details=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers
        self.details = details


def not_found(entity: str) -> AppError:
    return AppError(404, f"{entity.upper().replace(' ', '_')}_NOT_FOUND", f"{entity} not found")


def _body(request: Request, code: str, message: str, details=None) -> dict:
    error = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
    if details is not None:
        error["details"] = details
    return {"error": error}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(
            _body(request, exc.code, exc.message, exc.details), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = _DEFAULT_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            _body(request, code, str(exc.detail)), status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in err["loc"] if p != "body"), "message": err["msg"]}
            for err in exc.errors()
        ]
        return JSONResponse(
            jsonable_encoder(_body(request, "VALIDATION_ERROR", "Request validation failed", details)),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(_body(request, "INTERNAL_ERROR", "An unexpected error occurred"), status_code=500)
