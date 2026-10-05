"""Lỗi theo kiểu LEGACY: `{"detail": ...}` (FastAPI mặc định của admin-server), khác envelope `{ok, error}` của API mới.

Chỉ áp dụng cho các đường dẫn legacy (`LEGACY_PREFIXES`). Lỗi validation 422 giữ dạng danh sách `{type, loc, msg}` của
FastAPI nhưng BỎ `input`/`ctx` (CLAUDE.md §8: lỗi validation không phản chiếu giá trị đầu vào).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

LEGACY_PREFIXES = ("/v1/resources", "/v1/ops", "/v1/viewer/links")


def is_legacy_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in LEGACY_PREFIXES)


class LegacyError(Exception):
    def __init__(self, status_code: int, detail: Any, headers: dict[str, str] | None = None) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}


def validation_response(exc: RequestValidationError) -> JSONResponse:
    detail = [{"type": e.get("type", ""), "loc": list(e.get("loc", ())), "msg": e.get("msg", "")}
              for e in exc.errors()[:50]]
    return JSONResponse(status_code=422, content={"detail": detail})


def install(app: FastAPI) -> None:
    @app.exception_handler(LegacyError)
    async def _legacy_error(_: Request, exc: LegacyError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers or None)
