"""Access log JSON (1 dòng/request) + request-id. Health tách riêng (mức DEBUG, không ồn log công khai)."""

from __future__ import annotations

import logging
import re

from manager_observability.request_context import new_request_id

from api_gateway.access_log.redact import redact_path, redact_query
from api_gateway.context import RequestInfo

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-.]{8,128}$")
access_logger = logging.getLogger("gateway.access")


def mint_request_id(incoming: str | None) -> str:
    """Giữ X-Request-Id của client CHỈ khi hợp lệ (ký tự an toàn, 8..128); ngược lại sinh mới."""
    return incoming if incoming and _SAFE_ID.match(incoming) else new_request_id()


def quiet_noisy_loggers() -> None:
    """httpx/httpcore log nguyên URL (kèm query) ở INFO/DEBUG ⇒ có thể làm lộ token. Ép WARNING."""
    for name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_request(info: RequestInfo, *, method: str, path: str, query: bytes, latency_ms: float) -> None:
    level = logging.DEBUG if info.kind == "health" else (logging.WARNING if info.status >= 500 else logging.INFO)
    if not access_logger.isEnabledFor(level):
        return
    access_logger.log(level, "access", extra={
        "event": "access", "kind": info.kind, "method": method[:16],
        "path": redact_path(path), "query": redact_query(query), "status": info.status,
        "latency_ms": round(latency_ms, 2), "upstream": info.upstream,
        "upstream_status": info.upstream_status, "outcome": info.outcome, "error": info.error,
        "request_id": info.request_id, "client_ip": info.client_ip, "bytes_out": info.bytes_out,
    })
