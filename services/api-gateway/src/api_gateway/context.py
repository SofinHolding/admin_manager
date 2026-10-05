"""Thông tin theo từng request, điền dần qua các lớp rồi ghi vào access log."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RequestInfo:
    request_id: str
    client_ip: str = "unknown"
    scheme: str = "http"
    upstream: str | None = None
    upstream_status: int | None = None
    status: int = 0
    outcome: str = "ok"
    error: str | None = None
    bytes_out: int = 0
    path: str = "/"
    kind: str = "proxy"   # proxy | health | gateway
