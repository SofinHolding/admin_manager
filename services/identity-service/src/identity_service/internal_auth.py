"""Xác thực service-to-service cho `/internal/v1/*` bằng `X-Service-Key` (docs/api/service-auth-contract-v1.md §5)."""

from __future__ import annotations

import hmac

from fastapi import Header, Request

from identity_service.errors import DomainError


def reader_for_key(settings, presented: str) -> str | None:
    """Trả tên bên đọc nếu khoá khớp (so sánh hằng-thời-gian, duyệt hết để không lộ thứ tự)."""
    matched = None
    for reader, key in settings.feed_keys:
        if hmac.compare_digest(presented.encode(), key.encode()):
            matched = reader
    return matched


async def require_service_key(request: Request, x_service_key: str = Header("")) -> str:
    settings = request.app.state.container.settings
    if not x_service_key or not settings.feed_keys:
        raise DomainError(401, "Thiếu hoặc sai service key")
    reader = reader_for_key(settings, x_service_key)
    if reader is None:
        raise DomainError(401, "Thiếu hoặc sai service key")
    return reader
