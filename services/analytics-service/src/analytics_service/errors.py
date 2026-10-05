"""Xử lý lỗi HAI KIỂU theo đường dẫn (xem `contract_negotiation` trong `infra/gateway/route-map.json`):

- đường dẫn MỚI (`/v1/analytics/*`): envelope `{ok:false, error:{code,message,details}, meta:{request_id}}`;
- alias LEGACY (`/v1/viewer/*`, `/v1/admin/*`): `{"detail": ...}` đúng hình dạng admin-server cũ.

Ghi đè handler của chassis (cùng kiểu lỗi) để chọn kiểu theo `request.url.path`. Không bao giờ lộ traceback hay
phản chiếu giá trị đầu vào.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from manager_contracts import ApiError, ErrorCode, error_envelope
from manager_observability.request_context import current_request_id
from manager_sdk import AuthError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("analytics.errors")

LEGACY_PREFIXES = ("/v1/viewer", "/v1/admin")

_STATUS_TO_CODE = {
    401: ErrorCode.AUTH_INVALID, 403: ErrorCode.SCOPE_DENIED, 404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED, 429: ErrorCode.RATE_LIMITED, 503: ErrorCode.UNAVAILABLE,
}

# Dependency (DB) tạm chết: 503 nhanh thay vì 500, và KHÔNG làm sập tiến trình.
DB_DOWN = (OSError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError, TimeoutError, asyncio.TimeoutError,
           asyncpg.UndefinedTableError)


def is_legacy_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in LEGACY_PREFIXES)


def respond(request: Request, status: int, code: str, message: str, details: dict | None = None,
            headers: dict | None = None) -> JSONResponse:
    if is_legacy_path(request.url.path):
        return JSONResponse(status_code=status, content={"detail": message}, headers=headers)
    return JSONResponse(status_code=status, headers=headers,
                        content=error_envelope(code, message, request_id=current_request_id(), details=details))


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return respond(request, exc.status_code, exc.code.value, exc.message, exc.details)

    @app.exception_handler(AuthError)
    async def _auth_error(request: Request, exc: AuthError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return respond(request, exc.status_code, exc.code, exc.message, headers=headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        default = ErrorCode.SERVER_ERROR if exc.status_code >= 500 else ErrorCode.REQUEST_VALIDATION_FAILED
        code = _STATUS_TO_CODE.get(exc.status_code, default)
        return respond(request, exc.status_code, code.value, str(exc.detail),
                       headers=dict(exc.headers) if exc.headers else None)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Chỉ vị trí + loại lỗi, KHÔNG phản chiếu giá trị đầu vào.
        fields = [{"loc": list(e.get("loc", [])), "type": e.get("type", ""), "msg": e.get("msg", "")}
                  for e in exc.errors()]
        if is_legacy_path(request.url.path):
            return JSONResponse(status_code=422, content={"detail": fields})
        return respond(request, 422, ErrorCode.REQUEST_VALIDATION_FAILED.value, "Request validation failed",
                       {"fields": [{"loc": f["loc"], "type": f["type"]} for f in fields]})

    async def _db_down(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("Database không khả dụng: %s", type(exc).__name__)
        return respond(request, 503, ErrorCode.UNAVAILABLE.value, "Dịch vụ tạm thời không khả dụng")

    for error_type in DB_DOWN:  # Starlette nhận từng lớp lỗi, không nhận tuple
        app.add_exception_handler(error_type, _db_down)

    @app.exception_handler(asyncpg.DataError)
    async def _bad_data(request: Request, exc: Exception) -> JSONResponse:
        logger.info("Dữ liệu không hợp lệ: %s", type(exc).__name__)
        return respond(request, 422, ErrorCode.REQUEST_VALIDATION_FAILED.value, "Dữ liệu không hợp lệ")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return respond(request, 500, ErrorCode.SERVER_ERROR.value, "Internal server error")
