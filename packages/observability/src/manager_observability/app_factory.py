"""Dựng FastAPI với chassis chuẩn: request-id, health, envelope lỗi ổn định."""

from __future__ import annotations

import logging

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
                       **fastapi_kwargs) -> FastAPI:
    app = FastAPI(title=service_name, version=service_version, **fastapi_kwargs)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(make_health_router(service_name, service_version, CONTRACT_VERSION,
                                          readiness_checks))

    def _respond(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
        return JSONResponse(status_code=status,
                            content=error_envelope(code, message, request_id=current_request_id(),
                                                   details=details))

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _respond(exc.status_code, exc.code.value, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        default = ErrorCode.SERVER_ERROR if exc.status_code >= 500 else ErrorCode.REQUEST_VALIDATION_FAILED
        code = _STATUS_TO_CODE.get(exc.status_code, default)
        return _respond(exc.status_code, code.value, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Chỉ trả vị trí và loại lỗi, KHÔNG phản chiếu giá trị đầu vào (có thể là mật khẩu).
        fields = [{"loc": list(e.get("loc", [])), "type": e.get("type", "")} for e in exc.errors()]
        return _respond(422, ErrorCode.REQUEST_VALIDATION_FAILED.value, "Request validation failed",
                        {"fields": fields})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return _respond(500, ErrorCode.SERVER_ERROR.value, "Internal server error")

    return app
