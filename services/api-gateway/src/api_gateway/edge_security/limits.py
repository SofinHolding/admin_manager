"""Giới hạn kích thước/dạng request ở biên: header, URL, Content-Length (chống smuggling & lạm dụng)."""

from __future__ import annotations

from dataclasses import dataclass

ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_GENERIC = "REQUEST_VALIDATION_FAILED"


@dataclass(frozen=True)
class Rejection:
    status: int
    code: str
    message: str
    reason: str
    headers: tuple[tuple[bytes, bytes], ...] = ()


def check_head(scope: dict, *, max_header_bytes: int, max_header_count: int, max_url_bytes: int,
               max_body_bytes: int) -> tuple[Rejection | None, int | None]:
    """Kiểm phần đầu request. Trả (từ chối | None, Content-Length hợp lệ | None)."""
    method = scope["method"]
    if method not in ALLOWED_METHODS:
        return Rejection(405, "METHOD_NOT_ALLOWED", "Method not allowed", "method",
                         ((b"allow", b"GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"),)), None
    headers = scope["headers"]
    if len(headers) > max_header_count:
        return Rejection(431, _GENERIC, "Request header fields too large", "header_count"), None
    if sum(len(k) + len(v) + 4 for k, v in headers) > max_header_bytes:
        return Rejection(431, _GENERIC, "Request header fields too large", "header_bytes"), None
    if len(scope.get("raw_path") or b"") + len(scope.get("query_string", b"")) > max_url_bytes:
        return Rejection(414, _GENERIC, "Request URL too long", "url_length"), None

    lengths = [v for k, v in headers if k == b"content-length"]
    has_te = any(k == b"transfer-encoding" for k, _ in headers)
    if len(set(lengths)) > 1:
        return Rejection(400, _GENERIC, "Malformed request", "content_length_conflict"), None
    declared: int | None = None
    if lengths:
        raw = lengths[0].strip()
        if not raw.isdigit() or len(raw) > 18:
            return Rejection(400, _GENERIC, "Malformed request", "content_length_invalid"), None
        if has_te:  # CL + TE cùng lúc = dấu hiệu request smuggling
            return Rejection(400, _GENERIC, "Malformed request", "cl_and_te"), None
        declared = int(raw)
        if declared > max_body_bytes:
            return Rejection(413, _GENERIC, "Request body too large", "body_too_large",
                             ((b"connection", b"close"),)), declared
    return None, declared
