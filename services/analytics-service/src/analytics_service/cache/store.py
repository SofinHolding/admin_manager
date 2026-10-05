"""Bọc Redis asyncio — tự suy biến khi Redis không sẵn sàng hoặc chết giữa chừng (mọi thao tác nuốt lỗi)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("analytics.cache")

PREFIX = "analytics"
SCHEMA = "v1"          # tăng khi hình dạng kết quả đổi -> mọi khoá cũ vô hiệu


def build_key(seal: int, scope: str, endpoint: str, filter_hash: str) -> str:
    return f"{PREFIX}:{SCHEMA}:{seal}:{scope}:{endpoint}:{filter_hash}"


class ResultCache:
    """`url` rỗng => tắt hoàn toàn. Lỗi Redis => mạch hở `retry_after` giây (không làm chậm mỗi request)."""

    def __init__(self, url: str = "", *, ttl: int = 300, socket_timeout: float = 0.5, retry_after: float = 3.0,
                 clock=time.monotonic) -> None:
        self.url = url
        self.ttl = ttl
        self._timeout = socket_timeout
        self._retry_after = retry_after
        self._clock = clock
        self._r: Any = None
        self._down_until = 0.0
        self.hits = 0
        self.misses = 0
        self.errors = 0

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def _client(self):
        if not self.url:
            return None
        if self._clock() < self._down_until:
            return None
        if self._r is None:
            try:
                import redis.asyncio as aioredis
                self._r = aioredis.from_url(self.url, decode_responses=True, socket_connect_timeout=self._timeout,
                                            socket_timeout=self._timeout)
            except Exception as exc:  # noqa: BLE001
                self._fail(exc)
                return None
        return self._r

    def _fail(self, exc: Exception) -> None:
        self.errors += 1
        self._down_until = self._clock() + self._retry_after
        logger.warning("Redis không dùng được, đọc thẳng PostgreSQL: %s", type(exc).__name__)

    async def close(self) -> None:
        r, self._r = self._r, None
        if r is not None:
            try:
                await r.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def get(self, key: str) -> Any | None:
        r = self._client()
        if r is None:
            return None
        try:
            raw = await r.get(key)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return None
        if raw is None:
            self.misses += 1
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            self.misses += 1
            return None
        self.hits += 1
        return value

    async def set(self, key: str, value: Any) -> None:
        r = self._client()
        if r is None:
            return
        try:
            await r.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=self.ttl)   # LUÔN có TTL
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    async def flush(self) -> int:
        """Xoá mọi khoá `analytics:*` (SCAN theo lô + UNLINK; không dùng KEYS). Trả số khoá đã xoá."""
        r = self._client()
        if r is None:
            return 0
        try:
            total = 0
            batch: list[str] = []
            async for key in r.scan_iter(match=f"{PREFIX}:*", count=500):
                batch.append(key)
                if len(batch) >= 500:
                    await r.unlink(*batch)
                    total += len(batch)
                    batch = []
            if batch:
                await r.unlink(*batch)
                total += len(batch)
            return total
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return 0

    async def available(self) -> bool:
        r = self._client()
        if r is None:
            return False
        try:
            return bool(await r.ping())
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return False
