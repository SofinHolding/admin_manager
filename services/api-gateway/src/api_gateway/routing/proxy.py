"""Reverse proxy STREAMING (httpx): thân request/response đi theo từng chunk, không nạp cả body vào RAM.

- Body request: đọc từng message ASGI, đếm byte ngay khi đọc ⇒ vượt giới hạn là dừng NGAY (kể cả chunked, không
  Content-Length) và trả 413; chờ client quá `body_timeout_s` giữa hai chunk ⇒ 408 (chống slowloris thân request).
- Upstream chết/timeout ⇒ 502/504 `UNAVAILABLE` nhanh (timeout kết nối ngắn) + circuit breaker theo upstream.
- Không retry (tránh nhân đôi thao tác ghi), không theo redirect, không đọc proxy từ biến môi trường.
"""

from __future__ import annotations

import asyncio
import functools
import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from api_gateway.config import Settings
from api_gateway.context import RequestInfo
from api_gateway.edge_security.headers import sanitize_response_headers
from api_gateway.responses import Send, send_error
from api_gateway.routing.breaker import CircuitBreaker
from api_gateway.routing.table import Route, RouteTable


@functools.lru_cache(maxsize=1)
def _tls_context() -> ssl.SSLContext:
    """Nạp CA hệ thống một lần/tiến trình (tốn ~0.3s mỗi lần dựng client)."""
    return httpx.create_ssl_context()


class UpstreamAborted(Exception):
    """Upstream đứt giữa chừng khi đã gửi một phần phản hồi: buộc đóng kết nối để client không thấy cụt-như-hoàn-chỉnh."""


class _BodyAbort(Exception):
    pass


@dataclass
class _BodyState:
    reason: str | None = None
    received: int = 0


class Proxy:
    def __init__(self, settings: Settings, table: RouteTable,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._s = settings
        self._table = table
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self.breakers = {name: CircuitBreaker(settings.breaker_threshold, settings.breaker_cooldown_s)
                         for name in table.upstream_names}

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(connect=self._s.connect_timeout_s, read=self._s.read_timeout_s,
                                    write=self._s.read_timeout_s, pool=self._s.connect_timeout_s)
            limits = httpx.Limits(max_connections=200, max_keepalive_connections=50, keepalive_expiry=15)
            self._client = httpx.AsyncClient(transport=self._transport, timeout=timeout, limits=limits,
                                             follow_redirects=False, trust_env=False,
                                             verify=_tls_context())
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _body(self, receive, state: _BodyState) -> AsyncIterator[bytes]:
        limit = self._s.max_body_bytes
        while True:
            try:
                message = await asyncio.wait_for(receive(), self._s.body_timeout_s)
            except asyncio.TimeoutError:
                state.reason = "body_timeout"
                raise _BodyAbort from None
            if message["type"] == "http.disconnect":
                state.reason = "client_disconnect"
                raise _BodyAbort
            chunk = message.get("body", b"")
            state.received += len(chunk)
            if state.received > limit:
                state.reason = "body_too_large"
                raise _BodyAbort
            if chunk:
                yield chunk
            if not message.get("more_body", False):
                return

    async def forward(self, scope: dict, receive, send: Send, *, route: Route, forward_path: bytes,
                      headers: list[tuple[bytes, bytes]], has_body: bool, info: RequestInfo) -> None:
        name = route.upstream
        assert name is not None
        info.upstream = name
        breaker = self.breakers[name]
        if not breaker.allow():
            info.outcome = "circuit_open"
            await send_error(send, 503, "UNAVAILABLE", "Service temporarily unavailable",
                             request_id=info.request_id, details={"upstream": name},
                             headers=[(b"retry-after", str(breaker.retry_after()).encode())])
            return

        state = _BodyState()
        content = self._body(receive, state) if has_body else None
        query = scope.get("query_string", b"")
        raw_target = forward_path + (b"?" + query if query else b"")
        url = httpx.URL(self._table.upstream_urls[name]).copy_with(raw_path=raw_target)
        client = self._get_client()
        request = client.build_request(scope["method"], url, headers=headers, content=content)

        try:
            response = await client.send(request, stream=True)
        except BaseException as exc:
            if state.reason == "body_too_large":
                info.outcome = "body_too_large"
                await send_error(send, 413, "REQUEST_VALIDATION_FAILED", "Request body too large",
                                 request_id=info.request_id, details={"reason": "body_too_large"},
                                 headers=[(b"connection", b"close")])
                return
            if state.reason == "body_timeout":
                info.outcome = "body_timeout"
                await send_error(send, 408, "REQUEST_VALIDATION_FAILED", "Request body timeout",
                                 request_id=info.request_id, details={"reason": "body_timeout"},
                                 headers=[(b"connection", b"close")])
                return
            if state.reason == "client_disconnect":
                info.outcome = "client_disconnect"
                info.status = 499
                return
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            if not isinstance(exc, Exception):
                raise
            timed_out = isinstance(exc, httpx.TimeoutException)
            breaker.record_failure()
            info.outcome = "upstream_timeout" if timed_out else "upstream_unreachable"
            info.error = type(exc).__name__
            await send_error(send, 504 if timed_out else 502, "UNAVAILABLE",
                             "Upstream timed out" if timed_out else "Upstream unavailable",
                             request_id=info.request_id, details={"upstream": name})
            return

        info.upstream_status = response.status_code
        if response.status_code in (502, 504):
            breaker.record_failure()
        else:
            breaker.record_success()
        try:
            await send({"type": "http.response.start", "status": response.status_code,
                        "headers": sanitize_response_headers(response.headers.raw)})
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except httpx.HTTPError as exc:
            breaker.record_failure()
            info.outcome = "upstream_aborted"
            info.error = type(exc).__name__
            raise UpstreamAborted(name) from None
        finally:
            await response.aclose()
