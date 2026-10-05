"""Hạ tầng test: upstream THẬT (ASGI app echo chạy bằng uvicorn, cổng ngẫu nhiên) + gateway thật.

Không mock logic của gateway. Gateway được dùng theo hai cách:
- in-process qua `httpx.ASGITransport` (nhanh, kiểm header/route/CORS...),
- qua TCP thật bằng uvicorn (`serve`) khi cần byte thô (đường dẫn `..`, `//`, slowloris, header khổng lồ, streaming).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import select
import socket
import threading
import time
from collections.abc import Callable

import httpx
import pytest
import uvicorn
from api_gateway.app import Gateway, create_app
from api_gateway.config import load_settings
from manager_observability.logging_setup import JsonFormatter

SERVICES = ("identity", "integration", "analytics", "reward")


# ── Server uvicorn trong thread ─────────────────────────────────────────────────

class ServerThread:
    def __init__(self, app, **config) -> None:
        cfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False,
                             proxy_headers=False, server_header=False, lifespan="on", **config)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> ServerThread:
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("uvicorn không khởi động")
            time.sleep(0.01)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)


def free_port() -> int:
    """Cổng không ai nghe (đóng ngay sau khi bind) — mô phỏng upstream chết."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Upstream echo ───────────────────────────────────────────────────────────────

