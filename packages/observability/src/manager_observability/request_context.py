"""Request-id: nhận `X-Request-Id` hợp lệ từ phía trên hoặc tự sinh; luôn trả lại trong header."""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HEADER = "X-Request-Id"
_SAFE = re.compile(r"^[A-Za-z0-9_\-.]{8,128}$")
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def current_request_id() -> str:
    return _request_id.get()


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(HEADER, "")
        rid = incoming if _SAFE.match(incoming) else new_request_id()  # không tin giá trị lạ
        token = _request_id.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)
        response.headers[HEADER] = rid
        return response
