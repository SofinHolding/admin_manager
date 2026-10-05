"""Dựng gateway: ASGI thuần (không BaseHTTPMiddleware) để body đi streaming, ghép các lớp theo thứ tự cố định.

Thứ tự xử lý mỗi request (mọi lỗi đều là envelope ổn định, không traceback, không lộ nội bộ):
  request-id → IP client → kiểm head (method/header/URL/Content-Length) → chuẩn hoá path + CHẶN /internal
  → health/gateway-own → định tuyến → CORS preflight → rate limit → breaker/proxy streaming.
Header bảo mật + CORS + request-id được gắn ở MỘT chỗ (wrapper `send`) nên áp cho cả phản hồi lỗi.

Gateway KHÔNG xác thực nghiệp vụ: không verify JWT, chỉ chuyển `Authorization` nguyên vẹn cho service đích.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import FastAPI
from manager_observability import create_service_app

from api_gateway import SERVICE_NAME, __version__
from api_gateway.access_log.logger import (
    log_request,
    mint_request_id,
    quiet_noisy_loggers,
)
from api_gateway.config import Settings, load_settings
from api_gateway.context import RequestInfo
from api_gateway.edge_security.cors import Cors
from api_gateway.edge_security.headers import (
    ClientAddress,
    apply_security_headers,
    sanitize_request_headers,
)
from api_gateway.edge_security.limits import check_head
from api_gateway.edge_security.ratelimit import RateLimiter
from api_gateway.responses import Send, send_error, send_json
from api_gateway.routing.pathnorm import BlockedPath, InvalidPath, normalize_path
from api_gateway.routing.proxy import Proxy, UpstreamAborted
from api_gateway.routing.table import GATEWAY_TARGET, RouteTable, load_route_table

logger = logging.getLogger("gateway")

_INNER_PATHS = frozenset({"/health/live", "/health/ready", "/health/version", "/openapi.json"})
_FORWARDING_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "forwarded")
_LOOPBACK = {"127.0.0.1", "::1"}


class Gateway:
    def __init__(self, settings: Settings, table: RouteTable,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.table = table
        self.cors = Cors(settings.cors_origins, settings.cors_allow_credentials)
        self.addresses = ClientAddress(settings.trusted_proxies)
        self.limiter = RateLimiter(strict_per_min=settings.rate_limit_strict_per_min,
                                   auth_per_min=settings.rate_limit_auth_per_min,
                                   default_per_min=settings.rate_limit_default_per_min,
                                   enabled=settings.rate_limit_enabled)
        self.proxy = Proxy(settings, table, transport)
        # Readiness CHỈ nhìn cấu hình của chính gateway (không gọi upstream): upstream chết ≠ gateway not-ready.
        self._config_ok = bool(table.routes) and all(u for u in table.upstream_urls.values())
        self._inner: FastAPI = create_service_app(
            service_name=SERVICE_NAME, service_version=__version__,
            readiness_checks={"config": self._ready_config}, docs_url=None, redoc_url=None)
        quiet_noisy_loggers()

    async def _ready_config(self) -> bool:
        return self._config_ok

    # ── ASGI ────────────────────────────────────────────────────────────────────
    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self._lifespan(receive, send)
        elif kind == "http":
            await self._http(scope, receive, send)
        else:  # websocket: gateway không có route nào dùng ws ⇒ từ chối
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})

    async def _lifespan(self, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.proxy.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _http(self, scope, receive, send) -> None:
        started = time.perf_counter()
        headers: dict[str, str] = {}
        for k, v in scope["headers"]:
            headers.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
        request_id = mint_request_id(headers.get("x-request-id"))
        client_ip, scheme = self.addresses.resolve(scope, headers)
        info = RequestInfo(request_id=request_id, client_ip=client_ip, scheme=scheme)
        origin = headers.get("origin")
        info.path = scope["path"]
        hsts = self.settings.environment == "production" or scheme == "https"
        head_only = scope["method"] == "HEAD"

        async def out(message: dict) -> None:
            if message["type"] == "http.response.start":
                info.status = message["status"]
                hdrs = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                hdrs.append((b"x-request-id", request_id.encode()))
                if self.cors.enabled and info.outcome not in ("preflight", "preflight_denied"):
                    hdrs.extend(self.cors.response_headers(origin))
                message = {**message, "headers": apply_security_headers(hdrs, hsts=hsts, path=info.path)}
            elif message["type"] == "http.response.body":
                info.bytes_out += len(message.get("body", b""))
            await send(message)

        try:
            await self._dispatch(scope, receive, out, info, headers, origin, head_only)
        except UpstreamAborted:
            raise
        except Exception as exc:  # noqa: BLE001 — lưới an toàn cuối: không bao giờ lộ traceback cho client
            info.outcome = "internal_error"
            info.error = type(exc).__name__
            if not info.status:
                await send_error(out, 500, "SERVER_ERROR", "Internal server error", request_id=request_id)
        finally:
            log_request(info, method=scope["method"], path=info.path, query=scope.get("query_string", b""),
                        latency_ms=(time.perf_counter() - started) * 1000)

    async def _dispatch(self, scope, receive, out: Send, info: RequestInfo, headers: dict[str, str],
                        origin: str | None, head_only: bool) -> None:
        s = self.settings
        rid = info.request_id
        rejection, declared = check_head(scope, max_header_bytes=s.max_header_bytes,
                                         max_header_count=s.max_header_count, max_url_bytes=s.max_url_bytes,
                                         max_body_bytes=s.max_body_bytes)

        # Chuẩn hoá path TRƯỚC mọi thứ khác: /internal luôn là 404, kể cả khi head có vấn đề khác.
        try:
            norm = normalize_path(scope.get("raw_path") or scope["path"].encode("utf-8", "replace"))
        except BlockedPath:
            info.outcome = "blocked_internal"
            await send_error(out, 404, "NOT_FOUND", "Not Found", request_id=rid, head_only=head_only)
            return
        except InvalidPath:
            info.outcome = "invalid_path"
            await send_error(out, 400, "REQUEST_VALIDATION_FAILED", "Malformed request path", request_id=rid,
                             details={"reason": "path"}, head_only=head_only)
            return
        info.path = norm.match

        if rejection is not None:
            info.outcome = rejection.reason
            await send_error(out, rejection.status, rejection.code, rejection.message, request_id=rid,
                             details={"reason": rejection.reason}, headers=rejection.headers,
                             head_only=head_only)
            return

        path = norm.match
        if path in _INNER_PATHS:
            info.kind = "health"
            await self._inner({**scope, "path": path, "raw_path": norm.forward, "root_path": ""}, receive, out)
            return
        if path == "/health/upstreams":
            info.kind = "health"
            await self._upstream_health(scope, headers, out, info)
            return

        route = self.table.resolve(path)
        if route is None:
            info.outcome = "no_route"
            await send_error(out, 404, "NOT_FOUND", "Not Found", request_id=rid, head_only=head_only)
            return
        if route.target == GATEWAY_TARGET:  # /v1/health legacy do gateway tự trả lời
            info.kind = "gateway"
            if scope["method"] not in ("GET", "HEAD"):
                await send_error(out, 405, "METHOD_NOT_ALLOWED", "Method not allowed", request_id=rid,
                                 headers=[(b"allow", b"GET, HEAD")])
                return
            await send_json(out, 200, {"ok": True, "service": "gateway"}, head_only=head_only)
            return

        if scope["method"] == "OPTIONS" and origin and "access-control-request-method" in headers:
            granted = self.cors.preflight(origin, headers["access-control-request-method"],
                                          headers.get("access-control-request-headers", ""))
            info.outcome = "preflight" if granted is not None else "preflight_denied"
            if granted is None:
                await send_error(out, 403, "SCOPE_DENIED", "CORS origin not allowed", request_id=rid)
                return
            await out({"type": "http.response.start", "status": 204,
                       "headers": [(b"content-length", b"0"), *granted]})
            await out({"type": "http.response.body", "body": b""})
            return

        retry = self.limiter.check(info.client_ip, path)
        if retry is not None:
            info.outcome = "rate_limited"
            await send_error(out, 429, "RATE_LIMITED", "Too many requests", request_id=rid,
                             headers=[(b"retry-after", str(retry).encode())], head_only=head_only)
            return

        if declared is not None and declared > s.max_body_bytes:  # phòng thủ (check_head đã từ chối)
            await send_error(out, 413, "REQUEST_VALIDATION_FAILED", "Request body too large", request_id=rid)
            return

        has_body = bool(declared) or any(k == b"transfer-encoding" for k, _ in scope["headers"])
        upstream_headers = sanitize_request_headers(list(scope["headers"]), client_ip=info.client_ip,
                                                    scheme=info.scheme, request_id=rid)
        await self.proxy.forward(scope, receive, out, route=route, forward_path=norm.forward,
                                 headers=upstream_headers, has_body=has_body, info=info)

    async def _upstream_health(self, scope, headers: dict[str, str], out: Send, info: RequestInfo) -> None:
        client = scope.get("client")
        direct_local = bool(client) and client[0] in _LOOPBACK and not any(h in headers for h in _FORWARDING_HEADERS)
        if not direct_local or scope["method"] != "GET":
            # Qua proxy/mạng ngoài ⇒ như không tồn tại (không lộ topology upstream).
            await send_error(out, 404, "NOT_FOUND", "Not Found", request_id=info.request_id)
            return

        async def probe(name: str) -> tuple[str, bool]:
            try:
                r = await self.proxy._get_client().get(self.table.upstream_urls[name] + "/health/live",
                                                       timeout=httpx.Timeout(1.0))
                return name, r.status_code == 200
            except Exception:  # noqa: BLE001
                return name, False

        results = dict(await asyncio.gather(*(probe(n) for n in self.table.upstream_names)))
        body = {"status": "ok" if all(results.values()) else "degraded",
                "upstreams": {n: {"reachable": results[n], "circuit": self.proxy.breakers[n].state}
                              for n in self.table.upstream_names}}
        await send_json(out, 200, body)


def create_app(settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> Gateway:
    settings = settings or load_settings()
    table = load_route_table(settings.route_map_path, settings.registry_path, dict(settings.upstream_overrides))
    return Gateway(settings, table, transport)
