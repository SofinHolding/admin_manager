"""Kiểm `X-Service-Key` cho `/internal/v1/*` (docs/api/service-auth-contract-v1.md §5)."""

from __future__ import annotations

import hmac
from collections.abc import Mapping

from fastapi import Header

from manager_sdk.errors import AuthError


class ServiceKeyAuth:
    """Dùng làm dependency FastAPI: `Depends(ServiceKeyAuth({"analytics": key, ...}))` → trả tên bên đọc.

    So sánh hằng-thời-gian với MỌI khoá (không dừng sớm) để không lộ khoá nào gần đúng. Không khoá nào được cấu hình ⇒ từ chối tất cả."""

    def __init__(self, keys: Mapping[str, str]) -> None:
        self._keys = {reader: key for reader, key in keys.items() if key}

    def match(self, presented: str) -> str | None:
        matched = None
        for reader, key in self._keys.items():
            if hmac.compare_digest(presented.encode(), key.encode()):
                matched = reader
        return matched

    async def __call__(self, x_service_key: str = Header("")) -> str:
        reader = self.match(x_service_key) if x_service_key else None
        if reader is None:
            raise AuthError("Thiếu hoặc sai service key")
        return reader
