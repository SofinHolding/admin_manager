"""CORS allowlist: so khớp CHÍNH XÁC origin, không wildcard, preflight đúng chuẩn."""

from __future__ import annotations

ALLOWED_METHODS = "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
ALLOWED_HEADERS = ("authorization", "content-type", "accept", "x-request-id", "x-contract-version",
                   "idempotency-key", "x-requested-with")
EXPOSED_HEADERS = "X-Request-Id, Retry-After"
_METHODS = {m.strip() for m in ALLOWED_METHODS.split(",")}


class Cors:
    def __init__(self, origins: tuple[str, ...], allow_credentials: bool) -> None:
        self._origins = frozenset(o.lower() for o in origins)
        self._credentials = allow_credentials

    @property
    def enabled(self) -> bool:
        return bool(self._origins)

    def is_allowed(self, origin: str | None) -> bool:
        return bool(origin) and origin.lower() in self._origins  # type: ignore[union-attr]

    def response_headers(self, origin: str | None) -> list[tuple[bytes, bytes]]:
        """Header CORS cho phản hồi thường. Origin lạ ⇒ không có header CORS (chỉ `Vary: Origin`)."""
        out = [(b"vary", b"Origin")]
        if self.is_allowed(origin):
            out.append((b"access-control-allow-origin", origin.encode("latin-1")))  # type: ignore[union-attr]
            out.append((b"access-control-expose-headers", EXPOSED_HEADERS.encode()))
            if self._credentials:
                out.append((b"access-control-allow-credentials", b"true"))
        return out

    def preflight(self, origin: str | None, request_method: str,
                  request_headers: str) -> list[tuple[bytes, bytes]] | None:
        """Header cho phản hồi preflight; None nếu origin/method/header không được phép."""
        if not self.is_allowed(origin) or request_method.upper() not in _METHODS:
            return None
        asked = [h.strip().lower() for h in request_headers.split(",") if h.strip()]
        if any(h not in ALLOWED_HEADERS for h in asked):
            return None
        out = self.response_headers(origin)
        out += [(b"access-control-allow-methods", ALLOWED_METHODS.encode()),
                (b"access-control-allow-headers", ", ".join(ALLOWED_HEADERS).encode()),
                (b"access-control-max-age", b"600")]
        return out