class Echo:
    """Upstream giả nhưng là app ASGI thật: ghi lại mọi request nhận được và có vài endpoint đặc biệt.

    Endpoint đặc biệt (đặt dưới tiền tố đã được định tuyến, vd /v1/resources/...):
      .../__big?mb=N      trả N MiB, mỗi chunk 1 MiB (kiểm streaming response)
      .../__slow?s=X      ngủ X giây rồi trả (kiểm timeout)
      .../__status?code=N trả status N
      .../__headers       trả kèm header nguy hiểm/hop-by-hop/CORS của upstream
      .../__abort         gửi một phần rồi đứt kết nối
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict] = []

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            while True:
                m = await receive()
                if m["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        path = scope["path"]
        total = chunks = max_chunk = 0
        digest = hashlib.sha256()
        while True:
            m = await receive()
            if m["type"] == "http.disconnect":
                return
            b = m.get("body", b"")
            total += len(b)
            chunks += 1 if b else 0
            max_chunk = max(max_chunk, len(b))
            digest.update(b)
            if not m.get("more_body", False):
                break
        query = scope.get("query_string", b"").decode()
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        rec = {"service": self.name, "method": scope["method"], "path": path,
               "raw_path": (scope.get("raw_path") or b"").decode("latin-1"), "query": query,
               "headers": {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]},
               "header_pairs": [[k.decode("latin-1").lower(), v.decode("latin-1")] for k, v in scope["headers"]],
               "body_len": total, "body_sha": digest.hexdigest(), "chunks": chunks, "max_chunk": max_chunk}
        self.calls.append(rec)

        if path.endswith("/__big"):
            mb = int(q.get("mb", "1"))
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/octet-stream"),
                                    (b"content-length", str(mb * 1024 * 1024).encode())]})
            block = b"x" * (1024 * 1024)
            for i in range(mb):
                await send({"type": "http.response.body", "body": block, "more_body": i < mb - 1})
            return
        if path.endswith("/__slow"):
            await asyncio.sleep(float(q.get("s", "1")))
        status = int(q.get("code", "200")) if path.endswith("/__status") else 200
        headers = [(b"content-type", b"application/json")]
        if path.endswith("/__headers"):
            headers += [(b"server", b"upstream/9.9"), (b"x-powered-by", b"secret-framework"),
                        (b"access-control-allow-origin", b"*"), (b"access-control-allow-credentials", b"true"),
                        (b"x-request-id", b"upstream-chosen-id"), (b"keep-alive", b"timeout=5"),
                        (b"x-upstream-extra", b"kept"), (b"cache-control", b"public, max-age=300")]
        if path.endswith("/__abort"):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain"), (b"content-length", b"1000")]})
            await send({"type": "http.response.body", "body": b"partial", "more_body": True})
            raise RuntimeError("upstream died mid-stream")
        body = json.dumps(rec).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": headers + [(b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class Upstreams:
    def __init__(self) -> None:
        self.echo = {n: Echo(n) for n in SERVICES}
        self.servers = {n: ServerThread(e).start() for n, e in self.echo.items()}

    def env(self) -> dict[str, str]:
        return {f"GATEWAY_UPSTREAM_{n.upper()}": s.url for n, s in self.servers.items()}

    def clear(self) -> None:
        for e in self.echo.values():
            e.calls.clear()

    def all_calls(self) -> list[dict]:
        return [c for e in self.echo.values() for c in e.calls]

    def stop(self) -> None:
        for s in self.servers.values():
            s.stop()


@pytest.fixture(scope="session")
def upstreams():
    u = Upstreams()
    yield u
    u.stop()


@pytest.fixture
def ups(upstreams):
    upstreams.clear()
    return upstreams


@pytest.fixture
def make_gateway(ups):
    """Dựng gateway với upstream echo thật; `env` ghi đè cấu hình; `dead` = danh sách upstream trỏ vào cổng chết."""
    created: list[Gateway] = []

    def factory(*, dead: tuple[str, ...] = (), **env: str) -> Gateway:
        full = {"GATEWAY_RATE_LIMIT_ENABLED": "false", **ups.env()}
        for name in dead:
            full[f"GATEWAY_UPSTREAM_{name.upper()}"] = f"http://127.0.0.1:{free_port()}"
        full.update(env)
        gw = create_app(load_settings(full))
        created.append(gw)
        return gw

    yield factory


def client_for(gw: Gateway, ip: str = "203.0.113.9", **kw) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=gw, client=(ip, 5555))
    return httpx.AsyncClient(transport=transport, base_url="http://gateway.test", **kw)


@pytest.fixture
def serve():
    """Chạy gateway thành server TCP thật; trả cổng. Dùng cho test byte thô."""
    started: list[ServerThread] = []

    def factory(gw: Gateway, **config) -> ServerThread:
        s = ServerThread(gw, **config).start()
        started.append(s)
        return s

    yield factory
    for s in started:
        s.stop()


# ── Client socket thô ───────────────────────────────────────────────────────────

def raw_http(port: int, request: bytes, *, read_timeout: float = 5.0, chunks: list[bytes] | None = None,
             chunk_delay: float = 0.0) -> tuple[int, dict[str, str], bytes]:
    """Gửi byte thô, đọc phản hồi đầu tiên. Trả (status, headers_lower, body). status=0 nếu server đóng không trả gì."""
    with socket.create_connection(("127.0.0.1", port), timeout=read_timeout) as s:
        s.sendall(request)
        for c in chunks or []:
            time.sleep(chunk_delay)
            if select.select([s], [], [], 0)[0]:  # server đã trả lời/đóng ⇒ đừng gửi tiếp (tránh RST xoá phản hồi)
                break
            try:
                s.sendall(c)
            except OSError:
                break
        buf = b""
        s.settimeout(read_timeout)
        try:
            while b"\r\n\r\n" not in buf:
                data = s.recv(65536)
                if not data:
                    break
                buf += data
            head, _, rest = buf.partition(b"\r\n\r\n")
            if not head:
                return 0, {}, b""
            lines = head.decode("latin-1").split("\r\n")
            status = int(lines[0].split()[1])
            hdrs = {}
            for ln in lines[1:]:
                k, _, v = ln.partition(":")
                hdrs[k.strip().lower()] = v.strip()
            want = int(hdrs.get("content-length", "0"))
            while len(rest) < want:
                data = s.recv(65536)
                if not data:
                    break
                rest += data
            return status, hdrs, rest
        except (TimeoutError, ConnectionResetError):
            return 0, {}, b""


def get(port: int, target: str, extra: str = "") -> tuple[int, dict[str, str], bytes]:
    req = f"GET {target} HTTP/1.1\r\nHost: gw\r\nConnection: close\r\n{extra}\r\n".encode("latin-1")
    return raw_http(port, req)


# ── Bắt log (để quét secret) ────────────────────────────────────────────────────

class LogCapture:
    def __init__(self) -> None:
        import io
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(JsonFormatter("api-gateway", "test"))
        self.handler.setLevel(logging.DEBUG)

    @property
    def text(self) -> str:
        return self.stream.getvalue()

    def lines(self) -> list[dict]:
        return [json.loads(ln) for ln in self.text.splitlines() if ln.strip()]


@pytest.fixture
def logs():
    cap = LogCapture()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(cap.handler)
    root.setLevel(logging.DEBUG)
    yield cap
    root.removeHandler(cap.handler)
    root.setLevel(old_level)


def run(coro_fn: Callable):
    return asyncio.run(coro_fn())
