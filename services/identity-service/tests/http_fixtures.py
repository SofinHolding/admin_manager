"""Máy chủ HTTP THẬT (stdlib, thread) trên loopback cổng ngẫu nhiên: dùng làm receiver back-channel và nguồn feed.

Không mock: service gọi qua socket thật; test đọc những gì máy chủ này nhận được.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


class Receiver:
    """Ghi lại mọi request; hành vi điều khiển bằng `mode`: ok | fail (500) | redirect | slow."""

    def __init__(self, mode: str = "ok", redirect_to: str = "", delay: float = 0.0) -> None:
        self.mode, self.redirect_to, self.delay = mode, redirect_to, delay
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode() if length else ""
                outer.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                       "body": body, "form": {k: v[0] for k, v in parse_qs(body).items()}})
                if outer.delay:
                    time.sleep(outer.delay)
                if outer.mode == "fail":
                    self.send_response(500)
                elif outer.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", outer.redirect_to)
                else:
                    self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_GET = _handle

            def log_message(self, *args) -> None:  # im lặng
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/logout"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class FeedSource:
    """Giả lập `GET /internal/v1/feed` của Integration bằng máy chủ thật. `events` do test nạp."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.events: list[dict] = []
        self.mode = "ok"            # ok | fail | garbage
        self.calls: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                from urllib.parse import urlparse
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                outer.calls.append({"query": q, "key": self.headers.get("X-Service-Key", "")})
                if self.headers.get("X-Service-Key") != outer.key:
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if outer.mode == "fail":
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if outer.mode == "garbage":
                    body = b"khong phai json"
                else:
                    after, limit = int(q.get("after", 0)), int(q.get("limit", 100))
                    want = [t for t in q.get("types", "").split(",") if t]
                    rows = [e for e in outer.events if e["seq"] > after and (not want or e["type"] in want)]
                    page = rows[:limit]
                    body = json.dumps({"events": page, "next_cursor": page[-1]["seq"] if page else after,
                                       "has_more": len(rows) > limit,
                                       "head": max((e["seq"] for e in outer.events), default=0)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def dead_port_url() -> str:
    """URL tới cổng loopback chắc chắn không có ai nghe (bind rồi đóng)."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/logout"
