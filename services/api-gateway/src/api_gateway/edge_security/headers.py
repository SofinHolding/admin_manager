"""Làm sạch header request/response, header bảo mật, xác định IP client.

Nguyên tắc: gateway là biên tin cậy duy nhất. Mọi header "định danh/ủy quyền nội bộ" do client gửi đều bị loại
để upstream không bao giờ tin được giá trị giả mạo (X-Real-IP, X-Forwarded-*, X-Service-Key, ...).
"""

from __future__ import annotations

import ipaddress

Headerlist = list[tuple[bytes, bytes]]

HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
    "te", "trailer", "transfer-encoding", "upgrade",
})
# Header client KHÔNG được phép chuyển vào upstream (gateway tự đặt lại nếu cần).
_STRIP_EXACT = frozenset({
    "host", "expect", "forwarded", "x-real-ip", "x-service-key", "x-request-id",
    "x-http-method-override", "x-method-override", "x-original-url", "x-rewrite-url",
    "x-original-uri", "x-original-method",
})
_STRIP_PREFIX = ("x-forwarded-", "x-internal-", "x-gateway-")
_STRIP_RESPONSE_EXACT = frozenset({"server", "x-powered-by", "x-request-id", "x-aspnet-version", "via"})


def _connection_tokens(headers: Headerlist) -> set[str]:
    tokens: set[str] = set()
    for k, v in headers:
        if k.lower() == b"connection":
            tokens |= {t.strip().lower() for t in v.decode("latin-1").split(",") if t.strip()}
    return tokens


def sanitize_request_headers(raw: Headerlist, *, client_ip: str, scheme: str, request_id: str) -> Headerlist:
    drop_named = _connection_tokens(raw)
    out: Headerlist = []
    for key, value in raw:
        name = key.decode("latin-1").lower()
        if name in HOP_BY_HOP or name in _STRIP_EXACT or name in drop_named:
            continue
        if name.startswith(_STRIP_PREFIX):
            continue
        out.append((name.encode("latin-1"), value))
    out.append((b"x-real-ip", client_ip.encode()))
    out.append((b"x-forwarded-for", client_ip.encode()))
    out.append((b"x-forwarded-proto", scheme.encode()))
    out.append((b"x-request-id", request_id.encode()))
    if not any(k == b"accept-encoding" for k, _ in out):
        # Không để httpx tự thêm gzip rồi chuyển byte nén cho client không yêu cầu.
        out.append((b"accept-encoding", b"identity"))
    return out


def sanitize_response_headers(raw: list[tuple[bytes, bytes]]) -> Headerlist:
    drop_named = _connection_tokens(list(raw))
    out: Headerlist = []
    for key, value in raw:
        name = key.decode("latin-1").lower()
        if name in HOP_BY_HOP or name in _STRIP_RESPONSE_EXACT or name in drop_named:
            continue
        if name.startswith("access-control-"):  # gateway là nguồn sự thật duy nhất của CORS
            continue
        out.append((name.encode("latin-1"), value))
    return out


# ── Header bảo mật ──────────────────────────────────────────────────────────────

_NO_STORE_PREFIXES = ("/v1/auth", "/v1/accounts", "/v1/admin", "/v1/reward/auth", "/oauth")


def wants_no_store(path: str) -> bool:
    p = path.lower()
    return any(p == pre or p.startswith(pre + "/") for pre in _NO_STORE_PREFIXES)


def apply_security_headers(headers: Headerlist, *, hsts: bool, path: str) -> Headerlist:
    """Đặt (ghi đè) header bảo mật chuẩn cho mọi phản hồi, kể cả lỗi do gateway sinh."""
    override = {
        b"x-content-type-options": b"nosniff",
        b"x-frame-options": b"DENY",
        b"referrer-policy": b"no-referrer",
        b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",
        b"cross-origin-resource-policy": b"same-site",
    }
    if hsts:
        override[b"strict-transport-security"] = b"max-age=31536000; includeSubDomains"
    names = {k.lower() for k, _ in headers}
    no_store = wants_no_store(path)
    out = [(k, v) for k, v in headers if k.lower() not in override
           and not (k.lower() in (b"cache-control", b"pragma") and no_store)]
    out.extend(override.items())
    if no_store:
        out.append((b"cache-control", b"no-store"))
        out.append((b"pragma", b"no-cache"))
    elif b"cache-control" not in names:
        out.append((b"cache-control", b"no-store"))
    return out


# ── IP client ───────────────────────────────────────────────────────────────────

class ClientAddress:
    """IP client = địa chỉ peer TCP. Chỉ khi peer thuộc proxy tin cậy mới đọc X-Real-IP do proxy đó đặt."""

    def __init__(self, trusted: tuple[str, ...]) -> None:
        self._nets = [ipaddress.ip_network(t, strict=False) for t in trusted]

    def _is_trusted(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in n for n in self._nets)

    @staticmethod
    def _valid(ip: str) -> str | None:
        try:
            return str(ipaddress.ip_address(ip.strip()))
        except ValueError:
            return None

    def resolve(self, scope: dict, headers: dict[str, str]) -> tuple[str, str]:
        client = scope.get("client")
        peer = client[0] if client else "unknown"
        scheme = scope.get("scheme", "http")
        if peer != "unknown" and self._is_trusted(peer):
            real = self._valid(headers.get("x-real-ip", ""))
            if real is None:
                xff = headers.get("x-forwarded-for", "")
                for part in reversed([p for p in xff.split(",") if p.strip()]):
                    real = self._valid(part)
                    if real:
                        break
            if headers.get("x-forwarded-proto", "").lower() in {"http", "https"}:
                scheme = headers["x-forwarded-proto"].lower()
            return real or peer, scheme
        return peer, scheme
