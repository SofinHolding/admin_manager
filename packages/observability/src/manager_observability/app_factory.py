"""Dựng FastAPI với chassis chuẩn: request-id, health, envelope lỗi ổn định.

`error_style`:
  - "envelope" (mặc định): `{ok:false, error:{code,message,details}, meta:{request_id}}` — contract mới.
  - "legacy": `{"detail": ...}` đúng hình dạng FastAPI cũ, để frontend/Tool cũ không vỡ trong giai đoạn Strangler.
    Chế độ này KHÔNG phản chiếu giá trị đầu vào khi lỗi validation (không lộ mật khẩu).
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from manager_contracts import CONTRACT_VERSION, ApiError, ErrorCode, error_envelope
from starlette.exceptions import HTTPException as StarletteHTTPException

from manager_observability.health import ReadinessCheck, make_health_router
from manager_observability.request_context import RequestIdMiddleware, current_request_id

logger = logging.getLogger("chassis")

_STATUS_TO_CODE = {
    401: ErrorCode.AUTH_INVALID,
    403: ErrorCode.SCOPE_DENIED,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.UNAVAILABLE,
}


def create_service_app(*, service_name: str, service_version: str,
                       readiness_checks: dict[str, ReadinessCheck] | None = None,
                       error_style: Literal["envelope", "legacy"] = "envelope",
                       **fastapi_kwargs) -> FastAPI:
    app = FastAPI(title=service_name, version=service_version, **fastapi_kwargs)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(make_health_router(service_name, service_version, CONTRACT_VERSION,
                                          readiness_checks))
    legacy = error_style == "legacy"

    def _respond(status: int, code: str, message: str, details: dict | None = None,
                 headers: dict | None = None) -> JSONResponse:
        if legacy:
            return JSONResponse(status_code=status, content={"detail": message}, headers=headers)
        return JSONResponse(status_code=status, headers=headers,
                            content=error_envelope(code, message, request_id=current_request_id(),
                                                   details=details))

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _respond(exc.status_code, exc.code.value, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        default = ErrorCode.SERVER_ERROR if exc.status_code >= 500 else ErrorCode.REQUEST_VALIDATION_FAILED
        code = _STATUS_TO_CODE.get(exc.status_code, default)
        return _respond(exc.status_code, code.value, str(exc.detail),
                        headers=dict(exc.headers) if exc.headers else None)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Chỉ trả vị trí và loại lỗi, KHÔNG phản chiếu giá trị đầu vào (có thể là mật khẩu).
        if legacy:
            items = [{"type": e.get("type", ""), "loc": list(e.get("loc", [])), "msg": e.get("msg", "")}
                     for e in exc.errors()]
            return JSONResponse(status_code=422, content={"detail": items})
        fields = [{"loc": list(e.get("loc", [])), "type": e.get("type", "")} for e in exc.errors()]
        return _respond(422, ErrorCode.REQUEST_VALIDATION_FAILED.value, "Request validation failed",
                        {"fields": fields})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return _respond(500, ErrorCode.SERVER_ERROR.value, "Internal server error")

    return app
