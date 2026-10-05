"""Client đọc feed / canonical export nội bộ (docs/api/internal-feed-contract-v1.md).

Có timeout, retry với backoff mũ + jitter (giới hạn số lần), và KHÔNG bao giờ đưa service key vào exception/log.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Iterator

import httpx

logger = logging.getLogger("manager_sdk.feed")


class FeedError(Exception):
    """Nguồn feed không dùng được sau khi đã thử lại (consumer giữ con trỏ và thử lại sau, không crash)."""


def backoff_delays(attempts: int, base: float = 0.5, cap: float = 30.0, jitter: float = 0.2) -> Iterator[float]:
    for i in range(attempts):
        delay = min(cap, base * (2 ** i))
        yield delay * (1 + random.uniform(-jitter, jitter))


class FeedClient:
    def __init__(self, base_url: str, service_key: str, *, timeout: float = 5.0, retries: int = 3,
                 http_client: httpx.AsyncClient | None = None, sleep=asyncio.sleep, backoff_base: float = 0.5) -> None:
        self._base = base_url.rstrip("/")
        self._key = service_key
        self._timeout = timeout
        self._retries = retries
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._sleep = sleep
        self._backoff_base = backoff_base

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict) -> dict:
        last = "chưa thử"
        delays = list(backoff_delays(self._retries, base=self._backoff_base))
        for attempt in range(self._retries + 1):
            try:
                r = await self._http.get(self._base + path, params=params, headers={"X-Service-Key": self._key},
                                         timeout=self._timeout)
                if r.status_code in (401, 403):  # sai khoá: thử lại vô ích
                    raise FeedError(f"nguồn từ chối khoá (HTTP {r.status_code})")
                if r.status_code >= 500 or r.status_code == 429:
                    last = f"HTTP {r.status_code}"
                else:
                    r.raise_for_status()
                    return r.json()
            except FeedError:
                raise
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = type(exc).__name__  # không đưa URL/header vào thông điệp
            except httpx.HTTPStatusError as exc:
                raise FeedError(f"HTTP {exc.response.status_code}") from None
            if attempt < self._retries:
                await self._sleep(delays[attempt])
        raise FeedError(f"nguồn feed không khả dụng sau {self._retries + 1} lần thử ({last})")

    async def feed(self, after: int = 0, limit: int = 100, types: list[str] | None = None) -> dict:
        return await self._get("/internal/v1/feed", {"after": after, "limit": limit,
                                                    "types": ",".join(types or [])})

    async def canonical(self, kind: str, after_revision: int = 0, limit: int = 500) -> dict:
        return await self._get(f"/internal/v1/canonical/{kind}", {"after_revision": after_revision, "limit": limit})

    async def canonical_summary(self) -> dict:
        return await self._get("/internal/v1/canonical/summary", {})
